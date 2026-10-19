"""Append-only project comparisons from recorded evidence; never invoke a model.

The private bundle freezes project inputs and observed call files. Public views
are explicit projections, not arbitrary raw project/receipt downloads. Missing
history stays unknown; a recorded review is not a new independent experiment.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import fcntl
from itertools import islice
import os
from pathlib import Path
import re
import shutil
import uuid

from studio.core import ROOT
from studio.http_security import HTTPError
from . import store
from .creative_evidence_io import digest, new_directory, read_bytes, read_json, write_new
from .evaluation_io import database_project, finite

SCHEMA = "ai-studio.quality-baseline.v1"
PROTOCOL = "recorded-project-comparison.v1"
ID = re.compile(r"[a-f0-9]{32}\Z")
INPUTS = ("brief", "format", "creation_mode", "production_profile", "creative_contract",
          "story_feedback", "script_feedback", "series_bible", "submitted_sources",
          "material_package", "revision_request", "record_kind", "genre", "visual_style",
          "episode_count", "duration_seconds", "source_text", "source_name", "outline", "dependency_context")
VERSION_KEYS = ("workflow_version", "proposal_generation", "script_generation", "style_generation",
                "production_revision", "production_edit_revision", "final_generation")
VERSION_KEYS += ("plan_version", "plan_cycle", "plan_attempt")


def _folder(pid):
    if not isinstance(pid, str) or not re.fullmatch(r"[a-f0-9]{12}", pid):
        raise ValueError("项目编号无效")
    return store.DATA / "quality-baselines" / pid


def _number(value):
    value = finite(value)
    return value if value is not None and value >= 0 else None


def _text(value, maximum=1000):
    return value[:maximum] if isinstance(value, str) else ""


def _capture_calls(project, root):
    """Freeze exact local files, retaining failed and unreadable call entries."""
    slug = project.get("slug", "")
    if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", slug):
        return [], {}, {}, ["项目未记录可读取的调用目录"]
    base = Path(root) / "production" / slug
    paths = list(islice(base.glob("**/calls/*/receipt.json"), 241))
    files, sources, calls, gaps = {}, {}, [], []
    if len(paths) > 240:
        gaps.append("调用记录超过 240 条，本次仅冻结前 240 条，累计指标不完整")
    total = 0
    for path in sorted(paths[:240]):
        try:
            receipt = read_json(path, maximum=512 * 1024)
            execution = receipt.get("execution_context") or {}
            if execution.get("project_id", project["id"]) != project["id"]:
                gaps.append("忽略归属其他项目的调用记录")
                continue
            index = len(calls)
            captured = {}
            for source in [path, *sorted(path.parent.glob("request-*.json"))[:6],
                           *sorted(path.parent.glob("context-*.json"))[:6],
                           *sorted(path.parent.glob("response-*.json"))[:6],
                           *list(path.parent.glob("parsed.json"))]:
                try:
                    if not re.fullmatch(r"(?:receipt|parsed)\.json|(?:request|context|response)-[0-9]{2}\.json", source.name):
                        raise ValueError("未知调用文件名")
                    raw = read_bytes(source)
                    total += len(raw)
                    if total > 32 * 1024 * 1024:
                        raise ValueError("调用证据超过 32 MiB")
                    value = read_json(source)
                    name = f"call-{index:03d}-{source.name}"
                    files[name] = value
                    sources[name] = source
                    captured[source.name] = digest(value)
                except (ValueError, OSError):
                    gaps.append("一份调用请求或输入文件不可读或超限，已保留可读的调用记录")
            if "receipt.json" not in captured:
                gaps.append("调用回执未能冻结，未计入运行指标")
                continue
            attempts = receipt.get("attempts")
            calls.append({"kind": "model_call", "role": _text(receipt.get("role"), 64),
                "call_id": _text(receipt.get("call_id") or path.parent.name, 100),
                "status": _text(receipt.get("status"), 64),
                "model": _text(receipt.get("response_model"), 160) or None,
                "configured_model": _text(receipt.get("configured_model", receipt.get("model")), 160) or None,
                "provider": _text(receipt.get("provider"), 64),
                "model_identity_sha256": digest(receipt.get("model_identity")) if receipt.get("model_identity") else None,
                "config_revision": _text(receipt.get("config_revision"), 100) or None,
                "prompt": {k: _text((receipt.get("prompt") or {}).get(k), 100) or None for k in
                           ("version", "status", "template_sha256", "contract_sha256", "input_sha256", "wire_system_sha256")},
                "parameters_sha256": digest(receipt.get("parameters")) if receipt.get("parameters") else None,
                "sampling": {k: finite((receipt.get("parameters") or {}).get(k))
                             for k in ("max_tokens", "temperature", "top_p")},
                "elapsed_seconds": _number(receipt.get("elapsed_seconds")),
                "schema_retries": max(0, len(attempts) - 1) if isinstance(attempts, list) else None,
                "stage": _text(execution.get("stage"), 64), "files": captured})
        except (ValueError, OSError, TypeError, AttributeError):
            gaps.append("一条调用证据缺失、不可读或超限，未用当前配置补写历史")
    return calls, files, sources, gaps


def _observations(project):
    generations = []
    for shot in (project.get("shots") or [])[:80]:
        for attempt in (shot.get("attempts") or [])[:100]:
            generations.append({"kind": "generation", "shot_id": shot.get("id"),
                "stage": _text(attempt.get("stage"), 64), "take": attempt.get("take"),
                "attempt": attempt.get("attempt"), "status": _text(attempt.get("state"), 64),
                "model": _text(attempt.get("model"), 160) or None,
                "elapsed_seconds": _number(attempt.get("elapsed_seconds")),
                "parameters_sha256": digest(attempt.get("parameters")) if attempt.get("parameters") else None,
                "source_signature": attempt.get("source_signature")})
    return generations


def _metrics(project, records):
    elapsed = [_number(row.get("elapsed_seconds")) for row in records]
    model_calls = [row for row in records if row["kind"] == "model_call"]
    costs = defaultdict(lambda: {"confirmed_micros": 0, "estimated_micros": 0,
                                  "confirmed_count": 0, "estimated_count": 0})
    tasks = project.get("evaluation_cloud_tasks", [])
    unknown = 0
    for task in tasks:
        currency = task.get("currency")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            unknown += 1
            continue
        result = costs[currency]
        for field in ("confirmed", "estimated"):
            value = task.get(field + "_cost_micros")
            if type(value) is int and value >= 0:
                result[field + "_micros"] += value
                result[field + "_count"] += 1
        confirmed = task.get("confirmed_cost_micros")
        unknown += int(type(confirmed) is not int or confirmed < 0)
    repairs = {}
    for name in ("creation_review", "script_review"):
        rounds = (project.get(name) or {}).get("rounds")
        repairs[name] = max(0, len(rounds) - 1) if isinstance(rounds, list) and rounds else None
    history = project.get("plan_history")
    repairs["plan_review"] = (len({(r.get("cycle"), r["attempt"]) for r in history
        if isinstance(r, dict) and type(r.get("attempt")) is int and r["attempt"] > 1})
        if isinstance(history, list) and history else None)
    retries = [row["schema_retries"] for row in model_calls]
    return {"scope": "cumulative_recorded_history_at_freeze",
        "observed_operations": len(records),
        "failed_operations": sum(row.get("status") in {"failed", "fail", "error", "rejected"} for row in records),
        "elapsed_seconds_sum": round(sum(x for x in elapsed if x is not None), 3) if any(x is not None for x in elapsed) else None,
        "elapsed_known_count": sum(x is not None for x in elapsed),
        "production_elapsed_seconds": _number((project.get("production") or {}).get("elapsed_seconds")),
        "schema_retries": sum(x for x in retries if x is not None) if any(x is not None for x in retries) else None,
        "review_repairs": repairs, "cloud_task_count": len(tasks),
        "costs": [{"currency": key, **value} for key, value in sorted(costs.items())],
        "unconfirmed_cost_count": unknown}


def _reviews(project):
    result = []
    for name in ("creation_review", "resolution_review", "script_review", "plan_review"):
        review = project.get(name)
        if isinstance(review, dict) and review:
            current = review.get("review") if isinstance(review.get("review"), dict) else review
            result.append({"scope": name, "shot_id": None, "evidence": current,
                           "evidence_sha256": digest(review)})
            if name == "creation_review" and review.get("rounds"):
                for proposal in review["rounds"][-1].get("reviews", [])[:20]:
                    if isinstance(proposal, dict):
                        result.append({"scope": "proposal", "shot_id": _text(proposal.get("proposal_id"), 64),
                            "evidence": proposal, "evidence_sha256": digest(proposal)})
    for shot in (project.get("shots") or [])[:80]:
        for stage in ("image", "video"):
            media = shot.get(stage) or {}
            qa = media.get("qa") or (shot.get("qa") if stage == "video" else None)
            if isinstance(qa, dict) and qa:
                result.append({"scope": stage, "shot_id": shot.get("id"), "evidence": qa,
                    "media_sha256": media.get("sha256"), "evidence_sha256": digest(qa)})
        timing = shot.get("audio_timing") or shot.get("dialogue_measurement")
        if isinstance(timing, dict) and timing:
            result.append({"scope": "voice_timing", "shot_id": shot.get("id"),
                "evidence": {**timing, "passed": timing.get("fits")}, "evidence_sha256": digest(timing)})
    delivery = project.get("delivery") or {}
    for name in ("clean", "subtitled"):
        qa = (delivery.get("technical_qa") or {}).get(name)
        if isinstance(qa, dict) and qa:
            result.append({"scope": "delivery_" + name, "shot_id": None, "evidence": qa,
                "evidence_sha256": digest(qa)})
    return result


def _public_review(row):
    evidence = row["evidence"]
    issues = []
    candidates = [evidence, evidence.get("technical") or {}, evidence.get("semantic") or {}]
    for value in candidates:
        for issue in (value.get("issues") or [])[:30]:
            if isinstance(issue, dict):
                issues.append({k: _text(issue.get(k)) for k in ("code", "severity", "description", "detail", "evidence", "suggestion")})
            elif isinstance(issue, str):
                issues.append({"description": _text(issue)})
    passed = evidence.get("passed")
    status = "pass" if passed is True else "fail" if passed is False else evidence.get("status", "recorded")
    return {"scope": row["scope"], "shot_id": row["shot_id"],
        "status": status if status in {"pass", "fail", "unavailable", "recorded", "technical_pass_semantic_pending"} else "recorded",
        "summary": _text(evidence.get("summary")), "issues": issues,
        "scores": {k: finite(v) for k, v in (evidence.get("scores") or {}).items()
                   if k in {"hook", "causality", "motivation", "originality", "closure", "feasibility"}},
        "evidence_sha256": row["evidence_sha256"], "media_sha256": row.get("media_sha256"),
        "binding": "recorded_review_not_revalidated"}


def _summary(bundle):
    return {k: bundle[k] for k in ("schema", "protocol", "id", "project_id", "label", "created_at",
        "project_revision", "versions", "capture_identity", "input_sha256", "project_sha256", "source_sha256", "gaps", "metrics")}


def _load(pid, ident, *, verify_calls=True):
    if not isinstance(ident, str) or not ID.fullmatch(ident):
        raise ValueError("基线编号无效")
    folder = _folder(pid) / ident
    manifest = read_json(folder / "manifest.json")
    if manifest.get("schema") != SCHEMA or not isinstance(manifest.get("files"), dict) or len(manifest["files"]) > 5000:
        raise ValueError("基线清单无效")
    for name, expected in manifest.get("files", {}).items():
        if not re.fullmatch(r"(?:record|project|call-[0-9]{3}-(?:receipt|parsed|request-[0-9]{2}|context-[0-9]{2}|response-[0-9]{2}))\.json", name):
            raise ValueError("基线清单无效")
        if (verify_calls or name in {"record.json", "project.json"}) and digest(read_json(folder / name)) != expected:
            raise ValueError("基线证据校验失败，历史版本未改写")
    if "record.json" not in manifest.get("files", {}) or "project.json" not in manifest["files"]:
        raise ValueError("基线证据缺失")
    value = read_json(folder / "record.json")
    if value.get("schema") != SCHEMA or value.get("id") != ident or value.get("project_id") != pid:
        raise ValueError("基线归属无效")
    return value


def create(pid, payload, *, root=ROOT):
    if (not isinstance(payload, dict) or set(payload) != {"request_id", "label", "expected_revision"}
            or not isinstance(payload["request_id"], str) or not ID.fullmatch(payload["request_id"])
            or not isinstance(payload["label"], str) or not 1 <= len(payload["label"].strip()) <= 80
            or type(payload["expected_revision"]) is not int):
        raise HTTPError(400, "invalid_baseline", "请提供快照名称、当前项目版本和唯一请求编号")
    parent = _folder(pid)
    project = database_project(store.DATABASE, pid)
    if any(path.is_symlink() for path in (parent, *parent.parents)):
        raise ValueError("基线目录不能使用符号链接")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Serializes duplicate clicks/retries across processes, without a project lock.
    with (parent / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        destination = parent / payload["request_id"]
        if destination.exists():
            existing = _load(pid, payload["request_id"])
            if existing["request_sha256"] != digest(payload):
                raise ValueError("相同请求编号不能用于另一份基线")
            return _summary(existing)
        if project["revision"] != payload["expected_revision"]:
            raise HTTPError(409, "stale_project", "项目已更新，请同步后重新冻结")
        if len(list(islice(parent.glob("[a-f0-9]" * 32), 101))) >= 100:
            raise ValueError("该项目已保存 100 份基线，请使用新项目开展后续实验")
        calls, files, sources, gaps = _capture_calls(project, root)
        shots = project.get("shots") or []
        if len(shots) > 80 or any(len(s.get("attempts") or []) > 100 for s in shots[:80]):
            gaps.append("制作尝试超过快照展示上限，累计指标仅包含已捕获部分")
        records = calls + _observations(project)
        inputs = {key: project.get(key) for key in INPUTS}
        from .system_info import public_system_info
        bundle = {"schema": SCHEMA, "protocol": PROTOCOL, "id": payload["request_id"], "project_id": pid,
            "request_sha256": digest(payload), "label": payload["label"].strip(),
            "created_at": datetime.now(timezone.utc).isoformat(), "project_revision": project["revision"],
            "versions": {key: project.get(key) for key in VERSION_KEYS},
            "capture_identity": {"service_source_commit": public_system_info()["source_commit"],
                                 "snapshot_code_sha256": digest(Path(__file__).read_text())},
            "input_sha256": digest(inputs), "project_sha256": digest(project),
            "source_sha256": digest({name: digest(value) for name, value in files.items()}),
            "gaps": gaps, "records": records, "reviews": [_public_review(row) for row in _reviews(project)],
            "metrics": _metrics(project, records), "quality_conclusion": "not_established"}
        temporary = new_directory(parent / (".pending-" + uuid.uuid4().hex))
        try:
            files.update({"record.json": bundle, "project.json": project})
            for name, value in files.items():
                write_new(temporary / name, value)
            # Digests describe canonical JSON values, independent of trailing newline.
            write_new(temporary / "manifest.json", {"schema": SCHEMA, "files": {name: digest(value) for name, value in files.items()}})
            latest = database_project(store.DATABASE, pid)
            if latest["revision"] != project["revision"]:
                raise HTTPError(409, "stale_project", "冻结期间项目已更新，请同步后重试")
            for name, source in sources.items():
                if digest(read_json(source)) != digest(files[name]):
                    raise HTTPError(409, "changing_evidence", "冻结期间调用记录已更新，请重试")
            os.rename(temporary, destination)
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return _summary(bundle)


def inventory(pid):
    database_project(store.DATABASE, pid)  # No creation/migration on a GET.
    values, invalid = [], 0
    parent = _folder(pid)
    for path in islice(parent.glob("[a-f0-9]" * 32), 100):
        try:
            values.append(_summary(_load(pid, path.name, verify_calls=False)))
        except (ValueError, OSError):
            invalid += 1
    return {"project_id": pid, "baselines": sorted(values, key=lambda v: (v["created_at"], v["id"]), reverse=True),
            "unreadable_baselines": invalid}


def detail(pid, ident):
    database_project(store.DATABASE, pid)
    value = _load(pid, ident)
    return {**_summary(value), "records": value["records"], "reviews": value["reviews"],
            "quality_conclusion": "not_established"}


def compare(pid, baseline, candidate):
    before, after = detail(pid, baseline), detail(pid, candidate)
    reasons = []
    if baseline == candidate:
        reasons.append("请选择两份不同的基线")
    if before["input_sha256"] != after["input_sha256"]:
        reasons.append("需求、反馈、制作规格或固定材料发生变化，不能作为同输入效果实验")
    if before["protocol"] != after["protocol"]:
        reasons.append("比较协议版本不同")
    changes = []
    old = {(r["scope"], r["shot_id"]): r for r in before["reviews"]}
    new = {(r["scope"], r["shot_id"]): r for r in after["reviews"]}
    for key in sorted(old.keys() | new.keys(), key=repr):
        a, b = old.get(key), new.get(key)
        if (a or {}).get("evidence_sha256") != (b or {}).get("evidence_sha256"):
            changes.append({"scope": key[0], "shot_id": key[1], "before": a, "after": b})
    return {"schema": "ai-studio.quality-comparison.v1", "baseline": before, "candidate": after,
            "same_inputs": not reasons, "reasons": reasons, "review_changes": changes,
            "quality_conclusion": "not_established", "blind_evaluation_status": "not_run",
            "note": "历史审核与累计运行记录的版本比较；真实效果结论须使用冻结输入的匿名评测。"}
