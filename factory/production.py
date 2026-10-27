"""Resumable autonomous short-drama production using the installed local models.

Public API: generate_style_previews(...), generate_project(...). Both checkpoint
whole top-level patches; all production budget counters are cumulative.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import subprocess
import time
from contextlib import nullcontext
from pathlib import Path

from studio.core import ROOT, fingerprint, media_receipt, read_json, sha256, within, write_json
from studio.graphs import flux_image, wan_video, h3_video
from .image_generation import image_request
from . import team_control, team_ledger
from .audio_editor import AudioEditorMixin
from .speech import SpeechMixin
from .media_profiles import identity as media_identity
from .bound_media import uses_bound
from .video_production import VideoProductionMixin
from .series_production import SeriesProductionMixin
from .material_production import MaterialProductionMixin
from .shot_workbench import plan_signature, pinned_selection
from .production_profiles import explicit as explicit_profile, resolve as resolve_profile, profile_signature, validate_episode
from .video_providers import LOCAL_PROFILE, normalize_video_profile
from .minimax_video import video_identity, validate_video_plan
from .quality import combine, inspect_image, inspect_video, repair_plan, semantic_review
from .visual_constraints import POLICY as VISUAL_CONSTRAINT_POLICY
from .creative_contract import resolve as resolve_contract, style_prompt
from .media_contract import video_motion
from .scheduler import (Budget, ProductionBudgetExceeded, ProductionNeedsDecision,
                        ProductionPaused, Scheduler)

def media_url(path):
    return "/media/" + str(Path(path).resolve().relative_to(ROOT.resolve()))


def _safe_id(value):
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise ValueError("Unsafe production identifier")
    return value


def _receipt_path(record):
    path = within(ROOT, record["path"])
    if not path.is_file() or sha256(path) != record.get("sha256"):
        raise ProductionNeedsDecision("Cached media no longer matches its checksum",
                                      "cache_integrity", {"path": record.get("path")})
    return path


def _matching_source(record, target):
    return (record.get("generation_signature", record.get("source_signature")) == target["signature"]
            or record.get("source_signature") in target.get("compatible_source_signatures", ()))


def _grounded_image_target_repair(repair, prompt, context):
    """Reassert a reference/image target's approved requirements, never QA prose.

    These targets have no shot starting state. In particular, an identity,
    environment or prop reference must retain its purpose during a repair rather
    than inherit the narrative staging rules used for shot keyframes.
    """
    context = context if isinstance(context, dict) else {}
    # The final request already contains target.prompt. Do not repeat a compiled
    # reference prompt here: that can exceed the image provider's input limit.
    # Context is provenance for facts actually present in that approved request,
    # not a second channel for adding uncompiled or unchecked requirements.
    facts = [("target.prompt", prompt)]
    if isinstance(prompt, str):
        facts.extend(("review_context." + field, context.get(field))
                     for field in ("prompt", "continuity")
                     if isinstance(context.get(field), str) and context[field] in prompt)
    seen = set()
    for field, value in facts:
        if isinstance(value, str) and value.strip() and value not in seen:
            repair["positive_constraints"].append({"field": field, "text": value})
            seen.add(value)
    repair["constraint_source"] = "approved_image_target_fields"
    if not repair["positive_constraints"]:
        return repair
    purpose = context.get("review_purpose")
    focus = {
        "character_reference": (
            "Render the approved character identity reference. Make the approved face, hair, "
            "wardrobe, worn accessories and carried equipment visibly readable as required below. "
            "Keep the approved reference pose, framing and background; do not turn the identity "
            "reference into a story action scene."),
        "scene_reference": (
            "Render the approved environment reference. Make its required spatial layout, "
            "landmarks and environmental details visibly readable. Preserve the approved "
            "reference composition without adding story actions or unrequested characters."),
        "prop_reference": (
            "Render the approved prop reference. Make its required shape, material, colors "
            "and identifying details visibly readable. Keep the approved reference presentation "
            "without adding a story action scene."),
        "composition_reference": (
            "Render the approved reference composition. Preserve every supplied character "
            "identity, environment layout and prop required below in one coherent composition. "
            "Keep this a reference image rather than a sequence of story actions."),
    }.get(purpose, (
        "Render the approved image requirements faithfully. Make the required subjects, "
        "clothing, accessories, objects and composition visibly readable where specified below. "
        "Keep the original image purpose and approved visual style."))
    repair.update(prompt_prefix=focus, prompt_suffix="")
    return repair


def _grounded_repair_plan(assessment, stage, attempt, expected=None, reference_roles=(),
                          *, target_prompt=None, review_context=None):
    """Route structured diagnoses and ground new prompts in approved shot facts.

    Review details remain audit evidence only: a description of the wrong image
    must never become an instruction to render that same mistake.
    """
    issues = [copy.deepcopy(value) for value in assessment.get("issues", [])
              if isinstance(value, dict)]
    severity_rank = {"blocking": 3, "error": 3, "major": 2,
                     "minor": 1, "warning": 1}
    aliases = {"black_frame": "black", "blank_frame": "black",
               "possible_blur": "blur", "low_quality": "blur",
               "audio_missing": "audio", "audio_nonfinite": "audio",
               "audio_silent": "audio", "audio_duration": "audio",
               "video_missing": "decode", "nonfinite": "decode"}
    priority = ("decode", "audio", "fps", "duration", "resolution",
                "continuity", "action", "identity", "composition", "black", "blur")
    ranked = [(severity_rank.get(str(value.get("severity", "major")).lower(), 2),
               str(value.get("code", "generation")).strip().lower() or "generation")
              for value in issues] or [(2, "generation")]
    highest = max(rank for rank, _ in ranked)
    codes = sorted({code for rank, code in ranked if rank == highest})
    primary = min(codes, key=lambda code: (
        priority.index(aliases.get(code, code)) if aliases.get(code, code) in priority else len(priority),
        code))
    route = aliases.get(primary, primary)
    # The legacy helper receives no free-form detail and only the selected code.
    repair = repair_plan({"issues": [{"code": route}]}, stage, attempt)
    technical = route in ("decode", "audio", "fps", "duration", "resolution")
    if route in ("continuity", "action"):
        # A generated motion defect does not invalidate an approved starting
        # frame. Keep frame replacement for continuity/identity diagnoses.
        repair.update(action="repair_scene_action_and_continuity",
                      seed_delta=1013 * attempt, prompt_suffix="",
                      regenerate_keyframe=stage == "video" and route == "continuity",
                      simplify_motion=stage == "video" and route == "action")
    elif route == "identity":
        repair["prompt_suffix"] = ("Preserve the exact face, age, hairstyle and clothing "
                                   "of each corresponding character reference.")
    elif route not in priority:
        repair.update(action="new_seed_with_approved_staging",
                      prompt_suffix="Preserve the approved cast, composition and scene relationships.")
    repair["diagnosis"] = {"primary_code": primary,
                           "severity": {3: "blocking", 2: "major", 1: "minor"}[highest],
                           "codes": sorted({code for _, code in ranked}), "issues": issues}
    repair["positive_constraints"] = []
    repair["prompt_prefix"] = ""
    repair["constraint_source"] = "approved_shot_fields"
    if technical:
        return repair
    if not isinstance(expected, dict):
        if stage == "image":
            return _grounded_image_target_repair(repair, target_prompt, review_context)
        return repair

    if stage == "image":
        from .review_contracts import starting_frame
        expected = starting_frame(expected)
        fields = ("prompt", "character_states")
    else:
        fields = ("action", "continuity", "prompt", "motion_prompt")
    for field in fields:
        value = expected.get(field)
        if value is not None and value != "" and value != [] and value != {}:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            repair["positive_constraints"].append({"field": field, "text": text})
    if route in ("continuity", "action"):
        focus = (("Render the exact approved starting pose, object placement and state "
                  if stage == "image" else
                  "Render the approved action and the exact approved object placement and state ")
                 + "as visibly readable facts in the frame.")
    elif route == "identity":
        focus = ("Preserve each approved character's exact face, age, hairstyle and clothing "
                 "from the corresponding identity reference while keeping the characters distinct "
                 "and performing the approved scene.")
    elif route == "composition":
        focus = "Make the approved framing, character placement and required scene objects clearly readable."
    elif route in ("black", "blur"):
        focus = "Use readable lighting and sharp visible detail while preserving the approved scene."
    else:
        focus = "Render the approved scene facts, cast and composition faithfully."
    if stage == "image":
        focus = ("Render only the approved static STARTING frame and starting character positions. "
                 "Do not advance to an action endpoint or combine multiple action phases. " + focus)
    constraints = [focus]
    labels = {"character_states": "Approved starting character states", "action": "Approved action and object state", "continuity": "Approved continuity",
              "prompt": "Approved visual scene", "motion_prompt": "Approved motion"}
    constraints.extend(labels[item["field"]] + ": " + item["text"]
                       for item in repair["positive_constraints"])
    if expected.get("reference_group") == "two" or len(reference_roles) == 2:
        constraints.append("Preserve both approved characters and their distinct identities, "
                           "positions and interaction in this scene.")
    constraints.append("Preserve all approved foreground and background characters and their scene "
                       "relationships; a supplied identity reference does not limit the visible cast.")
    if reference_roles:
        constraints.append("; ".join(
            ("Reference" if index == 1 else "reference") + f" image {index} corresponds to {role}"
            for index, role in enumerate(reference_roles, 1)) + ".")
    constraints.append("Character reference images guide face, age, hair and clothing only. "
                       "Render props and their placement from the approved scene facts above.")
    repair.update(prompt_prefix="\n".join(constraints), prompt_suffix="")
    return repair


def capabilities(config=None):
    config = config or {}
    from .media_profiles import local_kinds
    local = local_kinds(config)
    info = {
        "image": {"backend": "ComfyUI / FLUX.2 klein 4B",
                  "installed": local["image"] and (ROOT / "models/diffusion_models/flux-2-klein-4b.safetensors").is_file()},
        "motion_comic": {"backend": "FLUX.2 images + FFmpeg procedural_camera_motion",
                         "diffusion_video": False, "lip_sync": False},
        "diffusion_video": {"backend": "Wan2.2 TI2V 5B fp16",
                            "installed": local["video"] and (ROOT / "models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors").is_file(),
                            "validation": "requires_a_successful_project_render"},
        "tts": {"backend": "local Qwen3-TTS 1.7B CustomVoice",
                "installed": local["audio"] and (ROOT / "envs/tts/bin/python").is_file()},
        "lip_sync": {"backend": "existing LatentSync adapter", "physical_gpu": 5,
                     "minimum_free_mib": 21500, "automatic_for_motion_comic": False,
                     "validation": "legacy_adapter_requires_its_original_gpu_gate"},
        "quality": {"technical": True, "semantic": "optional_local_vlm",
                    "vbench": "not_integrated", "human_final_review": True},
        "scheduler": {"physical_cards": config.get("gpu_cards", list(range(8))),
                      "concurrent_generation_workers": 1,
                      "minimum_free_mib": config.get("image_min_free_mib", 15000),
                      "existing_services_preserved": True},
    }
    if config.get("gpu_service"):
        from urllib.parse import urlsplit
        host = urlsplit(config["gpu_service"]["base_url"]).hostname
        for key in ("image", "diffusion_video", "tts"):
            info[key].update(installed=None, execution_host=host, remote_configured=True,
                             validation="requires_remote_service_and_generation_receipts")
        info["scheduler"]["execution_host"] = host
        info["lip_sync"].update(available=False, validation="not_supported_by_remote_inference_api")
    for kind,key in (("image","image"),("audio","tts"),("video","diffusion_video")):
        connection = config.get(kind + "_connection", {})
        if connection.get("provider", "legacy") not in {"legacy", "comfyui_wan"}:
            info[key].update(backend=connection.get("model"),provider=connection["provider"],installed=None,configured=connection.get("enabled",True),validation="requires_successful_generation_receipt")
            if connection["provider"] == "comfyui_h3" and not config.get("gpu_service"):
                from studio.h3 import installation
                info[key]["installed"] = installation(connection)["installed"]
    if not any(local.values()): info["scheduler"]["physical_cards"] = []
    return info


class ProductionEngine:
    def __init__(self, config=None, event_callback=None):
        self.config = config or {}
        self.event_callback = event_callback

    def run(self, project, checkpoint, control=None, semantic_evaluator=None, dialogue_rewriter=None):
        return generate_project(project, self.config, checkpoint, control, semantic_evaluator, dialogue_rewriter)


class _Run(MaterialProductionMixin, SeriesProductionMixin, VideoProductionMixin, SpeechMixin, AudioEditorMixin):
    receipt_path = staticmethod(_receipt_path)
    media_url = staticmethod(media_url)

    def capabilities(self):
        return capabilities(self.config)

    def __init__(self, project, config, checkpoint, control, evaluator=None, dialogue_rewriter=None):
        self.project = copy.deepcopy(project)
        from .drama_intent import enabled as whole_drama
        self.one_sentence = whole_drama(project)
        from .drama_intent import closed_mouths
        self.closed_mouths = closed_mouths(project)
        from .drama_intent import bound_budget
        self.config = bound_budget(project, dict(config or {}))
        connection = self.config.get("video_connection", {"profile": "local_wan", **LOCAL_PROFILE})
        self.video_connection = {"profile": connection.get("profile", "local_wan"),
                                 **normalize_video_profile({k: v for k, v in connection.items() if k != "profile"})}
        prompting = self.config.get("h3_prompting", "manual")
        if prompting not in {"automatic", "manual"}:
            raise ValueError("h3_prompting must be automatic or manual")
        self.h3_automatic = self.video_connection["provider"] == "comfyui_h3" and prompting == "automatic"
        self.checkpoint = checkpoint or (lambda patch: None)
        self.control, self.evaluator = control, evaluator
        self.dialogue_rewriter = dialogue_rewriter
        self.semantic_enabled = bool(self.config.get("semantic_enabled", evaluator is not None))
        self.semantic_max_attempts = int(self.config.get("semantic_max_attempts", 2))
        if not 1 <= self.semantic_max_attempts <= 3:
            raise ValueError("semantic_max_attempts must be between one and three")
        explicit_policy = self.config.get("semantic_review_revision")
        from .temporal_review import POLICY as temporal_evidence_policy
        self.temporal_evidence_policy = temporal_evidence_policy
        self.review_policy = fingerprint({
            "policy": explicit_policy or "local_vlm_review_v2",
            **({"temporal_evidence_policy": temporal_evidence_policy}
               if not explicit_policy or self.h3_automatic else {}),
            "model": self.config.get("semantic_model"),
            **({"temporal_sampling": self.config.get("temporal_sampling", {
                "policy": "uniform_endpoints_and_cut_neighbors.v1", "count": 8})}
               if "temporal_sampling" in self.config or not explicit_policy else {}),
            **({"model_identity": self.config["semantic_model_identity"]}
               if self.config.get("semantic_model_identity") is not None else {}),
            "implementation": {} if explicit_policy else {
                name: sha256(ROOT / "factory" / name)
                for name in ("llm.py", "quality.py", "evaluation_sampling.py", "temporal_review.py", "spatial_review.py", "review_contracts.py", "camera_review.py", "frame_observation.py") if (ROOT / "factory" / name).is_file()},
            "evaluator": (getattr(evaluator, "__module__", "") + "." +
                          getattr(evaluator, "__qualname__", type(evaluator).__name__)) if evaluator else None,
        })
        self.ident = _safe_id(self.project["id"])
        self.slug = _safe_id(self.project.get("slug") or ("factory-" + self.ident))
        if not self.slug.startswith("factory-"):
            raise ValueError("Autonomous projects use their own factory-* production directory")
        self.folder = within(ROOT / "production", self.slug)
        self.folder.mkdir(parents=True, exist_ok=True)
        owner_path = self.folder / "factory_owner.json"
        if owner_path.exists():
            if read_json(owner_path).get("project_id") != self.ident:
                raise ProductionNeedsDecision("This production directory belongs to another project",
                                              "project_ownership")
        else:
            if (self.folder / "episode.json").exists():
                raise ProductionNeedsDecision("An existing unowned production was preserved",
                                              "project_ownership")
            write_json(owner_path, {"project_id": self.ident, "created_at": time.time(),
                                    "schema": "autonomous_factory.owner.v1"})
        self.production = copy.deepcopy(self.project.get("production") or {})
        self.review_only_existing_videos = self.production.get(
            "review_only_existing_videos", self.config.get("review_only_existing_videos", self.h3_automatic))
        if type(self.review_only_existing_videos) is not bool:
            raise ValueError("review_only_existing_videos must be boolean")
        # Persist the boundary through remote-GPU yields. A newly generated
        # video must not accidentally become an old protected video on resume.
        from .shot_workbench import retained_video_results
        baseline = self.production.setdefault("review_only_video_results", [
            str(path.relative_to(ROOT)) for path in retained_video_results(self.folder)]) if self.review_only_existing_videos else []
        if not isinstance(baseline, list) or any(not isinstance(path, str) for path in baseline):
            raise ValueError("Invalid retained video boundary")
        self.existing_video_results = frozenset(within(ROOT, path) for path in baseline)
        if any(not path.is_relative_to((self.folder / "renders").resolve()) or path.name != "result.json"
               for path in self.existing_video_results):
            raise ValueError("Retained videos must belong to this project")
        self.budget = Budget(self.config, self.production)
        self._scheduler = None
        self.max_attempts = int(self.config.get("max_attempts",
                                               self.project.get("budget", {}).get("max_attempts", 3)))
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be between one and five")
        from .production_execution import enabled as recovery_enabled
        self.autonomous_recovery = recovery_enabled(self.config)
        from .production_execution import strategy_enabled, attempt_limit
        self.strategy_recovery = strategy_enabled(self.config)
        from .production_completion import authorization
        self.attempt_ceiling = None if authorization(self.project) else attempt_limit({**self.config, 'max_attempts': self.max_attempts})
        if self.autonomous_recovery and (not self.semantic_enabled or not callable(self.evaluator)):
            raise ProductionNeedsDecision('系统自主返修需要可用的独立画面审核；未新增生成', 'semantic_unavailable')
        self.mode = self.project.get("format", self.project.get("mode", "motion_comic"))
        if self.mode not in ("motion_comic", "animation", "live_action"):
            raise ValueError("Unsupported production format")
        self.creative_contract = resolve_contract(self.project)
        self.episode = copy.deepcopy(self.project.get("episode") or self.project.get("script") or {})
        if explicit_profile(self.project):
            self.episode["production_profile"] = resolve_profile(self.project)
        if not isinstance(self.episode, dict):
            raise ValueError("A structured episode/script is required")
        self.cast = self.episode.get("cast") or self.project.get("cast") or {}
        if not isinstance(self.cast, dict) or not self.cast:
            raise ValueError("The episode must define a cast reference dictionary")
        self.cast = {str(role): (value.get("description", value.get("appearance", ""))
                                if isinstance(value, dict) else str(value))
                     for role, value in self.cast.items()}
        for role, description in self.cast.items():
            _safe_id(role)
            if not description.strip():
                raise ValueError("A character reference description is empty")
        self.assets = copy.deepcopy(self.project.get("assets") or {})
        self.assets.setdefault("characters", {})
        native = self.config.get("native_720p", False)
        self.image_size = (int(self.config.get("image_width", 736 if native else 576)),
                           int(self.config.get("image_height", 1312 if native else 1024)))
        self.output_size = (int(self.config.get("output_width", 720)),
                            int(self.config.get("output_height", 1280)))
        self.video_size = (int(self.config.get("video_width", self.image_size[0])),
                           int(self.config.get("video_height", self.image_size[1])))
        if any(v < 128 or v > 2048 or v % 32 for v in self.image_size):
            raise ValueError("Image dimensions must be 128..2048 and divisible by 32")
        if any(v < 128 or v > 2048 or v % 2 for v in self.output_size):
            raise ValueError("Delivery dimensions must be even and bounded")
        if abs(self.output_size[0] / self.output_size[1] - 9 / 16) > .005:
            raise ValueError("The current delivery profile is portrait 9:16")
        self.styles = copy.deepcopy(self.project.get("styles") or [])
        chosen = self.project.get("selected_style") or self.project.get("selected_style_id") or self.project.get("style_id")
        if isinstance(chosen, dict):
            style = next((s for s in self.styles if s.get("id") == chosen.get("id")), chosen)
        else:
            style = next((s for s in self.styles if s.get("id") == chosen), {})
        self.selected_style = copy.deepcopy(style) if chosen else None
        self.style_prompt = str(style.get("prompt") or self.episode.get("visual_style") or
                                ("Cinematic illustrated graphic novel, consistent character design, restrained color, expressive faces, vertical composition."
                                 if self.mode == "motion_comic" else
                                 "Cinematic production still, natural skin and anatomy, restrained film color, vertical composition."))
        if self.creative_contract:
            self.episode["creative_contract"] = copy.deepcopy(self.creative_contract)
            self.style_prompt = style_prompt(self.project) + "\n" + self.style_prompt
        current = {s["id"]: s for s in self.project.get("shots", []) if isinstance(s, dict) and "id" in s}
        self.shots = []
        source_shots = self.episode.get("shots") or self.project.get("shots") or []
        seen = set()
        for i, raw in enumerate(source_shots):
            shot = {**copy.deepcopy(current.get(raw.get("id"), {})), **copy.deepcopy(raw)}
            if self.strategy_recovery:
                from .production_handoffs import restore_media
                shot.update(restore_media(self, raw, current.get(raw.get('id'), {})))
            shot["id"] = _safe_id(shot.get("id") or f"S{i + 1:02d}")
            if self.one_sentence:
                for kind, revision in self.project.get('episode_media_revisions', {}).get(shot['id'], {}).items():
                    shot.setdefault('render_revisions', {})[kind] = max(shot.get('render_revisions', {}).get(kind, 0), revision)
            if shot["id"] in seen:
                raise ValueError("Duplicate shot ID")
            seen.add(shot["id"])
            shot["seconds"] = round(float(shot.get("seconds", shot.get("duration", 4))) * 24) / 24
            if not .5 <= shot["seconds"] <= 15:
                raise ValueError("Each shot must last between 0.5 and 15 seconds")
            shot["prompt"] = str(shot.get("prompt") or shot.get("image_prompt") or "").strip()
            if not shot["prompt"]:
                raise ValueError("A shot is missing its image prompt")
            shot["motion_prompt"] = str(shot.get("motion_prompt") or shot.get("video_prompt") or shot["prompt"])
            shot.setdefault("status", "pending")
            shot.setdefault("attempts", [])
            for j, line in enumerate(shot.get("dialogue", [])):
                line["id"] = _safe_id(line.get("id") or f"{shot['id']}_D{j+1:02d}")
                line["role"] = str(line.get("role", "narrator"))
                if line["role"] not in self.cast and line["role"] != "narrator":
                    raise ValueError("Dialogue refers to an undefined character")
                if not str(line.get("text", "")).strip():
                    raise ValueError("Empty dialogue line")
            self.shots.append(shot)
        dialogue_ids = [d["id"] for s in self.shots for d in s.get("dialogue", [])]
        if len(dialogue_ids) != len(set(dialogue_ids)):
            raise ValueError("Duplicate dialogue ID")
        self._apply_edit_plan()

    def _apply_edit_plan(self):
        plan = self.project.get("edit_plan") or {}
        if self.project.get("reedit_requested") and not plan:
            raise ProductionNeedsDecision("The requested re-edit needs a concrete editing plan before it can run",
                                          "edit_plan_required",
                                          {"feedback": self.project.get("edit_feedback", "")})
        if not plan:
            return
        supported = {"shot_order", "trim_seconds", "music_gain", "cut_style"}
        if set(plan) - supported:
            raise ProductionNeedsDecision("The edit plan contains an unsupported operation", "edit_plan_required",
                                          {"unsupported": sorted(set(plan) - supported)})
        if plan.get("cut_style", "hard") != "hard":
            raise ProductionNeedsDecision("This editor currently supports hard cuts", "edit_plan_required")
        lookup = {s["id"]: s for s in self.shots}
        if plan.get("shot_order"):
            ids = plan["shot_order"]
            if len(ids) != len(lookup) or set(ids) != set(lookup):
                raise ValueError("shot_order must contain each existing shot exactly once")
            self.shots = [lookup[i] for i in ids]
        for ident, seconds in plan.get("trim_seconds", {}).items():
            if ident not in lookup or not .5 <= float(seconds) <= lookup[ident]["seconds"]:
                raise ValueError("trim_seconds must shorten an existing shot to at least 0.5 seconds")
            lookup[ident]["seconds"] = round(float(seconds) * 24) / 24
        if "music_gain" in plan and not 0 <= float(plan["music_gain"]) <= .3:
            raise ValueError("music_gain must be between 0 and 0.3")

    def emit(self, phase=None, **patch):
        if phase:
            self.production["phase"] = phase
        self.production.update(self.budget.snapshot())
        self.production.update({"format": self.mode, "max_attempts": self.max_attempts,
                                "strategy_attempt_limit": self.attempt_ceiling,
                                "engine": "cami_orchestrator_remote_gpu_v1" if self.config.get("gpu_service") else "local_autonomous_factory_v1",
                                "active_workers_limit": 1,
                                "semantic_enabled": self.semantic_enabled, "semantic_max_attempts": self.semantic_max_attempts})
        payload = {"production": copy.deepcopy(self.production),
                   "shots": copy.deepcopy(self.shots), "assets": copy.deepcopy(self.assets), **patch}
        self.project.update(copy.deepcopy(payload))
        self.checkpoint(payload)

    def _resource_event(self, event):
        current = self.production.get('resource_wait') or {}
        if current.get('enabled') is True and current.get('authorized_by') == 'producer':
            current['scheduler_event'] = copy.deepcopy(event)
        else:
            self.production["resource_wait"] = copy.deepcopy(event)
        if event["status"] in ("checking", "waiting"):
            self.production.setdefault("resource_resume_phase", self.production.get("phase", "production"))
            self.emit("waiting_resources")
        else:
            phase = self.production.pop("resource_resume_phase", None)
            self.emit(phase or ("resources_ready" if event["status"] == "ready" else "waiting_resources"))

    def boundary(self, gpu=False):
        self.emit()
        from .drama_intent import bound_budget
        self.budget.max_gpu = min(self.budget.max_gpu, bound_budget(self.project, self.config).get("max_gpu_seconds", self.budget.max_gpu))
        self.budget.check(self.control, require_gpu=gpu)

    def _valid_selection(self, target):
        pinned = pinned_selection(self.project, target)
        if pinned:
            _receipt_path(pinned)
            return pinned
        selected = target["folder"] / "selected.json"
        if not selected.exists():
            return None
        record = read_json(selected)
        if not _matching_source(record, target):
            return None
        _receipt_path(record)
        if record.get("qa", {}).get("status") == "fail":
            return None
        return record

    def _review_required(self, target):
        return self.semantic_enabled and target.get("review", False)

    def _review_current(self, target, receipt):
        if not self._review_required(target):
            return True
        if self.creative_contract:
            if receipt.get("creative_contract_signature") != fingerprint(self.creative_contract):
                return False
            if target.get("style"):
                from .media_contract import visual_direction
                if receipt.get("visual_direction_signature") != fingerprint(visual_direction(target["style"])):
                    return False
        if self.autonomous_recovery and target['kind'] == 'video':
            from .visual_constraints import required, certified
            expected = copy.deepcopy(target.get('shot') or {})
            if not expected.get('lip_sync') and self.closed_mouths:
                expected['execution_constraints'] = {'visible_mouths_closed': True}
            if required(expected) and not certified(receipt.get('qa', {}).get('semantic', {}), _receipt_path(receipt)):
                return False
        return (receipt.get("qa", {}).get("semantic", {}).get("status") == "pass"
                and receipt.get("review_signature") == target["review_signature"])

    def _receipt_attempt(self, target, receipt):
        path = _receipt_path(receipt)
        attempt_file = path.parent / "attempt.json"
        if attempt_file.exists():
            return read_json(attempt_file)
        return {"take": receipt.get("take", path.parent.name),
                "attempt": int(receipt.get("attempt", 1)), "stage": target["kind"],
                "state": "generated", "source_signature": target["signature"],
                "prompt": target["prompt"], "output": receipt}

    def _semantic_assessment(self, target, record, path):
        if record.get('media_handoff'):
            from .production_handoffs import load, advance
            case = load(self, record['media_handoff'])
            advance(self, case, 'reviewing', 'visual_critic', artifact={'path': str(path), 'sha256': sha256(path)})
        history = record.setdefault("semantic_attempts", [])
        expected = copy.deepcopy(target.get('shot') or target.get('review_context') or
                                 {'id': target['key'], 'prompt': target['prompt']})
        if self.autonomous_recovery and target['kind'] == 'video' and not expected.get('lip_sync') and self.closed_mouths:
            expected['execution_constraints'] = {'visible_mouths_closed': True,
                                                 'source': 'existing_external_voiceover_adapter_contract'}
        answer = None
        for number in range(1, self.semantic_max_attempts + 1):
            self.boundary()
            started = time.monotonic()
            answer = semantic_review(self.evaluator, self.project, expected, path, target["references"])
            if self.autonomous_recovery and target['kind'] == 'video':
                from .visual_constraints import required, certified
                if required(expected) and answer.get('status') == 'pass' and not certified(answer, path):
                    answer.update(status='unavailable', detail='明确视觉要求缺少本媒体的独立样本证据，原视频保留待补审。')
            history.append({"attempt": len(history) + 1, "invocation_attempt": number,
                            "review_signature": target["review_signature"],
                            "at": time.time(), "elapsed_seconds": round(time.monotonic() - started, 3),
                            "assessment": answer})
            record.update(state="reviewing", review_signature=target["review_signature"])
            self._save_attempt(target, record)
            if answer.get("status") in ("pass", "fail"):
                break
        return answer

    def _assert_semantic_gate(self):
        if self.creative_contract and self.creative_contract["motion"] == "generated_video":
            missing = [shot["id"] for shot in self.shots if video_motion(shot.get("video")) != "generated_video"]
            if missing:
                raise ProductionNeedsDecision(
                    "当前制作要求生成人物动作视频，静帧运镜或来源不明的片段不能替代动画。",
                    "generated_motion_required", {"shots": missing})
        if self.creative_contract and not self.semantic_enabled:
            raise ProductionNeedsDecision("当前画风与动作要求需要独立视觉审核，请先启用画面质检。",
                                          "semantic_unavailable")
        if not self.semantic_enabled:
            return
        missing = []
        for shot in self.shots:
            if shot.get("image", {}).get("qa", {}).get("semantic", {}).get("status") != "pass":
                missing.append({"shot": shot["id"], "stage": "image"})
            if self.mode != "motion_comic" and shot.get("video", {}).get("qa", {}).get("semantic", {}).get("status") != "pass":
                missing.append({"shot": shot["id"], "stage": "video"})
            if self.creative_contract:
                image_review = shot.get("image", {}).get("qa", {}).get("semantic", {})
                if image_review.get("style_alignment", {}).get("status") != "match":
                    missing.append({"shot": shot["id"], "stage": "image_style"})
                if self.mode != "motion_comic":
                    video_review = shot.get("video", {}).get("qa", {}).get("semantic", {})
                    if (video_review.get("style_alignment", {}).get("status") != "match"
                            or video_review.get("motion_alignment", {}).get("status") not in {"character_motion", "object_motion"}):
                        missing.append({"shot": shot["id"], "stage": "video_style_and_motion"})
        if missing:
            raise ProductionNeedsDecision("Required semantic media reviews are not complete",
                                          "semantic_unavailable", {"pending": missing,
                                                                   "resume_action": "review_existing_assets_only"})

    def _attempts(self, target):
        records = []
        for path in sorted(target["folder"].glob("take_*/attempt.json")):
            record = read_json(path)
            if _matching_source(record, target):
                record["_file"] = str(path)
                records.append(record)
        return records

    def _team_target(self, target):
        """Map actual renderer targets to stable production work, excluding previews."""
        if target.get("style") is not None:
            return None
        if target.get("character"):
            return "character", str(target["character"])
        shot = target.get("shot")
        if shot is not None:
            return target["kind"], str(shot["id"])
        return None

    def _team_admit_target(self, target):
        if target.get("generation_plan"):
            actual = [media_receipt(p)["sha256"] for p in target["references"]]
            if actual != target["generation_plan"]["parameters"]["reference_sha256"]:
                raise ProductionNeedsDecision("生成前引用像素已变化，原编译结果不再适用", "generation_frame_changed")
        identity = self._team_target(target)
        team_control.admit(self.ident, *(identity or (target["kind"], str(target["key"]))))

    def _team_publish_target(self, target):
        identity = self._team_target(target)
        if identity:
            team_control.assert_publishable(self.ident, *identity, input_version=target["signature"],
                                            compatible_input_versions=(*target.get("compatible_source_signatures", ()),
                                                *target.get("compatible_team_input_versions", ())))
        else:
            team_control.assert_publishable(self.ident, "stage", "visual_development")

    def _team_after_target(self, target):
        identity = self._team_target(target)
        if identity:
            team_control.after_unit(self.ident, *identity)

    def _team_begin_target(self, target, record):
        identity = self._team_target(target)
        if identity is None:
            return
        key = (*identity, record["take"])
        tokens = self.__dict__.setdefault("_team_generation_tokens", {})
        if key not in tokens:
            tokens[key] = team_ledger.begin_unit(
                self.ident, *identity, input_version=target["signature"],
                inputs={"prompt": record["prompt"], "references": record.get("references", []),
                        "parameters": record.get("parameters", {}), "take": record["take"]})

    def _team_review_unit(self, target, record, receipt):
        identity = self._team_target(target)
        if identity is None:
            return nullcontext(None)
        kind, scope = identity
        review_kind = "video_review" if kind == "video" else "image_review"
        return team_ledger.unit(self.ident, review_kind, scope,
                                input_version=target["review_signature"],
                                dependencies=[f"{kind}:{scope}"],
                                inputs={"artifact": receipt, "take": record["take"],
                                        "semantic_required": self._review_required(target)})

    def _save_attempt(self, target, record):
        record = copy.deepcopy(record)
        record.pop("_file", None)
        write_json(target["folder"] / record["take"] / "attempt.json", record)
        shot = target.get("shot")
        if shot is not None:
            public = copy.deepcopy(record)
            public["status"] = public.get("state")
            rows = shot.setdefault("attempts", [])
            rows[:] = [r for r in rows if not (r.get("stage") == record["stage"] and
                                               r.get("take") == record["take"])]
            rows.append(public)
        identity = self._team_target(target)
        if identity is not None and (record.get("output") or record.get("state") == "failed"):
            token = self.__dict__.get("_team_generation_tokens", {}).pop((*identity, record["take"]), None)
            if token:
                team_ledger.finish_unit(token, status="completed" if record.get("output") else "failed",
                                        artifact=record.get("output"), model=record.get("model"),
                                        details={key: record[key] for key in
                                                 ("error", "elapsed_seconds", "remote_task", "repair") if key in record})

    def _attach(self, target, record):
        self._team_publish_target(target)
        path = _receipt_path(record)
        if target.get("shot") is not None:
            shot = target["shot"]
            key = "image" if target["kind"] == "image" else "video"
            shot[key] = record
            shot[key + "_path"] = record["path"]
            shot[key + "_url"] = media_url(path)
            shot["qa"] = record.get("qa", {})
            shot["status"] = "passed" if key == "video" else "pending"
        if target.get("character"):
            self.assets["characters"][target["character"]] = record
        if target.get("style") is not None:
            target["style"].update(preview_url=media_url(path), preview_path=record["path"],
                                   preview=record, preview_status="generated")
        target["selected"] = record
        identity = self._team_target(target)
        if identity:
            team_ledger.mark_reused(self.ident, *identity, artifact=record, input_version=target["signature"],
                                    review_status=record.get("qa", {}).get("status"))
            team_ledger.record_adoption(self.ident, *identity, artifact=record)

    @property
    def scheduler(self):
        if self._scheduler is None:
            self._scheduler = Scheduler(self.config, self.budget, event_callback=self._resource_event)
        return self._scheduler

    @scheduler.setter
    def scheduler(self, value):
        self._scheduler = value

    def _target(self, key, folder, prompt, references=(), kind="image", **extra):
        from .reference_identity import generation_identity
        from .shot_continuity import request_context, bind_prompt
        approved_shot = extra.get('shot') or extra.get('review_context') or {}
        if approved_shot.get('id') in {shot['id'] for shot in self.shots} and kind in {'image', 'video'}:
            continuity = request_context(self.project, approved_shot, {'shots': self.shots})
            if continuity:
                extra['shot_continuity'] = continuity
                prompt = bind_prompt(prompt, continuity)
        refs = [Path(p) for p in references]
        dimensions = self.image_size if kind == "image" else self.video_size
        signature = fingerprint({"prompt": prompt, "references": [media_receipt(p) for p in refs],
                                 "dimensions": dimensions, "kind": kind,
                                 **({"connection": media_identity(self.config["image_connection"])} if kind == "image" and self.config.get("image_connection", {}).get("provider", "legacy") != "legacy" else {}),
                                 **({"connection": media_identity(self.video_connection)} if kind == "video" and self.video_connection.get("provider") in {"comfyui_local", "comfyui_external"} else {}),
                                 "model": (self.config.get('image_connection',{}).get('model','FLUX.2-klein-4B') if kind == "image" else
                                           video_identity(self.video_connection) if self.video_connection["provider"] == "minimax"
                                           else self.video_connection["model"]),
                                 **({"connection": media_identity(self.video_connection)} if kind == "video" and self.video_connection["provider"] == "comfyui_h3" else {}),
                                 "seconds": extra.get("seconds"),
                                 "mode": self.mode, "profile": self.config.get("video_profile", "quality"),
                                 **({"asset_references": generation_identity(extra["asset_references"], root=ROOT)} if extra.get("asset_references") else {}),
                                 **({"render_revision": extra["shot"]["render_revisions"][kind]}
                                    if (extra.get("shot") or {}).get("render_revisions", {}).get(kind) else {}),
                                 **({"production_profile": profile_signature(self.project)} if explicit_profile(self.project) else {})})
        shot = extra.get("shot") or extra.get("review_context") or {}
        compatible_sources = []
        if kind == "video" and self.h3_automatic:
            from .h3_prompting import identity, compatible_compiler
            generation_inputs = {"source": signature,
                "h3_prompt_compiler": identity(self.config.get("h3_prompt_config", {})),
                "approved_shot": {key: shot.get(key) for key in (
                    "action", "continuity", "characters", "character_states", "cast", "lip_sync",
                    "camera_motion", "motion_prompt", "prompt", "material_constraints")},
                "style": self.style_prompt, "cast": self.cast}
            signature = fingerprint(generation_inputs)
            # Old H3 signatures included the full first-frame QA. Reconstruct
            # each old hash with its saved QA, but with ALL current generation
            # inputs (including actual frame bytes, recipe and compiler).
            # A matching frame alone cannot authorize reuse after a shot edit.
            for path in folder.glob("take_*/attempt.json"):
                old = read_json(path)
                old_refs = old.get("references") or []
                if not old_refs or not isinstance(old_refs[0], dict):
                    continue
                compilers = [generation_inputs["h3_prompt_compiler"]]
                frozen = (old.get("h3_prompt") or {}).get("compiler")
                if ((path.parent / "result.json").is_file()
                        and compatible_compiler(frozen, compilers[0])):
                    compilers.append(frozen)
                for compiler in compilers:
                    inputs = {**generation_inputs, "h3_prompt_compiler": compiler}
                    for candidate in (inputs, {**inputs, "first_frame_review": old_refs[0].get("qa")}):
                        legacy = fingerprint(candidate)
                        if self.creative_contract:
                            legacy = fingerprint({"source_signature": legacy, "creative_contract": self.creative_contract})
                        if legacy in (old.get("source_signature"), old.get("generation_signature")):
                            compatible_sources.append(legacy)
        expected = {key: shot.get(key) for key in (
            "id", "seconds", "prompt", "motion_prompt", "dialogue", "reference_group",
            "reference_shots", "continuity", "camera_motion", "action", "cast", "characters")}
        if extra.get('shot_continuity'):
            expected['shot_continuity'] = copy.deepcopy(extra['shot_continuity'])
        if self.creative_contract:
            signature = fingerprint({"source_signature": signature, "creative_contract": self.creative_contract})
            expected.update(creative_contract=self.creative_contract,
                            approved_visual_style={key: self.selected_style[key] for key in
                                ("id", "name", "description", "prompt", "palette")
                                if self.selected_style and key in self.selected_style},
                            visual_direction=shot.get("visual_direction"))
        if shot.get("material_constraints"):
            expected["material_constraints"] = shot["material_constraints"]
        if self.project.get("resolved_visual_style") is not None:
            expected["resolved_visual_style"] = copy.deepcopy(self.project["resolved_visual_style"])
        if extra.get("character") and self.project.get("team_character_revisions", {}).get(extra["character"]):
            signature = fingerprint({"source_signature": signature, "character_revision": self.project["team_character_revisions"][extra["character"]]})
        review_key = ("video_review:" if kind == "video" else "image_review:") + str(extra.get("character") or key)
        review_signature = fingerprint({"source_signature": signature, "policy": self.review_policy,
                                        **({"static_review": self.config['static_review_policy']}
                                           if kind == 'image' and not shot.get('review_purpose') and self.config.get('static_review_policy') else {}),
                                        **({"explicit_constraint_policy": VISUAL_CONSTRAINT_POLICY}
                                           if kind == "video" and self.autonomous_recovery else {}),
                                        **({"reference_evidence": extra["asset_references"]} if extra.get("asset_references") else {}),
                                        **({"temporal_evidence_policy": self.temporal_evidence_policy}
                                           if kind == "video" else {}),
                                        "expected": expected, "cast": self.cast, "format": self.mode})
        if self.project.get("team_review_revisions", {}).get(review_key):
            review_signature = fingerprint({"review": review_signature, "producer_review_revision": self.project["team_review_revisions"][review_key]})
        from .reference_review import POLICY as reference_review_policy, PURPOSES as reference_purposes
        if kind == "image" and shot.get("review_purpose") in reference_purposes:
            review_signature = fingerprint({"review": review_signature, "reference_evidence_policy": reference_review_policy,
                "reference_purpose": shot["review_purpose"],
                "approved_reference_requirements": shot.get("approved_reference_requirements"),
                "resolved_visual_style": self.project.get("resolved_visual_style")})
        target = {"key": key, "folder": folder, "prompt": prompt, "references": refs,
                "kind": kind, "signature": signature, "review_signature": review_signature,
                "compatible_source_signatures": compatible_sources, **extra}
        if shot.get('id') == key:
            target['approved_render_style'] = shot.get('render_style_prompt', self.style_prompt)
        from .generation_pipeline import enabled, prepare, source, capabilities
        if enabled(self.project) and shot.get("id") == key and re.fullmatch(r"S[0-9]{2,3}", key) and kind in {"image", "video"}:
            # Reuse valid approved legacy results before introducing any new
            # model request. An opt-in configuration is not a remake command.
            if self._valid_selection(target):
                return target
            pipeline_config = {**self.config, "image_width": self.image_size[0], "image_height": self.image_size[1],
                "video_width": self.video_size[0], "video_height": self.video_size[1], "video_connection": self.video_connection}
            identities = [(r, _receipt_path(self.assets["characters"][r])) for r in self._shot_render_roles(shot)
                          if r in self.assets["characters"]]
            prior_path = folder / "selected.json"
            prior = read_json(prior_path) if prior_path.exists() else {}
            old_plan = prior.get("generation_plan") or {}
            current_source = source(self.project, shot, refs, style=shot.get("render_style_prompt", self.style_prompt), cast=self.cast)
            current_source["reference_bindings"] = copy.deepcopy(list(extra.get("asset_references", ())))
            if identities:
                current_source["identity_references"] = [{"character_id": r, "sha256": media_receipt(p)["sha256"]} for r, p in identities]
            current_source["reference_map"] = [{"reference_index": i+1, "sha256": ref["sha256"],
                "character_ids": [r["character_id"] for r in current_source.get("identity_references", []) if r["sha256"] == ref["sha256"]],
                "materials": [r for r in current_source["reference_bindings"] if r.get("sha256") == ref["sha256"]]}
                for i, ref in enumerate(current_source["references"])]
            def facts(value):
                # Keep reference mapping, versions and approved facts in the
                # identity; only verified composition review journals may vary.
                return generation_identity({k:v for k,v in value.items()
                    if k not in {"pipeline_snapshot", "prompt_snapshot_sha256"}}, root=ROOT)
            if (old_plan and prior.get("qa", {}).get("status") == "pass"
                    and facts(old_plan.get("source", {})) == facts(current_source)
                    and old_plan.get("capabilities") == capabilities(pipeline_config, self.project, kind)):
                _receipt_path(prior)
                target.update(prompt=old_plan["parameters"]["prompt"], signature=prior["source_signature"], generation_plan=old_plan,
                    review_signature=fingerprint({"review": review_signature, "generation_plan": old_plan["signature"]}))
                # Older runs could recompile identical approved inputs after a
                # composition review changed, leaving the ledger on that newer
                # plan. Permit the verified selected pixels to be re-reviewed
                # against those equivalent inputs. This is ONLY a ledger fence:
                # sibling takes keep their own generation identity and journal.
                target["compatible_team_input_versions"] = []
                for attempt_path in sorted(folder.glob("take_*/attempt.json")):
                    attempt = read_json(attempt_path)
                    plan = attempt.get("generation_plan") or {}
                    if (attempt.get("source_signature")
                            and facts(plan.get("source", {})) == facts(current_source)
                            and plan.get("capabilities") == capabilities(pipeline_config, self.project, kind)):
                        target["compatible_team_input_versions"].append(attempt["source_signature"])
                return target
            # Resume a frozen compiled plan before checking the media journal;
            # the legacy target signature must not hide its own in-flight job.
            for attempt_path in sorted(folder.glob("take_*/attempt.json"), reverse=True):
                attempt = read_json(attempt_path)
                compiled = attempt.get("generation_plan") or {}
                previous_source = compiled.get("source", {})
                if generation_identity(previous_source, root=ROOT) == generation_identity(current_source, root=ROOT) and compiled.get("capabilities") == capabilities(pipeline_config, self.project, kind):
                    target.update(prompt=compiled["parameters"]["prompt"], generation_plan=compiled,
                        # An accepted request resumes under its own immutable
                        # identity even when only upstream QA evidence changed.
                        signature=attempt.get("source_signature") or fingerprint({"source": signature, "generation_plan": compiled["signature"]}),
                        review_signature=fingerprint({"review": review_signature, "generation_plan": compiled["signature"]}))
                    return target
            self.boundary()
            self._guard_remote_submissions(target)
            compiled = prepare(self.project, shot, refs, config=pipeline_config, phase=kind,
                style=shot.get("render_style_prompt", self.style_prompt), cast=self.cast,
                reference_bindings=extra.get("asset_references", ()),
                identities=identities, checkpoint=self.boundary)
            self.boundary()
            target.update(prompt=compiled["parameters"]["prompt"], generation_plan=compiled,
                signature=fingerprint({"source": signature, "generation_plan": compiled["signature"]}),
                review_signature=fingerprint({"review": review_signature, "generation_plan": compiled["signature"]}))
        return target

    def _continuity_video_repair_allowed(self, target, retained):
        """A server-recorded return permits only its changed shot in this version.

        The original results remain protected evidence. Matching/compatible inputs
        still use the existing review path, and unresolved submissions are checked
        by the normal renderer before any new take can be admitted.
        """
        authorization = self.project.get("continuity_video_repair") or {}
        if (not self.h3_automatic or self.project.get("record_kind") != "scene_unit"
                or not isinstance(authorization, dict)
                or authorization.get("source") != "episode_continuity_review"
                or type(authorization.get("production_revision")) is not int
                or authorization["production_revision"] != self.project.get("production_revision")
                or not self.project.get("episode_id")
                or authorization.get("episode_id") != self.project["episode_id"]):
            return False
        shot_ids = authorization.get("shot_ids")
        shot_id = (target.get("shot") or {}).get("id")
        if not isinstance(shot_ids, list) or not shot_id or shot_id not in shot_ids:
            return False
        if not isinstance(target.get("signature"), str) or not target["signature"]:
            return False
        for path in retained:
            try:
                receipt = read_json(path)
            except (OSError, ValueError):
                return False
            if (not isinstance(receipt, dict) or not receipt.get("source_signature")
                    or _matching_source(receipt, target)):
                return False
        return True

    def _review_only_video(self, target):
        if not self.review_only_existing_videos or target["kind"] != "video":
            return False
        retained = [path for path in self.existing_video_results
                    if path.parent.parent == target["folder"]]
        return bool(retained) and not self._continuity_video_repair_allowed(target, retained)

    def _stop_existing_video(self, target, record=None, receipt=None):
        if target.get("shot") is not None:
            target["shot"]["status"] = "failed" if record else "reviewing"
        self.emit("needs_attention")
        raise ProductionNeedsDecision(
            "已有视频仅复审：未通过当前审核或输入已变化；原视频保留，需人工处理，不会自动重制。",
            "existing_video_review_failed" if record else "existing_video_review_required",
            {"asset": target["key"], "stage": "video_review",
             "take": (record or {}).get("take"), "path": (receipt or {}).get("path"),
             "assessment": (record or {}).get("qa", {}),
             "retained_receipts": [str(path.relative_to(ROOT)) for path in sorted(self.existing_video_results)
                                   if path.parent.parent == target["folder"]],
             "resume_action": "review_existing_assets_only", "regeneration_allowed": False})

    def _reject_upstream_media(self, target, receipt):
        return receipt.get("sha256") in target.get("_rejected_media_sha256", ())

    def _remember_pre_submit_stop(self, worker, target, record, error):
        from .gpu_client import RemoteComfyWorker
        proof = error.details.get('gpu_not_submitted') or {}
        folder = target['folder'] / record['take']
        if (not isinstance(proof, dict) or not isinstance(worker, RemoteComfyWorker)
                or proof.get('stage') != 'before_submission_intent'
                or proof.get('destination') != str(folder.resolve())
                or proof.get('service') != worker.client.base
                or not re.fullmatch(r'[a-f0-9]{64}', str(proof.get('request_identity', '')))
                or not re.fullmatch(r'[a-f0-9]{64}', str(proof.get('workload_sha256', '')))
                or (folder / 'submission.json').exists() or (folder / 'result.json').exists()):
            return False
        record['prepared_only'] = True
        record.setdefault('pre_submit_stops', []).append(copy.deepcopy(proof))
        return True

    def _unfinished_candidate(self, target):
        self._guard_remote_submissions(target)
        for record in reversed(self._attempts(target)):
            folder = target["folder"] / record["take"]
            result = folder / "result.json"
            if self.strategy_recovery and record.get('state') == 'rendering' and record.get('prepared_only') is True and not (folder / 'submission.json').exists() and not result.exists():
                target['resume_attempt'] = record
                return None
            if (self.h3_automatic and target["kind"] == "video" and
                    (record.get("state") == "prompt_preparing" or
                     record.get("state") == "rendering" and record.get("h3_prompt") and
                     not (folder / "submission.json").exists() and not result.exists())):
                target["resume_attempt"] = record
                return None
            reassess_failed = (
                record.get("state") == "failed" and self._review_required(target)
                and record.get("qa", {}).get("technical", {}).get("status") == "pass"
                and bool(record.get("review_signature"))
                and record["review_signature"] != target["review_signature"])
            if (record.get("state") in ("generated", "reviewing", "semantic_pending", "passed", "rendering")
                    or reassess_failed) and result.exists():
                media = read_json(result)
                if reassess_failed and not _matching_source(media, target):
                    continue
                _receipt_path(media)
                if self._reject_upstream_media(target, media):
                    continue
                return record, media
            if uses_bound(self, target) and record.get("state") == "rendering":
                target["resume_attempt"] = record
                return None
            if self._remote_video(target) and record.get("state") == "rendering":
                target["resume_attempt"] = record
                return None
            if record.get("state") == "rendering" and (folder / "submission.json").exists():
                submission = read_json(folder / "submission.json")
                if submission.get("gpu_service_job_id") and not (folder / "submission_error.json").exists():
                    from .gpu_client import resume_remote_comfy
                    try:
                        resume_remote_comfy(self.config, folder, self.budget, self.control,
                                            event_callback=lambda event: self.emit("remote_gpu", remote_gpu_execution=event))
                    except RuntimeError:
                        if not (folder / "submission_error.json").exists():
                            raise
                        record.update(state="failed", error="Remote GPU execution has a terminal failure receipt")
                        self._save_attempt(target, record)
                        continue
                    media = read_json(result)
                    _receipt_path(media)
                    return record, media
                history = read_json(folder / "history.json") if (folder / "history.json").exists() else {}
                if history.get("status", {}).get("status_str") == "error" or (folder / "submission_error.json").exists():
                    record.update(state="failed", error="Previous generation failed with a recorded Comfy error")
                    self._save_attempt(target, record)
                else:
                    raise ProductionNeedsDecision("A previous GPU submission has no proven terminal result",
                                                  "unresolved_submission",
                                                  {"receipt": str((folder / "submission.json").relative_to(ROOT))})
            elif self.strategy_recovery and record.get('state') == 'rendering':
                raise ProductionNeedsDecision('已有生成缺少可核对的提交回执，未重复提交',
                                              'unresolved_submission', {'asset': target['key'], 'take': record['take']})
            elif record.get("state") == "rendering" and self.config.get("gpu_service"):
                # A pause before the main submission may leave an accepted
                # repair-keyframe job. Re-enter the same take so its client can
                # reconcile that job before submitting the main generation.
                target["resume_attempt"] = record
                return None
        return None

    def _asset_attempt_ceiling(self, target):
        from .production_continuation import ceiling
        return ceiling(self.project, {**self.config, 'max_attempts': self.max_attempts}, target)

    def _quota_attempts(self, target):
        if not self.strategy_recovery:
            return self._attempts(target)
        lineage = (self.production.get('recovery_lineage') or {}).get(target['kind'] + ':' + target['key'], [])
        result = []
        for path in sorted(target['folder'].glob('take_*/attempt.json')):
            record = read_json(path)
            if (record.get('stage') == target['kind'] and
                    (_matching_source(record, target) or record.get('source_signature') in lineage
                     or record.get('production_strategy'))):
                record['_file'] = str(path)
                result.append(record)
        return result

    def _verify_frame_compatibility(self, case, target):
        from . import production_strategy as strategy
        from .production_recovery import _STRATEGY_SHOT_FIELDS
        from .creative_evidence_io import digest
        frame = _receipt_path(target['shot']['image'])
        checksum = sha256(frame)
        approved = {k: copy.deepcopy(target['shot'][k]) for k in _STRATEGY_SHOT_FIELDS if k in target['shot']}
        model_config, models = strategy._models(self.config, 'video')
        identity = {'case_id': case['id'], 'frame_sha256': checksum, 'approved_shot': approved,
                    'models': models, 'protocol': 'same-frame-schema-after-upstream.v1'}
        base = case['path'].parent / 'compatibility' / digest({'asset': target['key']})
        checked_target = {**target, 'references': [frame]}
        def check():
            self.boundary()
            if sha256(frame) != checksum or digest({k: copy.deepcopy(target['shot'][k]) for k in _STRATEGY_SHOT_FIELDS if k in target['shot']}) != digest(approved):
                raise ProductionNeedsDecision('上游首帧或批准要求在复验期间变化', 'production_handoff_corrupt')
        evidence = strategy._once(base, 'frame', identity,
            lambda: strategy._observe(self.project, model_config, checked_target,
                                       {'sha256': checksum}, base / 'vision'), check)
        strategy._check_observation(evidence, target['shot'])
        path = base / ('frame-' + digest(identity) + '.json')
        return {**evidence, 'receipt_path': str(path), 'receipt_sha256': sha256(path)}

    def _producer_retake(self, target, previous):
        """A new explicit render revision is not a failure of the old approval.

        Lifetime quota/history still uses all previous attempts. Only the first
        request for a newer revision skips a diagnosis that does not exist.
        """
        if (not previous or target.get('upstream_return') or target.get('_upstream_assessment')
                or target.get('_frame_observation_assessment')
                or (self.production.get('upstream_returns') or {}).get((target.get('shot') or {}).get('id'))):
            return False
        last = previous[-1]
        revision = (target.get('shot') or {}).get('render_revisions', {}).get(target['kind'], 0)
        old_revision = last.get('render_revision', (last.get('generation_plan') or {}).get(
            'source', {}).get('approved_shot', {}).get('render_revisions', {}).get(target['kind'], 0))
        return (type(revision) is int and type(old_revision) is int and revision > old_revision >= 0
                and last.get('state') == 'passed' and last.get('qa', {}).get('status') == 'pass'
                and not _matching_source(last, target))

    def _prepare_system_repair(self, target, previous=None):
        """Execute upstream handoffs before acquiring the next media worker."""
        if not self.strategy_recovery or target.get('_system_repair_context'):
            return
        previous = previous if previous is not None else self._quota_attempts(target)
        previous = [r for r in previous if r.get('state') != 'prompt_preparing']
        if self._producer_retake(target, previous):
            return
        if not previous or (self._asset_attempt_ceiling(target) is not None and len(previous) >= self._asset_attempt_ceiling(target)):
            return
        from .production_recovery import prepare_repair
        from .production_handoffs import start, advance, role, upstream_receipt
        self.boundary()
        failed = previous[-1]
        assessment = target.get('_upstream_assessment') or failed.get('qa') or {
            'status': 'fail', 'issues': [{'code': 'generation', 'severity': 'blocking'}]}
        def checked():
            return {**target, 'first_frame_assessment': copy.deepcopy(target.get('_frame_observation_assessment') or
                    (target.get('shot') or {}).get('image', {}).get('qa'))}
        returned = (self.production.get('upstream_returns') or {}).get((target.get('shot') or {}).get('id')) if target['kind'] == 'video' else None
        if returned:
            from .production_handoffs import load
            case = load(self, returned['case_id'])
            shot = target['shot']
            with role(self, case, 'handing_off', 'image_generator'):
                missing = [r for r in self._shot_render_roles(shot) if r not in self.assets.get('characters', {})]
                self._characters_and_images(roles=missing, shot_ids=[shot['id']], upstream_return=returned)
            if shot['image']['sha256'] in returned['rejected_media_sha256']:
                raise ProductionNeedsDecision('首帧返修仍为已拒绝的相同像素，未宣告上游修复成功', 'production_upstream_unchanged')
            compatibility = self._verify_frame_compatibility(case, {**target, 'references': [_receipt_path(shot['image'])]})
            refreshed = self._video_target(shot)
            refreshed['first_frame_assessment'] = copy.deepcopy(shot['image'].get('qa'))
            refreshed['first_frame_compatibility'] = compatibility
            refreshed['upstream_resolution'] = upstream_receipt(self, case, refreshed, returned['operation'],
                returned['previous_video_source_signature'])
            refreshed['_frame_handoff_count'] = 1
            target.clear(); target.update(refreshed)
            self.production['upstream_returns'].pop(shot['id'], None)
            self.emit('production_repair')
            directed = prepare_repair(self.project, self.config, checked(), assessment, previous_attempt=failed, root=ROOT)
            advance(self, case, 'resolved', 'visual_critic', artifact=refreshed['upstream_resolution'])
            target['_system_repair_context'] = (directed, start(self, target, directed) if directed.get('id') else None)
            return
        directed = prepare_repair(self.project, self.config, checked(), assessment, previous_attempt=failed, root=ROOT)
        if not directed.get('id'):
            target['_system_repair_context'] = (directed, None)
            return
        case = start(self, target, directed)
        if directed['action'] == 'request_upstream':
            operations = directed.get('required_handoffs') or []
            operation = operations[0] if operations else None
            if operation in {'review_first_frame', 'revise_first_frame', 'revise_reference'} and target['kind'] == 'video' and target.get('shot'):
                old_source = failed.get('source_signature') or target['signature']
                shot = target['shot']
                lineages = self.production.setdefault('recovery_lineage', {})
                key = target['kind'] + ':' + target['key']
                lineages[key] = list(dict.fromkeys([*lineages.get(key, []), old_source, target['signature']]))
                from .production_recovery import _STRATEGY_SHOT_FIELDS
                from .creative_evidence_io import digest
                self.production.setdefault('upstream_contracts', {})[shot['id']] = digest({k: copy.deepcopy(shot[k]) for k in _STRATEGY_SHOT_FIELDS if k in shot})
                # Persist the inherited quota before an upstream media write or
                # model call can pause, crash or return a different frame.
                self.emit('production_repair')
                needs_pixels = operation != 'review_first_frame'
                diagnosis = copy.deepcopy(target.get('_frame_observation_assessment') or shot['image'].get('qa') or {})
                if operation == 'review_first_frame':
                    from .production_handoffs import review_first_frame
                    needs_pixels = review_first_frame(self, case, target) != 'pass'
                    if not needs_pixels:
                        try:
                            self._verify_frame_compatibility(case, target)
                        except ProductionNeedsDecision as exc:
                            if exc.reason != 'generation_frame_conflict':
                                raise
                            needs_pixels = True
                            diagnosis = {'scope': 'independent_first_frame_compatibility',
                                         'observation': copy.deepcopy(exc.details.get('observation') or {})}
                if needs_pixels:
                    rejected = copy.deepcopy(shot['image'])
                    returned = {
                        'case_id': case['id'], 'operation': 'revise_first_frame',
                        'previous_image_source_signature': rejected.get('source_signature'),
                        'previous_video_source_signature': old_source,
                        'rejected_media_sha256': [rejected['sha256']],
                        'original_qa': copy.deepcopy(rejected.get('qa')),
                        'assessment': {'status': 'fail', 'issues': [{
                            'code': 'composition', 'severity': 'blocking',
                            'detail': '真实首帧未满足批准的起始状态，按独立像素诊断修订。',
                            'evidence': {'media_sha256': rejected['sha256'], 'diagnosis': diagnosis}}]}}
                    self.production.setdefault('upstream_returns', {})[shot['id']] = returned
                    self.emit('production_repair')
                    with role(self, case, 'handing_off', 'image_generator'):
                        prior_policy = self.review_policy
                        try:
                            self.review_policy = fingerprint({'policy': prior_policy, 'upstream_case': case['id']})
                            missing = [r for r in self._shot_render_roles(shot) if r not in self.assets.get('characters', {})]
                            self._characters_and_images(roles=missing, shot_ids=[shot['id']], upstream_return=returned)
                        finally:
                            self.review_policy = prior_policy
                    if shot['image']['sha256'] in returned['rejected_media_sha256']:
                        raise ProductionNeedsDecision('首帧返修仍为已拒绝的相同像素，未宣告上游修复成功', 'production_upstream_unchanged')
                compatibility = self._verify_frame_compatibility(case, {**target, 'references': [_receipt_path(shot['image'])]})
                refreshed = self._video_target(shot)
                refreshed['first_frame_assessment'] = copy.deepcopy(shot['image'].get('qa'))
                refreshed['first_frame_compatibility'] = compatibility
                receipt = upstream_receipt(self, case, refreshed, operation, old_source)
                refreshed['upstream_resolution'] = receipt
                self.production.setdefault('upstream_returns', {}).pop(shot['id'], None)
                if target.get('_upstream_assessment'):
                    refreshed['_upstream_assessment'] = copy.deepcopy(target['_upstream_assessment'])
                if target.get('_frame_handoff_count'):
                    refreshed['_frame_handoff_count'] = target['_frame_handoff_count']
                target.clear(); target.update(refreshed)
                self.emit('production_repair')
                directed = prepare_repair(self.project, self.config, checked(), assessment, previous_attempt=failed, root=ROOT)
                if directed.get('id'):
                    advance(self, case, 'resolved', 'visual_critic', artifact=receipt)
                    case = start(self, target, directed)
        target['_system_repair_context'] = (directed, case)

    def _assert_model_capabilities(self, target, *, references=None, prompt=None):
        from .model_capabilities import validate_target, CapabilityError
        try:
            width, height = self.image_size if target['kind'] == 'image' else self.video_size
            return validate_target(self.project, self.config, target, width, height,
                                   references=references, prompt=prompt)
        except CapabilityError as exc:
            raise ProductionNeedsDecision(str(exc), exc.code, exc.details) from None

    def _new_attempt(self, target):
        capability_receipt = self._assert_model_capabilities(target)
        from .drama_resources import ensure
        ensure(self.project, self.config, media=target["kind"])
        if self._review_only_video(target):
            self._stop_existing_video(target)
        self._team_admit_target(target)
        previous = [record for record in self._quota_attempts(target) if record.get("state") != "prompt_preparing"]
        if self._asset_attempt_ceiling(target) is not None and len(previous) >= self._asset_attempt_ceiling(target):
            if target.get("shot") is not None:
                target["shot"]["status"] = "failed"
            self.emit("needs_attention")
            if self.autonomous_recovery:
                self.production['recovery'] = {'status': 'stopped', 'reason': 'retry_limit',
                    'asset': target['key'], 'stage': target['kind'], 'attempts_used': len(previous),
                    'attempts_limit': self._asset_attempt_ceiling(target), 'message': '本制作对象的授权尝试次数已用尽；保留结果与费用，系统已停止新增生成。'}
                self.emit('needs_attention')
            raise ProductionNeedsDecision(f"{target['key']} 的 {target['kind']} 已用完 {self._asset_attempt_ceiling(target)} 次授权尝试；已有成果和费用已保留。需调整尝试次数授权，直接继续不会获得新额度。" if self.autonomous_recovery else "A production asset exhausted its authorized retry limit",
                                          "retry_limit",
                                          {"asset": target["key"], "stage": target["kind"],
                                           "attempts": len(previous),
                                           **({'attempts_used': len(previous), 'attempts_limit': self._asset_attempt_ceiling(target)}
                                              if self.autonomous_recovery else {})})
        indexes = [int(p.name[5:]) for p in target["folder"].glob("take_*")
                   if p.is_dir() and p.name[5:].isdigit()]
        take = f"take_{max(indexes, default=0) + 1:03d}"
        folder = target["folder"] / take
        number = len(previous) + 1
        producer_retake = self._producer_retake(target, previous)
        repair = _grounded_repair_plan(
            previous[-1].get("qa", {"issues": [{"code": "generation", "detail": previous[-1].get("error", "")}]}),
            target["kind"], number, target.get("shot"), target.get("reference_roles", ()),
            target_prompt=target["prompt"], review_context=target.get("review_context")) if previous and not producer_retake else {}
        directed, case, compiled_strategy = None, None, None
        if ((previous and not producer_retake) or target.get("upstream_return")) and self.autonomous_recovery:
            from .production_recovery import prepare_repair, applicable
            self.boundary()
            checked_target = {**target, 'first_frame_assessment': copy.deepcopy(
                (target.get('shot') or {}).get('image', {}).get('qa'))}
            failed = previous[-1] if previous else {}
            assessment = target.get('_upstream_assessment') or failed.get('qa') or {'status': 'fail', 'issues': [{'code': 'generation', 'severity': 'blocking'}]}
            self._prepare_system_repair(target, previous)
            context = target.pop('_system_repair_context', None)
            checked_target = {**target, 'first_frame_assessment': copy.deepcopy(target.get('_frame_observation_assessment') or
                            (target.get('shot') or {}).get('image', {}).get('qa'))}
            if context:
                directed, case = context
            else:
                directed = prepare_repair(self.project, self.config, checked_target, assessment,
                                          previous_attempt=failed, root=ROOT)
            if self.strategy_recovery and case is None and directed.get('id'):
                from .production_handoffs import start
                case = start(self, target, directed)
            self.boundary()
            state = {'status': 'repairing', 'asset': target['key'], 'stage': target['kind'],
                     'action': directed['action'], 'reason': directed['reason'],
                     'record_id': directed.get('id'), 'attempts_used': len(previous),
                     'attempts_limit': self._asset_attempt_ceiling(target)}
            if directed['action'] in {'stop', 'request_upstream'}:
                explanations = {
                    'approved_inputs_require_upstream_revision': '批准材料存在矛盾或模型能力不足，需要核对上游材料或执行能力。',
                    'actual_reference_evidence_missing': '缺少实际参考素材，需要补齐引用证据。',
                    'first_frame_requires_independent_upstream_repair': '当前首帧未通过独立审核，需要先修复并复核首帧。',
                    'first_frame_review_unverified': '首帧审核证据不足，需要补审原首帧。',
                    'paid_repair_authorization_unverified': '供应商返修缺少可验证的金额授权，不能用 GPU 分钟代替付费许可。',
                    'recovery_has_no_new_directed_change': '已有定向约束未解决问题，系统不再重复相同返修。',
                    'recovery_cycle_limit': '已达到自主返修计划上限，需要核对失败证据后调整方案。',
                }
                explanation = explanations.get(directed['reason'], '当前材料、证据或授权不足以安全继续。')
                state.update(status='stopped', message=f"{target['key']} · {explanation} 已保留原结果及诊断证据（{directed['reason']}）。")
                self.production['recovery'] = state
                self.emit('needs_attention')
                if case:
                    from .production_handoffs import advance
                    advance(self, case, 'stopped', directed.get('assigned_role', 'storyboard_director'), reason=state['message'])
                raise ProductionNeedsDecision(state['message'], 'production_recovery_' + directed['action'], state)
            if not applicable(directed, self.project, self.config, checked_target, assessment,
                              previous_attempt=failed, root=ROOT):
                raise ProductionNeedsDecision('返修材料或执行条件已改变，原计划仅保留为证据，未新增生成', 'production_recovery_stale')
            repair.update(prompt_prefix=directed['prompt_appendix'], prompt_suffix='',
                          regenerate_keyframe=False,
                          recovery_record=directed['id'], recovery_action=directed['action'])
            self.production['recovery'] = state
            self.emit('production_repair')
            if self.strategy_recovery:
                from .production_strategy import compile_strategy
                from .production_handoffs import role
                try:
                    with role(self, case, 'planning', 'storyboard_director'):
                        compiled_strategy = compile_strategy(self.project, self.config, checked_target, directed, checkpoint=self.boundary, root=ROOT)
                except ProductionNeedsDecision as exc:
                    if exc.reason != 'generation_frame_conflict' or target.get('_frame_handoff_count', 0) >= 1:
                        raise
                    observation = exc.details.get('observation') or {}
                    target['_frame_observation_assessment'] = {
                        'status': 'fail' if observation.get('alignment') == 'conflict' and observation.get('conflicts') else 'unavailable',
                        'scope': 'independent_first_frame_compatibility', 'observation': copy.deepcopy(observation)}
                    target['_frame_handoff_count'] = 1
                    target.pop('_system_repair_context', None)
                    self._prepare_system_repair(target, previous)
                    return self._new_attempt(target)
        seed = (int(fingerprint({"project": self.ident, "asset": target["key"]})[:8], 16)
                + int(repair.get("seed_delta", 0)) + number - 1) % (2**31)
        retake = (target.get("shot") or {}).get("render_revisions", {}).get(target["kind"], 0) or self.project.get("team_character_revisions", {}).get(target.get("character"), 0)
        if retake:
            seed = (seed + int(fingerprint({"retake": retake, "kind": target["kind"]})[:8], 16)) % (2**31)
        prompt = ((repair["prompt_prefix"] + "\n\n" + target["prompt"]) if repair.get("prompt_prefix")
                  else target["prompt"] + (" " + repair["prompt_suffix"] if repair.get("prompt_suffix") else ""))
        record = {"take": take, "attempt": number, "stage": target["kind"], "state": "rendering",
                  "source_signature": target["signature"], "prompt": prompt,
                  "prompt_version": number, "seed": seed, "repair": repair,
                  "model": self.config.get("image_connection", {}).get("model", "FLUX.2-klein-4B") if target["kind"] == "image" else self.video_connection["model"],
                  "parameters": {"width": (self.image_size if target["kind"] == "image" else self.video_size)[0],
                                 "height": (self.image_size if target["kind"] == "image" else self.video_size)[1],
                                 "seconds": target.get("seconds"), "fps": 24,
                                 "steps": 4 if target["kind"] == "image" else 20},
                  "references": [media_receipt(p) for p in target["references"]],
                  "started_at": time.time()}
        if capability_receipt:
            record['model_capabilities'] = capability_receipt
        if self.strategy_recovery:
            record['prepared_only'] = True
        if directed:
            record['production_recovery'] = {'record_id': directed['id'], 'action': directed['action'],
                                             'attempts_used_before': len(previous), 'attempt_limit': self._asset_attempt_ceiling(target)}
        if target["kind"] == "video" and self.video_connection["provider"] == "comfyui_h3":
            from studio.h3 import frame_count, recipe
            selected = recipe(self.video_connection)
            record.update(provider="comfyui_h3", profile=self.video_connection["profile"], model_revision=selected["revision"])
            record["parameters"].update(selected, frames=frame_count(target["seconds"]), gpu_count=2)
        if explicit_profile(self.project):
            record["production_profile"] = resolve_profile(self.project)
            record["production_profile_signature"] = profile_signature(self.project)
        if target.get("shot"):
            record.update(plan_signature=plan_signature(self.project, target["shot"], target["kind"]),
                          render_revision=target['shot'].get('render_revisions', {}).get(target['kind'], 0),
                          shot_revision=target["shot"].get("shot_revision", 1),
                          production_revision=self.project.get("production_revision", 1))
        if producer_retake:
            record['producer_retake'] = {'previous_take': previous[-1]['take'],
                'previous_source_signature': previous[-1].get('source_signature'),
                'render_revision': record['render_revision'], 'attempts_used_before': len(previous),
                'reason': 'explicit_new_render_revision_after_approved_take'}
            self.production['recovery'] = {'status': 'repairing', 'asset': target['key'],
                'stage': target['kind'], 'owner': 'image_generator' if target['kind'] == 'image' else 'video_generator',
                'reason': 'producer_requested_new_render_revision',
                'message': '按出品人明确的新版本要求制作，原审核、素材和尝试历史保留。'}
        if target.get("asset_references"):
            record["asset_references"] = copy.deepcopy(target["asset_references"])
        if target.get('shot_continuity'):
            record['shot_continuity'] = copy.deepcopy(target['shot_continuity'])
        if self._remote_video(target):
            record.update(provider="minimax", profile=self.video_connection["profile"],
                          model=self.video_connection["model"],
                          parameters={"duration": self.video_connection["duration"],
                                      "resolution": self.video_connection["resolution"],
                                      "timeline_seconds": target.get("seconds"), "timeline_fps": 24})
        preparation_folder = False
        if target.get("generation_plan"):
            record["generation_plan"] = copy.deepcopy(target["generation_plan"])
            # Keep the adapter's compiled prefix intact, including H3's timing
            # format. Grounded repair constraints cannot replace approved facts.
            record["prompt"] = target["prompt"] + "\n" + (repair.get("prompt_prefix") or repair.get("prompt_suffix") or "")
            if target["kind"] == "video" and repair.get("regenerate_keyframe"):
                raise ProductionNeedsDecision("视频返修要求更换首帧；请先定向返修并确认图片，再读取新首帧编译视频", "generation_frame_revision_required")
        if compiled_strategy:
            record.update(prompt=compiled_strategy['parameters']['prompt'], production_strategy=compiled_strategy,
                          media_handoff=case['id'])
            record['repair'].update(strategy=directed['strategy'], compiled_strategy_signature=compiled_strategy['signature'])
            from .production_handoffs import advance
            advance(self, case, 'repairing', 'video_generator' if target['kind'] == 'video' else 'image_generator',
                    artifact={'strategy_signature': compiled_strategy['signature'], 'take': take})
        from .shot_continuity import bind_prompt
        record['prompt'] = bind_prompt(record['prompt'], target.get('shot_continuity'))
        if self.h3_automatic and target["kind"] == "video" and not target.get("generation_plan") and not compiled_strategy:
            record["state"] = "prompt_preparing"
            if repair.get("regenerate_keyframe"):
                # A replacement first frame must exist before its prompt is
                # compiled. Keep this preparation resumable and uncounted.
                folder.mkdir(parents=True, exist_ok=False)
                preparation_folder = True
                self._save_attempt(target, record)
            self._finish_h3_prompt(target, record)
        if not preparation_folder:
            folder.mkdir(parents=True, exist_ok=False)
        self._team_begin_target(target, record)
        self._save_attempt(target, record)
        if record.get("generation_plan"):
            from .generation_pipeline import request_receipt
            request_receipt(self.project, target, record)
        return record

    def _finish_h3_prompt(self, target, record):
        """Prepare text and any replacement frame before leasing H3 GPUs."""
        from .h3_prompting import compile_prompt
        self._guard_remote_submissions(target)
        self._team_admit_target(target)
        self.boundary()
        frame = self._remote_first_frame(target, record)
        references = [media_receipt(frame), *[media_receipt(p) for p in target["references"][1:]]]
        approved_frame = (target.get("shot") or {}).get("image", {})
        if approved_frame.get("sha256") == references[0]["sha256"] and approved_frame.get("qa"):
            references[0]["qa"] = copy.deepcopy(approved_frame["qa"])
        previous = [p for p in self._attempts(target) if p["take"] != record["take"] and p.get("state") != "prompt_preparing"]
        request_key = fingerprint({"source": target["signature"], "attempt": record["attempt"],
                                   "reference": references, "repair": record.get("repair")})[:24]
        self.emit("h3_prompt_preparation")
        compiled = compile_prompt(self.project, {**copy.deepcopy(target.get("shot") or {}), "cast": self.cast},
            config=self.config.get("h3_prompt_config", {}), references=references,
            style=self.style_prompt, seconds=target["seconds"], repair=record.get("repair"),
            previous_prompt=previous[-1].get("prompt") if previous else None,
            output_dir=target["folder"] / "prompting" / request_key, attempt=record["attempt"])
        self.boundary()
        self._team_admit_target(target)
        record.update(prompt=compiled["prompt"], h3_prompt=compiled["receipt"],
                      references=references, state="rendering")
        from .shot_continuity import bind_prompt
        record['prompt'] = bind_prompt(record['prompt'], target.get('shot_continuity'))

    def _render_targets(self, targets, phase):
        if targets and self._team_target(targets[0]):
            kind = self._team_target(targets[0])[0]
            targets = team_control.ordered(self.ident, kind, targets, key=lambda value: self._team_target(value)[1])
        if targets and targets[0]["kind"] == "image" and self.config.get("image_connection", {}).get("enabled") is False:
            raise ProductionNeedsDecision("请先配置并启用图像连接", "image_unconfigured")
        pending = []
        for target in targets:
            wait = self.production.get('review_transport_recovery') or {}
            item = next((i for i in wait.get('pending', []) if i.get('asset') == target['key'] and i.get('stage') == target['kind']), None) if wait.get('status') == 'waiting' else None
            if item:
                target['review_candidate'] = self._transport_candidate(target, item)
                pending.append(target)
                continue
            selected = self._valid_selection(target)
            if selected and self._reject_upstream_media(target, selected):
                selected = None
            if selected and self._review_current(target, selected):
                identity = self._team_target(target)
                if identity:
                    team_ledger.mark_reused(self.ident, *identity, artifact=selected,
                                            input_version=target["signature"],
                                            review_status=selected.get("qa", {}).get("status"))
                self._attach(target, selected)
            else:
                if selected:
                    target["review_candidate"] = (self._receipt_attempt(target, selected), selected)
                pending.append(target)
        self.emit(phase)
        while pending:
            self.boundary()
            ready, generate = [], []
            for target in pending:
                candidate = target.pop("review_candidate", None) or self._unfinished_candidate(target)
                if candidate:
                    ready.append((target, *candidate))
                else:
                    if self._review_only_video(target):
                        failed = next((row for row in reversed(self._attempts(target))
                                       if row.get("state") == "failed"), None)
                        self._stop_existing_video(target, failed, (failed or {}).get("output"))
                    generate.append(target)
            waiting = self.production.get('review_transport_recovery') or {}
            if waiting.get('status') == 'waiting':
                if generate:
                    raise ProductionNeedsDecision('原媒体补审尚未完成，未新增生成', 'semantic_unavailable',
                        {'pending': waiting.get('pending', []), 'resume_action': 'review_existing_assets_only'})
            for target in generate:
                if not target.get('resume_attempt'):
                    self._prepare_system_repair(target)
                    if self.strategy_recovery:
                        target['resume_attempt'] = self._new_attempt(target)
            from .bound_media import uses_bound, render_targets
            bound = [target for target in generate if uses_bound(self, target)]
            if bound:
                ready.extend(render_targets(self, bound, phase))
            generate = [target for target in generate if not uses_bound(self, target)]
            remote_targets = [target for target in generate if self._remote_video(target)]
            if remote_targets:
                ready.extend(self._render_remote_targets(remote_targets, phase))
            generate = [target for target in generate if not self._remote_video(target)]
            if generate:
                self.boundary(gpu=True)
                h3 = self.video_connection["provider"] == "comfyui_h3" and any(t["kind"] == "video" for t in generate)
                if h3 and self.h3_automatic:
                    for target in generate:
                        if target["kind"] != "video" or target.get("generation_plan"):
                            continue
                        record = target.get("resume_attempt")
                        if record is None:
                            target["resume_attempt"] = self._new_attempt(target)
                        elif record.get("state") == "prompt_preparing":
                            self._finish_h3_prompt(target, record)
                            self._team_begin_target(target, record)
                            self._save_attempt(target, record)
                with self.scheduler.worker(self.slug + "_" + phase, self.control, **({"h3": True} if h3 else {})) as worker:
                    for target in generate:
                        self._team_admit_target(target)
                        self.boundary(gpu=True)
                        record = target.pop("resume_attempt", None) or self._new_attempt(target)
                        self._team_begin_target(target, record)
                        shot = target.get("shot")
                        if shot is not None:
                            shot["status"] = "rendering" if record["attempt"] == 1 else "retrying"
                        self.emit(phase)
                        started = time.monotonic()
                        try:
                            references = target["references"]
                            if record.get('production_strategy'):
                                from .production_strategy import render_references
                                compiled = record['production_strategy']
                                references = render_references(target, compiled['parameters'])
                                if len(references) != len(target['references']) and not (compiled.get('visual_contract_verification') or {}).get('passed'):
                                    raise ProductionNeedsDecision('逐镜参考选择尚未通过独立契约核对', 'production_strategy_contract_conflict')
                                record['render_references'] = [media_receipt(p) for p in references]
                            self._assert_model_capabilities(target, references=references, prompt=record['prompt'])
                            names = [worker.copy_input(p) for p in references]
                            if target["kind"] == "image":
                                graph = image_request(self.config.get("image_connection", {}), record["prompt"], record["seed"], *self.image_size,
                                                   references=names, prefix=self.slug + "/" + target["key"])
                            else:
                                if self.h3_automatic and record.get("repair_keyframe"):
                                    names[0] = worker.copy_input(_receipt_path(record["repair_keyframe"]))
                                elif record.get("repair", {}).get("regenerate_keyframe"):
                                    if team_control.is_locked(self.ident, "image", target["shot"]["id"]):
                                        raise ProductionNeedsDecision(
                                            "视频返修需要更换已锁定首帧，请先解锁或调整返修要求", "team_locked_keyframe",
                                            {"shot_id": target["shot"]["id"], "task_id": "image:" + target["shot"]["id"]})
                                    # A diagnosed identity/composition failure gets a genuinely new
                                    # first frame, rather than merely a differently worded video prompt.
                                    shot_refs = list(target["references"])
                                    if not any(ref.get("library_key") for ref in target.get("asset_references", [])):
                                        shot_refs += [_receipt_path(value) for value in self.assets.get("characters", {}).values()]
                                    material = any(ref.get("library_key") for ref in target.get("asset_references", []))
                                    keyframe_prompt = ((record["repair"].get("prompt_prefix") or
                                                        record["repair"].get("prompt_suffix", "")) + "\n" +
                                                       self.style_prompt + " " + target["shot"]["prompt"] +
                                                       " Preserve each corresponding character identity and wardrobe.")
                                    from .shot_continuity import bind_prompt
                                    keyframe_prompt = bind_prompt(keyframe_prompt, target.get('shot_continuity'))
                                    from .model_capabilities import enabled as capability_contract
                                    repair_refs = shot_refs if material or capability_contract(self.project) else shot_refs[:4]
                                    self._assert_model_capabilities({**target, 'kind': 'image', 'prompt': keyframe_prompt, 'references': repair_refs})
                                    repair_names = [worker.copy_input(p) for p in repair_refs]

                                    repair_graph = image_request(self.config.get("image_connection", {}), keyframe_prompt, record["seed"], *self.image_size,
                                                              references=repair_names,
                                                              prefix=self.slug + "/repair/" + target["key"])
                                    repair_path = worker.execute(repair_graph, target["folder"] / record["take"] / "repair_keyframe",
                                                                 "image", timeout=self.budget.timeout(1800, 1))
                                    image_qa = inspect_image(repair_path, self.image_size)
                                    if image_qa["status"] != "pass":
                                        raise RuntimeError("Replacement first frame failed technical QA")
                                    record["repair_keyframe"] = media_receipt(repair_path, technical_qa=image_qa)
                                    names[0] = worker.copy_input(repair_path)
                                profile = None
                                if not h3 and self.config.get("video_profile") == "fastwan":
                                    profile = read_json(ROOT / "workflows/fastwan_profile.json")
                                builder = h3_video if h3 else wan_video
                                graph = builder(record["prompt"], record["seed"], names[0],
                                                  *self.video_size, seconds=target["seconds"],
                                                  prefix=self.slug + "/" + target["key"], profile=self.video_connection if h3 else profile)
                            # Native H3 base sampling can exceed the legacy two-hour
                            # task cap. Keep the service cap and the approved project
                            # budget; count both leased GPUs when bounding local work.
                            task_limit = (1800 if target["kind"] == "image" else
                                          21600 if h3 and self.video_connection.get("quality") == "production" else 7200)
                            if self.strategy_recovery:
                                record['prepared_only'] = False
                                self._save_attempt(target, record)
                            path = worker.execute(graph, target["folder"] / record["take"], target["kind"],
                                                  timeout=self.budget.timeout(task_limit, 2 if h3 else 1))
                            receipt = read_json(path.parent / "result.json")
                            record.update(state="generated", output=receipt, elapsed_seconds=round(time.monotonic() - started, 3))
                            ready.append((target, record, receipt))
                        except (ProductionPaused, ProductionNeedsDecision) as exc:
                            if self.strategy_recovery:
                                self._remember_pre_submit_stop(worker, target, record, exc)
                            raise
                        except Exception as exc:
                            record.update(state="failed", error=f"{type(exc).__name__}: {str(exc)[:1500]}",
                                          elapsed_seconds=round(time.monotonic() - started, 3),
                                          qa={"status": "fail", "issues": [{"code": "generation", "detail": str(exc)[:500]}]})
                            if shot is not None:
                                shot["status"] = "retrying"
                        finally:
                            self._save_attempt(target, record)
                            self.emit(phase)
                        self._team_after_target(target)
            # The owned Comfy process and its GPU lease are closed before VLM evaluation.
            semantic_pending = []
            for target, record, receipt in ready:
                self._team_publish_target(target)
                with self._team_review_unit(target, record, receipt) as review_token:
                    self.boundary()
                    path = _receipt_path(receipt)
                    if self._reject_upstream_media(target, receipt):
                        raise ProductionNeedsDecision('上游返修返回了已被独立像素证据拒绝的同一首帧，未宣告修复成功',
                                                      'production_upstream_unchanged', {'asset': target['key'], 'sha256': receipt['sha256']})
                    shot = target.get("shot")
                    if shot is not None:
                        shot["status"] = "reviewing"
                    self.emit("quality_review")
                    if target["kind"] == "image":
                        technical = inspect_image(path, self.image_size)
                    else:
                        if self.video_connection["provider"] == "comfyui_h3":
                            from studio.h3 import frame_count
                            frames = frame_count(target["seconds"])
                        else:
                            frames = round(target["seconds"] * 24)
                            while frames % 4 != 1:
                                frames += 1
                        expected_seconds = target["seconds"] if self._remote_video(target) or uses_bound(self, target) else frames / 24
                        technical = inspect_video(path, expected_seconds, self.video_size)
                    required = self._review_required(target)
                    semantic = None
                    if required and technical["status"] == "pass":
                        semantic = self._semantic_assessment(target, record, path)
                    qa = combine(technical, semantic, semantic_required=required)
                    awaiting_review = qa["technical_passed"] and required and semantic.get("status") not in ("pass", "fail")
                    state = "semantic_pending" if awaiting_review else "passed" if qa["eligible_for_selection"] else "failed"
                    team_ledger.finish_unit(review_token, artifact=receipt,
                                            review_status=qa["status"],
                                            model=self.config.get("semantic_model") if required else "technical_checks",
                                            details={"qa": qa, "semantic_attempts": record.get("semantic_attempts", [])})
                    record.update(qa=qa, state=state, finished_at=time.time(),
                                  review_signature=target["review_signature"] if required else None)
                    saved = {**receipt, "source_signature": target["signature"], "qa": qa,
                             "attempt": record["attempt"], "take": record["take"],
                             "review_signature": target["review_signature"] if required else None,
                             "generation_kind": ("local_diffusion_image" if target["kind"] == "image" else
                                                 "cloud_diffusion_video" if self._remote_video(target) else "local_diffusion_video")}
                    if record.get("generation_plan"):
                        saved["generation_plan"] = copy.deepcopy(record["generation_plan"])
                    if record.get('production_strategy'):
                        saved['production_strategy'] = copy.deepcopy(record['production_strategy'])
                        saved['media_handoff'] = record['media_handoff']
                    if target["kind"] == "video":
                        saved.update(motion_method="generated_video", diffusion_video=True)
                        if self.h3_automatic:
                            record["generation_signature"] = target["signature"]
                            saved["generation_signature"] = target["signature"]
                            # Locks and provenance retain the original source
                            # receipt. The new canonical identity is separate.
                            if _matching_source(receipt, target):
                                saved["source_signature"] = receipt["source_signature"]
                        if record.get("h3_prompt"):
                            saved["h3_prompt"] = copy.deepcopy(record["h3_prompt"])
                        if self.video_connection["provider"] == "comfyui_h3":
                            from studio.h3 import MODEL, recipe
                            selected = recipe(self.video_connection)
                            saved.update(provider="comfyui_h3", model=MODEL, model_revision=selected["revision"],
                                         generation_parameters=selected)
                    if self.creative_contract:
                        saved["creative_contract_signature"] = fingerprint(self.creative_contract)
                        if target.get("style"):
                            from .media_contract import visual_direction
                            saved["visual_direction_signature"] = fingerprint(visual_direction(target["style"]))
                    if record.get("production_profile_signature"):
                        saved.update(production_profile_signature=record["production_profile_signature"])
                    if record.get("plan_signature"):
                        saved.update(plan_signature=record["plan_signature"], shot_revision=record.get("shot_revision"),
                                     production_revision=record.get("production_revision"))
                    if target.get("asset_references"):
                        saved["asset_references"] = copy.deepcopy(target["asset_references"])
                        record["output"] = copy.deepcopy(saved)
                    write_json(path.parent / "result.json", saved)
                    self._save_attempt(target, record)
                    self._team_publish_target(target)
                    identity = self._team_target(target)
                    if identity:
                        team_control.assert_publishable(self.ident, "video_review" if identity[0] == "video" else "image_review", identity[1],
                                                        input_version=target["review_signature"])
                    selection_file = target["folder"] / "selected.json"
                    if qa["eligible_for_selection"]:
                        if record.get('media_handoff'):
                            from .production_handoffs import load, advance
                            advance(self, load(self, record['media_handoff']), 'resolved', 'visual_critic', artifact=saved)
                        recovery = self.production.get('recovery') or {}
                        if recovery.get('asset') == target['key'] and recovery.get('stage') == target['kind']:
                            recovery.update(status='resolved', reviewed_take=record['take'],
                                            message='定向返修已通过独立审核，系统继续后续制作。')
                            self.production['recovery'] = recovery
                        self._team_publish_target(target)
                        identity = self._team_target(target)
                        if identity:
                            team_control.assert_publishable(self.ident, "video_review" if identity[0] == "video" else "image_review", identity[1],
                                                            input_version=target["review_signature"])
                        write_json(selection_file, saved)
                        self._attach(target, saved)
                        (target["folder"] / "pending_review.json").unlink(missing_ok=True)
                    else:
                        if record.get('media_handoff') and qa['status'] == 'fail':
                            from .production_handoffs import load, advance
                            advance(self, load(self, record['media_handoff']), 'diagnosing', 'storyboard_director',
                                    reason='独立复验未通过，保留失败证据并检查下一个不同方案。', artifact=saved)
                        # Retain the actual media and its receipt, but withdraw stale automatic approval.
                        if selection_file.exists() and read_json(selection_file).get("sha256") == saved["sha256"]:
                            selection_file.unlink()
                        if receipt.get("producer_selection"):
                            raise ProductionNeedsDecision("人工选定素材未通过当前复核，请重新审片；原文件保留", "manual_selection_review")
                        if shot is not None:
                            shot["qa"] = qa
                            shot["status"] = "reviewing" if awaiting_review else "retrying"
                            shot[target["kind"] + "_candidate"] = saved
                            shot[target["kind"] + "_path"] = saved["path"]
                            shot[target["kind"] + "_url"] = media_url(path)
                        if awaiting_review:
                            write_json(target["folder"] / "pending_review.json", saved)
                            semantic_pending.append({"asset": target["key"], "stage": target["kind"],
                                                     "path": saved["path"], "take": saved["take"],
                                                     "retryable_transport": semantic.get("retryable_transport") is True,
                                                     "detail": semantic.get("detail")})
                    self.emit(phase)
                    waiting = self.production.get('review_transport_recovery') or {}
                    if waiting.get('status') == 'waiting' and not awaiting_review:
                        remaining = [p for p in waiting.get('pending', []) if not
                                     (p.get('asset') == target['key'] and p.get('stage') == target['kind'])]
                        waiting['pending'] = remaining
                        if not remaining:
                            waiting.update(status='resolved', message='原媒体补审已完成，系统继续制作。')
                        self.production['review_transport_recovery'] = waiting
                    if qa["status"] == "fail" and self._review_only_video(target):
                        self._stop_existing_video(target, record, saved)
                    self._team_after_target(target)
                    identity = self._team_target(target)
                    if identity:
                        team_control.after_unit(self.ident, "video_review" if identity[0] == "video" else "image_review", identity[1])
            if semantic_pending:
                self.emit("semantic_review_pending")
                raise ProductionNeedsDecision("The semantic reviewer is unavailable; generated media is preserved for review-only resume",
                                              "semantic_unavailable",
                                              {"pending": semantic_pending, "review_attempts_per_asset": self.semantic_max_attempts,
                                               "resume_action": "review_existing_assets_only"})
            if getattr(self, '_transport_review_only', False):
                return [target['selected'] for target in targets if target.get('selected')]
            pending = [target for target in pending if "selected" not in target]
        return [target["selected"] for target in targets]

    def previews(self):
        from .drama_intent import enabled as whole_drama
        from .material_production import preview as material_preview
        prepared = material_preview(self)
        if prepared is not None:
            return prepared
        if self.creative_contract and (not self.semantic_enabled or self.evaluator is None):
            raise ProductionNeedsDecision("视觉候选必须先审核是否符合指定画风，请连接并启用画面质检。",
                                          "semantic_unavailable")
        if len(self.styles) != 3:
            raise ValueError("Visual development requires exactly three structured style candidates")
        role = resolve_profile(self.project)["characters"][0]["character_id"] if explicit_profile(self.project) else "female" if "female" in self.cast else next(iter(self.cast))
        targets = []
        for style in self.styles:
            ident = _safe_id(style["id"])
            style["preview_role"] = role
            prompt = (str(style.get("prompt") or style.get("description") or style.get("name")) +
                      "\nHalf-body character design reference, one person, uncluttered background, no written text. " +
                      self.cast[role])
            if self.creative_contract:
                prompt = style_prompt(self.project) + "\n" + prompt
            targets.append(self._target("style_" + ident, self.folder / "style_previews" / ident,
                                        prompt, style=style,
                                        **({"review": True, "review_context": {
                                            "id": "style_" + ident, "prompt": prompt,
                                            "review_purpose": "visual_direction_preview",
                                            "visual_direction": {key: style[key] for key in
                                                ("id", "name", "description", "prompt", "palette") if key in style}}}
                                           if self.creative_contract or whole_drama(self.project) else {})))
        self._render_targets(targets, "visual_previews")
        self.emit("visual_previews_complete", styles=self.styles)
        return self.styles

    def _approved_preview_reference(self):
        """Resolve the selected, project-owned preview before any character generation."""
        if self.selected_style is None:
            return None, None
        if self._material_preview_reference():
            return None, None  # A style image must never become a character identity reference.
        style = self.selected_style
        role = resolve_profile(self.project)["characters"][0]["character_id"] if explicit_profile(self.project) else "female" if "female" in self.cast else next(iter(self.cast))
        details = {"style_id": style.get("id"), "role": role}

        def invalid(problem):
            return ProductionNeedsDecision(
                "The approved character preview is missing or invalid; restore the approved image before production",
                "approved_preview_invalid", {**details, "problem": problem})

        if not style.get("id"):
            raise invalid("selected_style_missing")
        if style.get("preview_role", role) != role:
            raise invalid("preview_character_does_not_match")
        preview = style.get("preview")
        if not isinstance(preview, dict) or not preview.get("path") or not preview.get("sha256"):
            raise invalid("preview_receipt_missing")
        try:
            path = within(ROOT, preview["path"])
            approved_folder = within(self.folder, Path("style_previews") / _safe_id(style["id"]))
            if not path.is_relative_to(self.folder.resolve()) or not path.is_relative_to(approved_folder):
                raise invalid("preview_belongs_to_another_project_or_style")
            if not path.is_file():
                raise invalid("preview_file_missing")
            if sha256(path) != preview["sha256"]:
                raise invalid("preview_checksum_mismatch")
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise invalid("preview_path_unreadable: " + str(exc)[:300]) from exc
        return role, path

    def _shot_render_roles(self, shot):
        group = shot.get("reference_group", "")
        roles = shot.get("cast") or shot.get("characters")
        if not isinstance(roles, list):
            roles = (["female", "male"] if group == "two" else [group] if group in self.cast
                     else sorted({d["role"] for d in shot.get("dialogue", []) if d["role"] in self.cast})
                     or list(self.cast)[:1])
        return [role for role in roles if role in self.cast]

    def _characters_and_images(self, roles=None, shot_ids=None, upstream_return=None):
        preview_role, preview = self._approved_preview_reference()
        targets = []
        for role, description in self.cast.items():
            if roles is not None and role not in roles:
                continue
            references = [preview] if role == preview_role else []
            approved = self._material_character(role) or self._series_character(role)
            if approved:
                team_control.assert_publishable(self.ident, "character", role)
                team_ledger.mark_reused(self.ident, "character", role, artifact=approved,
                                        input_version=approved.get("source_signature"),
                                        review_status=approved.get("qa", {}).get("status"))
                self.assets["characters"][role] = approved
                continue
            identity = ("Preserve the exact face, age, hairstyle, clothing and visual design of the person in the approved reference image. "
                        if references else "")
            prompt = self.style_prompt + "\nCharacter reference, medium close-up, both shoulders visible, relaxed closed lips, uncluttered background. " + identity + description
            targets.append(self._target("character_" + role, self.folder / "assets/characters" / role,
                                        prompt, references, character=role))
        self._render_targets(targets, "character_references")
        lookup = {s["id"]: s for s in self.shots}
        pending = [shot for shot in self.shots if shot_ids is None or shot["id"] in shot_ids]
        done = set()
        if shot_ids is not None:
            referenced = {ref for shot in pending for ref in shot.get("reference_shots", []) if ref not in shot_ids}
            for ref in referenced:
                if ref not in lookup or not lookup[ref].get("image"):
                    raise ValueError("Unknown or unfinished shot reference dependency")
                _receipt_path(lookup[ref]["image"])
                done.add(ref)
        while pending:
            batch = [s for s in pending if set(s.get("reference_shots", [])) <= done]
            if not batch:
                raise ValueError("Unknown or cyclic shot reference dependency")
            targets = []
            for shot in batch:
                roles = self._shot_render_roles(shot)
                references = [_receipt_path(self.assets["characters"][r]) for r in roles]
                identity = "Preserve the exact identity, age, hairstyle and clothing of each corresponding person in the character reference images. "
                references, series_prompt, asset_references = self._series_shot_inputs(shot, roles, references)
                if series_prompt:
                    identity = series_prompt
                references, material_prompt, material_references = self._material_shot_inputs(shot, roles, references)
                if material_prompt:
                    identity += material_prompt
                    asset_references += material_references
                for ref in shot.get("reference_shots", []):
                    references.append(_receipt_path(lookup[ref]["image"]))
                    identity += "The additional image establishes the exact set, props and lighting continuity. "
                if material_references:
                    self._check_material_reference_limit(references)
                shot_style = shot.get("render_style_prompt", self.style_prompt)
                if not isinstance(shot_style, str) or not shot_style.strip():
                    raise ValueError("render_style_prompt must be a non-empty string")
                prompt = shot_style + "\n" + identity + "Create a single clear story frame: " + shot["prompt"]
                target = self._target(shot["id"], self.folder / "assets/shots" / shot["id"],
                                      prompt, references, shot=shot, review=True, reference_roles=roles,
                                      **({"asset_references": asset_references} if asset_references else {}))
                returned = upstream_return or (self.production.get('upstream_returns') or {}).get(shot['id'])
                if returned:
                    target['upstream_return'] = copy.deepcopy(returned)
                    target['_upstream_assessment'] = copy.deepcopy(returned['assessment'])
                    target['_rejected_media_sha256'] = list(returned['rejected_media_sha256'])
                    lineage = self.production.setdefault('recovery_lineage', {}).setdefault('image:' + shot['id'], [])
                    for source in (target['signature'], returned.get('previous_image_source_signature')):
                        if source and source not in lineage:
                            lineage.append(source)
                targets.append(target)
            self._render_targets(targets, "story_frames")
            for shot in batch:
                done.add(shot["id"])
                pending.remove(shot)

    def _video_target(self, shot):
        image = _receipt_path(shot['image'])
        prompt = shot['motion_prompt'] + ' Preserve the reference character identities, clothing, lighting and composition. Restrained natural motion and a single readable action.'
        if self.creative_contract:
            prompt = (style_prompt(self.project) + '\n' + prompt +
                ' Animate the character performing the approved action over time. '
                'Character movement must be visible independently of camera movement; do not deliver a pan over a still image.')
        if not shot.get('lip_sync'):
            prompt += ' Visible people keep relaxed closed lips; dialogue may be off-screen voiceover.'
        return self._target(shot['id'], self.folder / 'renders' / shot['id'], prompt, [image], kind='video',
            seconds=shot['seconds'], shot=shot, review=True,
            **({'asset_references': shot['image']['asset_references']} if shot['image'].get('asset_references') else {}))

    def _review_transport_pending(self):
        wait = self.production.get('review_transport_recovery') or {}
        if wait.get('status') != 'waiting':
            return
        lookup = {s['id']: s for s in self.shots}
        self._transport_review_only = True
        try:
            self._resume_transport_reviews(wait, lookup)
        finally:
            self._transport_review_only = False

    def _resume_transport_reviews(self, wait, lookup):
        for item in list(wait.get('pending', [])):
            shot = lookup.get(item.get('asset'))
            if shot is None or item.get('stage') not in {'image', 'video'}:
                raise ProductionNeedsDecision('原媒体补审范围无法匹配，未新增生成', 'production_review_scope_changed')
            saved_path = within(ROOT, item['path'])
            if not saved_path.is_file():
                raise ProductionNeedsDecision('原待补审媒体缺失，未重新生成', 'production_review_media_missing')
            if item.get('review_kind') == 'first_frame':
                from .production_handoffs import load, review_first_frame
                image = shot.get('image') or {}
                if (item['stage'] != 'image' or not image.get('path')
                        or within(ROOT, image['path']).resolve() != saved_path.resolve()):
                    raise ProductionNeedsDecision('原首帧补审归属已变化，未新增生成', 'production_review_scope_changed')
                case = load(self, item.get('upstream_case_id'))
                if case['document']['identity'].get('asset') != shot['id']:
                    raise ProductionNeedsDecision('原首帧交接岗位范围不匹配', 'production_review_scope_changed')
                review_first_frame(self, case, {'key': shot['id'], 'kind': 'video', 'shot': shot,
                                               'references': [saved_path]})
                wait['pending'] = [p for p in wait.get('pending', []) if p != item]
                if not wait['pending']:
                    wait['status'] = 'resolved'
                self.emit('semantic_review_pending')
                continue
            if item['stage'] == 'video':
                target = self._video_target(shot)
            else:
                self._characters_and_images(roles=[], shot_ids=[shot['id']])
                continue
            target['review_candidate'] = self._transport_candidate(target, item)
            self._render_targets([target], 'semantic_review_pending')

    def _transport_candidate(self, target, item):
        path = within(ROOT, item['path'])
        if not path.is_relative_to(target['folder']) or path.parent.name != item['take']:
            raise ProductionNeedsDecision('待补审媒体归属不匹配，未新增生成', 'production_review_scope_changed')
        record = read_json(path.parent / 'attempt.json')
        receipt = read_json(path.parent / 'result.json')
        if (record.get('take') != item['take'] or not _matching_source(receipt, target)
                or _receipt_path(receipt).resolve() != path.resolve()):
            raise ProductionNeedsDecision('原待补审媒体来源已变化，未提交新生成', 'production_review_scope_changed')
        return record, receipt

    def _video(self, shot_ids=None):
        shots = [shot for shot in self.shots if shot_ids is None or shot["id"] in shot_ids]
        if self.mode == "motion_comic":
            for shot in team_control.ordered(self.ident, "video", shots, key=lambda value: value["id"]):
                self.boundary()
                shot["status"] = "rendering"
                self.emit("camera_motion")
                record = self._camera_clip(shot)
                team_control.assert_publishable(self.ident, "video", shot["id"])
                # Camera movement does not animate speech or acting.
                record["semantic_source"] = shot["image"].get("qa", {}).get("semantic", {})
                if shot["image"].get("asset_references"):
                    record["asset_references"] = copy.deepcopy(shot["image"]["asset_references"])
                shot.update(video=record, video_path=record["path"],
                            video_url=media_url(_receipt_path(record)), status="passed",
                            generation_kind="procedural_camera_motion",
                            qa={"technical": record["qa"]["technical"],
                                "semantic": record["semantic_source"],
                                "scope": "semantic_review_of_keyframe_plus_camera_clip_technical_checks"})
                write_json(self.folder / "renders" / shot["id"] / "selected.json", record)
                team_ledger.record_adoption(self.ident, "video", shot["id"], artifact=record)
                self.emit("camera_motion")
                team_control.after_unit(self.ident, "video", shot["id"])
                team_control.after_unit(self.ident, "video_review", shot["id"])
            return
        targets = [self._video_target(shot) for shot in shots]
        self._render_targets(targets, "diffusion_video")

    def _controlled_run(self):
        """Select one actual ready unit without changing the film timeline.

        Engine-local completion follows receipt verification in the real
        adapters. Predicted ledger states are never evidence that an input is
        current or that a generation can be skipped.
        """
        lookup = {shot["id"]: shot for shot in self.shots}
        line_ids = {line["id"] for shot in self.shots for line in shot.get("dialogue", [])}
        # A command may invalidate an ancestor while a paid descendant is still
        # running. Resume that saved unit directly, before rechecking ancestors;
        # provider journals/admission continue to forbid replacement submission.
        for draining in team_control.draining_units(self.ident):
            kind, scope = draining["kind"], draining["scope"]
            if kind == "video" and scope in lookup:
                self._video(shot_ids=[scope])
            elif kind == "image" and scope in lookup:
                self._characters_and_images(roles=[], shot_ids=[scope])
            elif kind == "character" and scope in self.cast:
                self._characters_and_images(roles=[scope], shot_ids=[])
            elif kind == "speech" and scope in line_ids:
                self._voice(line_ids=[scope])
            else:
                raise ProductionNeedsDecision(
                    "已有远程任务无法与当前制作对象安全匹配，请核对原任务后继续", "team_remote_scope_changed",
                    {"kind": kind, "scope": scope})
        units = []

        def add(kind, scope, dependencies=()):
            units.append({"id": kind + ":" + scope, "kind": kind, "scope": scope,
                          "dependencies": list(dependencies)})

        for shot in self.shots:
            for line in shot.get("dialogue", []):
                add("speech", line["id"])
        for shot in self.shots:
            if shot.get("dialogue"):
                add("dialogue", shot["id"], ["speech:" + line["id"] for line in shot["dialogue"]])
        for role in self.cast:
            add("character", role)
        for shot in self.shots:
            refs = shot.get("reference_shots", [])
            if not isinstance(refs, list) or any(ref not in lookup for ref in refs):
                raise ValueError("Unknown shot reference dependency")
            dependencies = ["character:" + role for role in self._shot_render_roles(shot)]
            dependencies += ["image:" + ref for ref in refs]
            if shot.get("dialogue"):
                dependencies.append("dialogue:" + shot["id"])
            add("image", shot["id"], dependencies)
        for shot in self.shots:
            add("video", shot["id"], ["image:" + shot["id"]])
        mapping, completed = {}, set()
        if not any(shot.get("dialogue") for shot in self.shots):
            self._voice()
        while len(completed) < len(units):
            ready = [unit for unit in units if unit["id"] not in completed
                     and set(unit["dependencies"]) <= completed]
            if not ready:
                raise ValueError("Unknown or cyclic production dependency")
            unit = team_control.next_ready(self.ident, ready)
            kind, scope = unit["kind"], unit["scope"]
            if kind == "speech":
                mapping.update(self._voice(line_ids=[scope]))
            elif kind == "dialogue":
                mapping = self._fit_dialogue(mapping, shot_ids=[scope])
            elif kind == "character":
                self._characters_and_images(roles=[scope], shot_ids=[])
            elif kind == "image":
                self._characters_and_images(roles=[], shot_ids=[scope])
            elif kind == "video":
                self._video(shot_ids=[scope])
            else:
                raise RuntimeError("Unsupported ready production unit")
            completed.add(unit["id"])
        self._assert_semantic_gate()
        return self._delivery(mapping)

    def run(self):
        if not self.shots:
            raise ValueError("The approved episode contains no shots")
        if len(self.shots) > 80:
            raise ValueError("This bounded episode profile supports at most 80 shots")
        validate_episode({**self.episode, "shots": self.shots}, self.project,
                         self.video_connection if self.mode != "motion_comic" else None,
                         allow_trim=bool(self.project.get("edit_plan")))
        if self.semantic_enabled and self.evaluator is None:
            raise ProductionNeedsDecision("Semantic review is enabled but no evaluator is connected",
                                          "semantic_unavailable", {"resume_action": "connect_evaluator_and_resume"})
        if self.mode != "motion_comic":
            validate_video_plan(self.video_connection, self.shots)
        self._review_transport_pending()
        self._approved_preview_reference()
        episode = {**self.episode, "slug": self.slug, "factory_project_id": self.ident,
                   "cast": self.cast, "shots": self.shots, "fps": 24,
                   "total_seconds": sum(s["seconds"] for s in self.shots),
                   "generation": {"width": self.image_size[0], "height": self.image_size[1]},
                   "width": min(self.image_size[0], self.output_size[0]),
                   "height": min(self.image_size[1], self.output_size[1]),
                   "delivery": {"width": self.output_size[0], "height": self.output_size[1]}}
        write_json(self.folder / "revisions" / ("episode_" + fingerprint(episode)[:16] + ".json"), episode)
        write_json(self.folder / "episode.json", episode)
        self.emit("production_started", capabilities=capabilities(self.config))
        if team_ledger.enabled(self.project):
            return self._controlled_run()
        mapping = self._fit_dialogue(self._voice())
        self._characters_and_images()
        self._video()
        self._assert_semantic_gate()
        return self._delivery(mapping)


def generate_style_previews(project, config, checkpoint, control=None, *, semantic_evaluator=None):
    run = _Run(project, config, checkpoint, control, semantic_evaluator)
    try:
        return run.previews()
    finally:
        run.emit()


def generate_project(project, config, checkpoint, control=None, semantic_evaluator=None, dialogue_rewriter=None):
    run = _Run(project, config, checkpoint, control, semantic_evaluator, dialogue_rewriter)
    try:
        return run.run()
    finally:
        run.emit()
