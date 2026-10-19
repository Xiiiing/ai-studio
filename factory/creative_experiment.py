"""Freeze comparable text experiments without executing any model or workflow.

This is an evidence protocol, not a second film-quality or approval engine.
Only explicit, offline imports can populate an experiment. The trusted seal
belongs outside the run directory and must be retained by its operator.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .config import llm_identity
from .providers import config_revision
from .profile_creative import adapt_system
from .prompt_registry import prompt_receipt, PromptText
from .creative_evidence_io import digest, read_json, read_bytes, new_directory, write_new
from .schemas import SCORE_KEYS, episode_schema
from .storyboard import storyboard_schema, DIRECTOR_FIELDS
from .creative_holdout import reserve_holdout

PROTOCOL = "ai-studio.creative-ab.v1"
CORPUS = "ai-studio.creative-corpus.v1"
ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "factory/creative_experiment.py", "factory/creative_comparison.py",
    "factory/creative_experiment_cli.py", "factory/creative_evidence_io.py",
    "factory/creative_holdout.py",
    "factory/agent_loop.py", "factory/agent_policy.py", "factory/agent_protocol.py", "factory/agent_media.py",
    "factory/creative_runtime.py", "factory/role_handoffs.py", "factory/worker.py",
    "factory/execution_context.py", "factory/team_tasks.py",
    "factory/prompt_registry.py", "factory/prompt_workbench.py", "factory/generation_pipeline.py", "factory/profile_creative.py",
    "factory/llm.py", "factory/schemas.py", "factory/storyboard.py",
    "factory/production.py", "factory/audio_editor.py", "factory/speech.py",
    "factory/production_profiles.py", "factory/config.py", "factory/providers.py",
)
# Existing text-critic dimensions, not the separate seven-dimension film gate.
RUBRIC = dict(zip(SCORE_KEYS, (
    "开场是否以作品中的具体事件建立观看兴趣，而非只在说明中自称有钩子。",
    "行动、信息、道具转移与结果的因果是否在当前文本内成立。",
    "人物目标与选择是否有具体依据，是否符合输入中的既有事实。",
    "本题材内的组合是否具体；不能因格式完整或换名称自动加分。",
    "本集冲突是否兑现结局，删除项与最新人工反馈是否真正落实。",
    "是否符合冻结的媒介、角色、时长和镜头权限；不评价不存在的媒体。",
)))
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")


def source_identity():
    files = [{"path": path, "sha256": hashlib.sha256(read_bytes(ROOT / path)).hexdigest()}
             for path in SOURCE_FILES]
    return {"sha256": digest(files), "files": files}


def _outside(seal, run):
    if any(".." in Path(p).parts for p in (seal, run)):
        raise ValueError("Experiment paths cannot contain parent traversal")
    seal, run = Path(seal).absolute(), Path(run).absolute()
    if seal.is_relative_to(run):
        raise ValueError("Trusted seal must be outside the experiment run directory")
    if seal.is_symlink() or any(p.is_symlink() for p in seal.parents):
        raise ValueError("Trusted seal path cannot contain a symlink")
    return seal, run


def _cases(corpus, split):
    if set(corpus) != {"schema", "corpus_id", "cases"} or corpus["schema"] != CORPUS:
        raise ValueError("Unsupported creative corpus")
    if not isinstance(corpus["corpus_id"], str) or not ID.fullmatch(corpus["corpus_id"]):
        raise ValueError("Invalid corpus ID")
    cases = corpus["cases"]
    if not isinstance(cases, list) or not 2 <= len(cases) <= 64:
        raise ValueError("Corpus must contain 2..64 frozen cases")
    seen = set()
    for case in cases:
        if (not isinstance(case, dict) or set(case) != {
                "id", "split", "genre", "negative_control", "conflict_case", "project", "inputs"}
                or not isinstance(case["id"], str) or not ID.fullmatch(case["id"])
                or case["id"] in seen):
            raise ValueError("Invalid or duplicate corpus case")
        seen.add(case["id"])
        if case["split"] not in {"development", "holdout"}:
            raise ValueError("Unknown corpus split")
        if not isinstance(case["genre"], str) or not case["genre"].strip():
            raise ValueError("Case genre is required")
        if any(type(case[name]) is not bool for name in ("negative_control", "conflict_case")):
            raise ValueError("Case control flags must be booleans")
        if not isinstance(case["project"], dict) or not isinstance(case["inputs"], dict):
            raise ValueError("Case project and inputs must be objects")
        if set(case["inputs"]) != {"screenwriter", "storyboard_director"}:
            raise ValueError("Corpus must freeze both screenplay and storyboard inputs")
    selected = [case for case in cases if case["split"] == split]
    if not selected:
        raise ValueError("The selected corpus split is empty")
    return selected


def pair_specs(plan):
    """Two positions for every replicate; mappings stay out of judge bundles."""
    result = []
    for case in plan["cases"]:
        for replicate in range(1, plan["samples"] + 1):
            flip = int(digest([plan["seed"], case["id"], replicate])[-1], 16) % 2
            for position in (0, 1):
                arms = ("baseline", "candidate") if flip == position else ("candidate", "baseline")
                ident = digest([plan["experiment_id"], case["id"], replicate, position])[:24]
                result.append({"id": ident, "case_id": case["id"], "replicate": replicate,
                               "position": position, "arms": dict(zip(("A", "B"), arms))})
    return result


def freeze(corpus_path, baseline, candidate, config, output, seal_path, *, role,
           samples=3, seed=731, split="development", allow_holdout=False,
           judge_id, judge_config=None, git_commit=None, max_tokens=None, temperature=.65):
    """Emit exact request bundles, a private plan, and an external trusted seal.

    No provider discovery, sockets, subprocesses, database reads or generation.
    Baseline/candidate differ ONLY in their explicitly supplied prompt bundles.
    """
    if role not in {"screenwriter", "storyboard_director"}:
        raise ValueError("Only screenplay and storyboard comparisons are supported")
    if any(not isinstance(prompt, PromptText) or prompt.spec.role != role for prompt in (baseline, candidate)):
        raise ValueError("Both variants must be registered prompts for the same role")
    if split not in {"development", "holdout"} or (split == "holdout" and not allow_holdout):
        raise ValueError("holdout requires an explicit frozen release")
    if type(samples) is not int or not 2 <= samples <= 5 or type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("Use 2..5 fixed replicates and an unsigned 32-bit seed")
    if not isinstance(judge_id, str) or not ID.fullmatch(judge_id):
        raise ValueError("An explicit, stable judge identity is required")
    if git_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", git_commit):
        raise ValueError("Invalid source git commit")
    max_tokens = max_tokens if max_tokens is not None else (11500 if role == "screenwriter" else 6500)
    if type(max_tokens) is not int or not 128 <= max_tokens <= 16384:
        raise ValueError("Invalid output budget")
    if type(temperature) not in (float, int) or not 0 <= temperature <= 2:
        raise ValueError("Invalid sampling temperature")
    seal_path, output = _outside(seal_path, output)
    if output.exists() or seal_path.exists():
        raise FileExistsError("Experiment output and trusted seal must be new")
    corpus = read_json(corpus_path)
    cases = _cases(corpus, split)
    model = llm_identity(config, role)
    from .creative_comparison import JUDGE_SYSTEM
    judge = {"kind": "human", "id": judge_id, "model_identity": None, "config_revision": None,
             "parameters": {"max_tokens": 5800, "temperature": .25}}
    if judge_config is not None:
        judge.update(kind="model", model_identity=llm_identity(judge_config, "independent_critic"),
                     config_revision=config_revision(judge_config))
    source = source_identity()
    variants = {name: {"role": prompt.spec.role, "version": prompt.spec.version,
                        "template_sha256": prompt.spec.template_sha256,
                        "contract_sha256": prompt.spec.contract_sha256}
                for name, prompt in (("baseline", baseline), ("candidate", candidate))}
    plan = {"protocol": PROTOCOL, "role": role, "git_commit": git_commit,
            "source": source, "corpus_id": corpus["corpus_id"], "corpus_sha256": digest(corpus),
            "split": split, "samples": samples, "seed": seed, "judge_id": judge_id,
            "judge_identity": judge, "judge_protocol": "ai-studio.blind-text-judge.v1",
            "judge_system_sha256": hashlib.sha256(JUDGE_SYSTEM.encode()).hexdigest(),
            "rubric": RUBRIC, "rubric_sha256": digest(RUBRIC), "model_identity": model,
            "config_revision": config_revision(config), "variants": variants,
            "parameters": {"max_tokens": max_tokens, "temperature": temperature},
            "holdout_policy": "one-sealed-comparison; retire-after-results-informed-tuning",
            "cases": [{key: case[key] for key in ("id", "split", "genre", "negative_control", "conflict_case")}
                      for case in cases], "tasks": []}
    # Derived entirely from frozen inputs, not paths, wall-clock time or best scores.
    plan["experiment_id"] = digest(plan)[:24]
    requests = {}
    for case in cases:
        payload = case["inputs"][role]
        if not isinstance(payload, dict):
            raise ValueError("Creative request context must be an object")
        if role == "screenwriter":
            if payload.get("project") != case["project"]:
                raise ValueError("Screenwriter input does not match the frozen project")
            schema = episode_schema(project=case["project"])
        else:
            selected = payload.get("editable_shot_ids")
            if payload.get("editable_fields") != list(DIRECTOR_FIELDS):
                raise ValueError("Storyboard editable fields must match the production director boundary")
            episode = payload.get("current_episode", {})
            ordered = [shot.get("id") for shot in episode.get("shots", [])]
            if (not isinstance(selected, list) or not selected
                    or any(not isinstance(sid, str) or not re.fullmatch(r"S[0-9]{2,3}", sid) for sid in selected + ordered)
                    or len(set(selected)) != len(selected) or not set(selected).issubset(ordered)
                    or len(set(ordered)) != len(ordered)):
                raise ValueError("Storyboard input contains invalid editable shot IDs")
            if payload.get("locked_shot_ids") != [sid for sid in ordered if sid not in selected]:
                raise ValueError("Storyboard locked scope does not match the frozen input")
            schema = storyboard_schema(selected, case["project"])
        for arm, prompt in (("baseline", baseline), ("candidate", candidate)):
            rendered = adapt_system(prompt, case["project"])
            meta = prompt_receipt(rendered, role, payload, schema)
            request = {"protocol": PROTOCOL, "role": role, "system": str(rendered),
                       "payload": payload, "output_schema": schema,
                       "model_identity": model, "config_revision": plan["config_revision"],
                       **plan["parameters"]}
            for replicate in range(1, samples + 1):
                task_id = digest([plan["experiment_id"], case["id"], replicate, arm])[:24]
                requests[task_id] = request
                plan["tasks"].append({"id": task_id, "case_id": case["id"], "arm": arm,
                                      "replicate": replicate, "request_sha256": digest(request), "prompt": meta})
    # Holdout inputs become visible as soon as requests are exported. Reserve
    # the release first, even if no generation or final comparison ever follows.
    reserve_holdout(plan, seal_path)
    output = new_directory(output)
    for directory in ("requests", "results", "reviews"):
        new_directory(output / directory)
    for ident, request in requests.items():
        write_new(output / "requests" / (ident + ".json"), request)
    write_new(output / "corpus.json", corpus)
    write_new(output / "plan.json", plan)
    write_new(seal_path, {"protocol": PROTOCOL, "experiment_id": plan["experiment_id"],
                          "plan_sha256": digest(plan), "corpus_sha256": digest(corpus),
                          "source_sha256": source["sha256"]})
    return plan


def verify(run, seal_path):
    """Verify a run against the operator's seal and the current exact protocol."""
    seal_path, run = _outside(seal_path, run)
    seal, plan = read_json(seal_path), read_json(run / "plan.json")
    if (seal.get("protocol") != PROTOCOL or plan.get("protocol") != PROTOCOL
            or seal.get("plan_sha256") != digest(plan)
            or seal.get("experiment_id") != plan.get("experiment_id")):
        raise ValueError("Experiment seal/checksum mismatch or incompatible protocol")
    if source_identity()["sha256"] != seal.get("source_sha256"):
        raise ValueError("Experiment source changed after the seal was frozen")
    corpus = read_json(run / "corpus.json")
    if digest(corpus) != seal.get("corpus_sha256") or digest(corpus) != plan["corpus_sha256"]:
        raise ValueError("Frozen corpus checksum mismatch")
    cases = _cases(corpus, plan["split"])
    if plan["cases"] != [{key: case[key] for key in ("id", "split", "genre", "negative_control", "conflict_case")}
                         for case in cases]:
        raise ValueError("Frozen case metadata mismatch")
    if len(plan["tasks"]) != len(cases) * plan["samples"] * 2:
        raise ValueError("Missing experiment replicate")
    expected = {(case["id"], arm, repeat) for case in cases for arm in ("baseline", "candidate")
                for repeat in range(1, plan["samples"] + 1)}
    found = {(t["case_id"], t["arm"], t["replicate"]) for t in plan["tasks"]}
    if found != expected or len({t["id"] for t in plan["tasks"]}) != len(plan["tasks"]):
        raise ValueError("Invalid replicate coverage")
    for task in plan["tasks"]:
        if not re.fullmatch(r"[0-9a-f]{24}", task["id"]):
            raise ValueError("Invalid experiment task ID")
        request = read_json(run / "requests" / (task["id"] + ".json"))
        if digest(request) != task["request_sha256"]:
            raise ValueError("Frozen request checksum mismatch")
    reserve_holdout(plan, seal_path, verify_only=True)
    return plan


if __name__ == "__main__":
    from .creative_experiment_cli import main
    raise SystemExit(main())
