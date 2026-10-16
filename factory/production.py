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
from studio.graphs import flux_image, wan_video
from . import team_ledger
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


def _grounded_repair_plan(assessment, stage, attempt, expected=None, reference_roles=()):
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
        repair.update(action="repair_scene_action_and_continuity",
                      seed_delta=1013 * attempt, prompt_suffix="",
                      regenerate_keyframe=stage == "video",
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
    if not isinstance(expected, dict) or technical:
        return repair

    fields = ("action", "continuity", "prompt")
    if stage == "video":
        fields += ("motion_prompt",)
    for field in fields:
        value = expected.get(field)
        if value is not None and value != "" and value != [] and value != {}:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            repair["positive_constraints"].append({"field": field, "text": text})
    if route in ("continuity", "action"):
        focus = ("Render the approved action and the exact approved object placement and state "
                 "as visibly readable facts in the frame.")
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
    constraints = [focus]
    labels = {"action": "Approved action and object state", "continuity": "Approved continuity",
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
        self.config = dict(config or {})
        connection = self.config.get("video_connection", {"profile": "local_wan", **LOCAL_PROFILE})
        self.video_connection = {"profile": connection.get("profile", "local_wan"),
                                 **normalize_video_profile({k: v for k, v in connection.items() if k != "profile"})}
        self.checkpoint = checkpoint or (lambda patch: None)
        self.control, self.evaluator = control, evaluator
        self.dialogue_rewriter = dialogue_rewriter
        self.semantic_enabled = bool(self.config.get("semantic_enabled", evaluator is not None))
        self.semantic_max_attempts = int(self.config.get("semantic_max_attempts", 2))
        if not 1 <= self.semantic_max_attempts <= 3:
            raise ValueError("semantic_max_attempts must be between one and three")
        explicit_policy = self.config.get("semantic_review_revision")
        self.review_policy = fingerprint({
            "policy": explicit_policy or "local_vlm_review_v2",
            "model": self.config.get("semantic_model"),
            **({"temporal_sampling": self.config.get("temporal_sampling", {
                "policy": "uniform_endpoints_and_cut_neighbors.v1", "count": 8})}
               if "temporal_sampling" in self.config or not explicit_policy else {}),
            **({"model_identity": self.config["semantic_model_identity"]}
               if self.config.get("semantic_model_identity") is not None else {}),
            "implementation": {} if explicit_policy else {
                name: sha256(ROOT / "factory" / name)
                for name in ("llm.py", "quality.py", "evaluation_sampling.py") if (ROOT / "factory" / name).is_file()},
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
        self.budget = Budget(self.config, self.production)
        self._scheduler = None
        self.max_attempts = int(self.config.get("max_attempts",
                                               self.project.get("budget", {}).get("max_attempts", 3)))
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be between one and five")
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
            shot["id"] = _safe_id(shot.get("id") or f"S{i + 1:02d}")
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
                                "engine": "cami_orchestrator_remote_gpu_v1" if self.config.get("gpu_service") else "local_autonomous_factory_v1",
                                "active_workers_limit": 1,
                                "semantic_enabled": self.semantic_enabled, "semantic_max_attempts": self.semantic_max_attempts})
        payload = {"production": copy.deepcopy(self.production),
                   "shots": copy.deepcopy(self.shots), "assets": copy.deepcopy(self.assets), **patch}
        self.project.update(copy.deepcopy(payload))
        self.checkpoint(payload)

    def _resource_event(self, event):
        self.production["resource_wait"] = copy.deepcopy(event)
        if event["status"] in ("checking", "waiting"):
            self.production.setdefault("resource_resume_phase", self.production.get("phase", "production"))
            self.emit("waiting_resources")
        else:
            phase = self.production.pop("resource_resume_phase", None)
            self.emit(phase or ("resources_ready" if event["status"] == "ready" else "waiting_resources"))

    def boundary(self, gpu=False):
        self.emit()
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
        if record.get("source_signature") != target["signature"]:
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
        history = record.setdefault("semantic_attempts", [])
        answer = None
        for number in range(1, self.semantic_max_attempts + 1):
            self.boundary()
            started = time.monotonic()
            answer = semantic_review(self.evaluator, self.project,
                                     target.get("shot") or target.get("review_context") or
                                     {"id": target["key"], "prompt": target["prompt"]},
                                     path, target["references"])
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
            if record.get("source_signature") == target["signature"]:
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

    @property
    def scheduler(self):
        if self._scheduler is None:
            self._scheduler = Scheduler(self.config, self.budget, event_callback=self._resource_event)
        return self._scheduler

    @scheduler.setter
    def scheduler(self, value):
        self._scheduler = value

    def _target(self, key, folder, prompt, references=(), kind="image", **extra):
        refs = [Path(p) for p in references]
        dimensions = self.image_size if kind == "image" else self.video_size
        signature = fingerprint({"prompt": prompt, "references": [media_receipt(p) for p in refs],
                                 "dimensions": dimensions, "kind": kind,
                                 **({"connection": media_identity(self.config["image_connection"])} if kind == "image" and self.config.get("image_connection", {}).get("provider", "legacy") != "legacy" else {}),
                                 **({"connection": media_identity(self.video_connection)} if kind == "video" and self.video_connection.get("provider") in {"comfyui_local", "comfyui_external"} else {}),
                                 "model": ("FLUX.2-klein-4B" if kind == "image" else
                                           video_identity(self.video_connection) if self.video_connection["provider"] == "minimax"
                                           else "Wan2.2-TI2V-5B"),
                                 "seconds": extra.get("seconds"),
                                 "mode": self.mode, "profile": self.config.get("video_profile", "quality"),
                                 **({"asset_references": extra["asset_references"]} if extra.get("asset_references") else {}),
                                 **({"render_revision": extra["shot"]["render_revisions"][kind]}
                                    if (extra.get("shot") or {}).get("render_revisions", {}).get(kind) else {}),
                                 **({"production_profile": profile_signature(self.project)} if explicit_profile(self.project) else {})})
        shot = extra.get("shot") or extra.get("review_context") or {}
        expected = {key: shot.get(key) for key in (
            "id", "seconds", "prompt", "motion_prompt", "dialogue", "reference_group",
            "reference_shots", "continuity", "camera_motion", "action", "cast", "characters")}
        if self.creative_contract:
            signature = fingerprint({"source_signature": signature, "creative_contract": self.creative_contract})
            expected.update(creative_contract=self.creative_contract,
                            approved_visual_style={key: self.selected_style[key] for key in
                                ("id", "name", "description", "prompt", "palette")
                                if self.selected_style and key in self.selected_style},
                            visual_direction=shot.get("visual_direction"))
        if shot.get("material_constraints"):
            expected["material_constraints"] = shot["material_constraints"]
        review_signature = fingerprint({"source_signature": signature, "policy": self.review_policy,
                                        "expected": expected, "cast": self.cast, "format": self.mode})
        return {"key": key, "folder": folder, "prompt": prompt, "references": refs,
                "kind": kind, "signature": signature, "review_signature": review_signature, **extra}

    def _unfinished_candidate(self, target):
        self._guard_remote_submissions(target)
        for record in reversed(self._attempts(target)):
            folder = target["folder"] / record["take"]
            result = folder / "result.json"
            if record.get("state") in ("generated", "reviewing", "semantic_pending", "passed", "rendering") and result.exists():
                media = read_json(result)
                _receipt_path(media)
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
            elif record.get("state") == "rendering" and self.config.get("gpu_service"):
                # A pause before the main submission may leave an accepted
                # repair-keyframe job. Re-enter the same take so its client can
                # reconcile that job before submitting the main generation.
                target["resume_attempt"] = record
                return None
        return None

    def _new_attempt(self, target):
        previous = self._attempts(target)
        if len(previous) >= self.max_attempts:
            if target.get("shot") is not None:
                target["shot"]["status"] = "failed"
            self.emit("needs_attention")
            raise ProductionNeedsDecision("A production asset exhausted its authorized retry limit",
                                          "retry_limit",
                                          {"asset": target["key"], "stage": target["kind"],
                                           "attempts": len(previous)})
        indexes = [int(p.name[5:]) for p in target["folder"].glob("take_*")
                   if p.is_dir() and p.name[5:].isdigit()]
        take = f"take_{max(indexes, default=0) + 1:03d}"
        folder = target["folder"] / take
        folder.mkdir(parents=True, exist_ok=False)
        number = len(previous) + 1
        repair = _grounded_repair_plan(
            previous[-1].get("qa", {"issues": [{"code": "generation", "detail": previous[-1].get("error", "")}]}),
            target["kind"], number, target.get("shot"), target.get("reference_roles", ())) if previous else {}
        seed = (int(fingerprint({"project": self.ident, "asset": target["key"]})[:8], 16)
                + int(repair.get("seed_delta", 0)) + number - 1) % (2**31)
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
        if explicit_profile(self.project):
            record["production_profile"] = resolve_profile(self.project)
            record["production_profile_signature"] = profile_signature(self.project)
        if target.get("shot"):
            record.update(plan_signature=plan_signature(self.project, target["shot"], target["kind"]),
                          shot_revision=target["shot"].get("shot_revision", 1),
                          production_revision=self.project.get("production_revision", 1))
        if target.get("asset_references"):
            record["asset_references"] = copy.deepcopy(target["asset_references"])
        if self._remote_video(target):
            record.update(provider="minimax", profile=self.video_connection["profile"],
                          model=self.video_connection["model"],
                          parameters={"duration": self.video_connection["duration"],
                                      "resolution": self.video_connection["resolution"],
                                      "timeline_seconds": target.get("seconds"), "timeline_fps": 24})
        self._team_begin_target(target, record)
        self._save_attempt(target, record)
        return record

    def _render_targets(self, targets, phase):
        if targets and targets[0]["kind"] == "image" and self.config.get("image_connection", {}).get("enabled") is False:
            raise ProductionNeedsDecision("请先配置并启用图像连接", "image_unconfigured")
        pending = []
        for target in targets:
            selected = self._valid_selection(target)
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
                    if target.get("resume_attempt"):
                        self._team_begin_target(target, target["resume_attempt"])
                    generate.append(target)
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
                with self.scheduler.worker(self.slug + "_" + phase, self.control) as worker:
                    for target in generate:
                        self.boundary(gpu=True)
                        record = target.pop("resume_attempt", None) or self._new_attempt(target)
                        shot = target.get("shot")
                        if shot is not None:
                            shot["status"] = "rendering" if record["attempt"] == 1 else "retrying"
                        self.emit(phase)
                        started = time.monotonic()
                        try:
                            names = [worker.copy_input(p) for p in target["references"]]
                            if target["kind"] == "image":
                                graph = flux_image(record["prompt"], record["seed"], *self.image_size,
                                                   references=names, prefix=self.slug + "/" + target["key"])
                            else:
                                if record.get("repair", {}).get("regenerate_keyframe"):
                                    # A diagnosed identity/composition failure gets a genuinely new
                                    # first frame, rather than merely a differently worded video prompt.
                                    shot_refs = list(target["references"])
                                    if not any(ref.get("library_key") for ref in target.get("asset_references", [])):
                                        shot_refs += [_receipt_path(value) for value in self.assets.get("characters", {}).values()]
                                    material = any(ref.get("library_key") for ref in target.get("asset_references", []))
                                    repair_names = [worker.copy_input(p) for p in (shot_refs if material else shot_refs[:4])]
                                    keyframe_prompt = ((record["repair"].get("prompt_prefix") or
                                                        record["repair"].get("prompt_suffix", "")) + "\n" +
                                                       self.style_prompt + " " + target["shot"]["prompt"] +
                                                       " Preserve each corresponding character identity and wardrobe.")
                                    repair_graph = flux_image(keyframe_prompt, record["seed"], *self.image_size,
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
                                if self.config.get("video_profile") == "fastwan":
                                    profile = read_json(ROOT / "workflows/fastwan_profile.json")
                                graph = wan_video(record["prompt"], record["seed"], names[0],
                                                  *self.video_size, seconds=target["seconds"],
                                                  prefix=self.slug + "/" + target["key"], profile=profile)
                            path = worker.execute(graph, target["folder"] / record["take"], target["kind"],
                                                  timeout=self.budget.timeout(1800 if target["kind"] == "image" else 7200, 1))
                            receipt = read_json(path.parent / "result.json")
                            record.update(state="generated", output=receipt, elapsed_seconds=round(time.monotonic() - started, 3))
                            ready.append((target, record, receipt))
                        except (ProductionPaused, ProductionNeedsDecision):
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
            # The owned Comfy process and its GPU lease are closed before VLM evaluation.
            semantic_pending = []
            for target, record, receipt in ready:
                with self._team_review_unit(target, record, receipt) as review_token:
                    self.boundary()
                    path = _receipt_path(receipt)
                    shot = target.get("shot")
                    if shot is not None:
                        shot["status"] = "reviewing"
                    self.emit("quality_review")
                    if target["kind"] == "image":
                        technical = inspect_image(path, self.image_size)
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
                    if target["kind"] == "video":
                        saved.update(motion_method="generated_video", diffusion_video=True)
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
                    selection_file = target["folder"] / "selected.json"
                    if qa["eligible_for_selection"]:
                        write_json(selection_file, saved)
                        self._attach(target, saved)
                        (target["folder"] / "pending_review.json").unlink(missing_ok=True)
                    else:
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
                                                     "detail": semantic.get("detail")})
                    self.emit(phase)
            if semantic_pending:
                self.emit("semantic_review_pending")
                raise ProductionNeedsDecision("The semantic reviewer is unavailable; generated media is preserved for review-only resume",
                                              "semantic_unavailable",
                                              {"pending": semantic_pending, "review_attempts_per_asset": self.semantic_max_attempts,
                                               "resume_action": "review_existing_assets_only"})
            pending = [target for target in pending if "selected" not in target]
        return [target["selected"] for target in targets]

    def previews(self):
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
                                           if self.creative_contract else {})))
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

    def _characters_and_images(self):
        preview_role, preview = self._approved_preview_reference()
        targets = []
        for role, description in self.cast.items():
            references = [preview] if role == preview_role else []
            approved = self._material_character(role) or self._series_character(role)
            if approved:
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
        done, pending = set(), list(self.shots)
        while pending:
            batch = [s for s in pending if set(s.get("reference_shots", [])) <= done]
            if not batch:
                raise ValueError("Unknown or cyclic shot reference dependency")
            targets = []
            for shot in batch:
                group = shot.get("reference_group", "")
                roles = shot.get("cast") or shot.get("characters")
                if not isinstance(roles, list):
                    roles = (["female", "male"] if group == "two" else [group] if group in self.cast
                             else sorted({d["role"] for d in shot.get("dialogue", []) if d["role"] in self.cast})
                             or list(self.cast)[:1])
                roles = [r for r in roles if r in self.cast]
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
                targets.append(self._target(shot["id"], self.folder / "assets/shots" / shot["id"],
                                            prompt, references, shot=shot, review=True, reference_roles=roles,
                                            **({"asset_references": asset_references} if asset_references else {})))
            self._render_targets(targets, "story_frames")
            for shot in batch:
                done.add(shot["id"])
                pending.remove(shot)

    def _video(self):
        if self.mode == "motion_comic":
            for shot in self.shots:
                self.boundary()
                shot["status"] = "rendering"
                self.emit("camera_motion")
                record = self._camera_clip(shot)
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
                self.emit("camera_motion")
            return
        targets = []
        for shot in self.shots:
            image = _receipt_path(shot["image"])
            prompt = shot["motion_prompt"] + " Preserve the reference character identities, clothing, lighting and composition. Restrained natural motion and a single readable action."
            if self.creative_contract:
                prompt = (style_prompt(self.project) + "\n" + prompt +
                          " Animate the character performing the approved action over time. "
                          "Character movement must be visible independently of camera movement; do not deliver a pan over a still image.")
            if not shot.get("lip_sync"):
                prompt += " Visible people keep relaxed closed lips; dialogue may be off-screen voiceover."
            targets.append(self._target(shot["id"], self.folder / "renders" / shot["id"],
                                        prompt, [image], kind="video", seconds=shot["seconds"],
                                        shot=shot, review=True,
                                        **({"asset_references": shot["image"]["asset_references"]}
                                           if shot["image"].get("asset_references") else {})))
        self._render_targets(targets, "diffusion_video")

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
