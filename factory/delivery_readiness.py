"""Cheap recorded-state handoff checks; no filesystem reads or approval writes."""
from __future__ import annotations

import copy
import re
from urllib.parse import quote

REQUIRED = {
    "episode.mp4": "带字幕成片", "episode_clean.mp4": "无字幕成片",
    "subtitles.zh.srt": "中文字幕", "cover.jpg": "封面",
    "timeline.json": "剪辑时间线", "audio_edit.json": "声音制作记录",
    "technical_qa.json": "技术质检记录",
}
PLATFORM_CHECKS = (
    ("account", "核对红果官方后台的账号与发布权限"),
    ("platform_specs", "按当前官方后台核对时长、画幅、编码及提交材料"),
    ("content_review", "确认内容审核、备案或许可等适用要求"),
    ("rights", "核验剧本、音乐、字体、人物与声音等素材授权"),
    ("ai_disclosure", "核验视频及文件中的 AI 标识和上传时的声明"),
)


def _object(value):
    return value if isinstance(value, dict) else {}


def _positive(value):
    return type(value) is int and 1 <= value <= 2147483647


def _artifacts(project, delivery):
    raw = delivery.get("artifacts", [])
    if not isinstance(raw, list) or len(raw) > 32:
        return [], ["invalid_artifact_collection"]
    rows, errors, names, parents = [], [], set(), set()
    pid, revision = project.get("id"), project.get("production_revision")
    owned = (isinstance(pid, str) and re.fullmatch(r"[a-f0-9]{12}", pid)
             and project.get("slug") == "factory-" + pid and _positive(revision))
    for receipt in raw:
        receipt = _object(receipt)
        path, sha, size = receipt.get("path"), receipt.get("sha256"), receipt.get("bytes")
        if not isinstance(path, str) or len(path) > 512 or "\\" in path:
            errors.append("invalid_artifact_receipt")
            continue
        parts = path.split("/")
        valid = (owned and len(parts) == 5 and parts[:3] == ["production", "factory-" + pid, "release"]
                 and re.fullmatch(r"v" + str(revision) + r"-[a-f0-9]{12}(?:-retry-[0-9]+)?", parts[3])
                 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", parts[4])
                 and isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha)
                 and type(size) is int and size > 0)
        if not valid:
            errors.append("invalid_artifact_receipt")
            continue
        if parts[4] in names:
            errors.append("duplicate_artifact_name")
            continue
        names.add(parts[4])
        parents.add("/".join(parts[:4]))
        rows.append({"name": parts[4], "label": REQUIRED.get(parts[4], parts[4]), "path": path,
                     "sha256": sha, "bytes": size, "status": "recorded_not_verified",
                     "url": "/media/" + quote(path, safe="/")})
    if len(parents) > 1:
        errors.append("mixed_release_directories")
    return rows, sorted(set(errors))


def snapshot(project):
    """Record presence is not file integrity, current decoding or platform approval."""
    delivery = _object(project.get("delivery"))
    version = project.get("production_revision")
    current = (_positive(version) and type(delivery.get("revision")) is int and delivery["revision"] == version
               and type(project.get("delivery_complete_revision")) is int and project["delivery_complete_revision"] == version
               and delivery.get("project_id") == project.get("id") and delivery.get("slug") == project.get("slug"))
    artifacts, problems = _artifacts(project, delivery)
    missing = sorted(set(REQUIRED) - {row["name"] for row in artifacts})
    final_id = f"{project.get('id')}:final:{version}"
    answer = _object(_object(project.get("answers")).get(final_id))
    effect = _object(_object(project.get("decision_effects")).get(final_id))
    human = bool(current and project.get("status") == "approved" and project.get("accepted_by") == "producer"
                 and answer.get("gate_id") == final_id and answer.get("type") == "final"
                 and answer.get("choice") == "approve" and answer.get("actor") == "producer"
                 and effect.get("route") == "done")
    qa = _object(delivery.get("technical_qa"))
    recorded_qa = all(_object(qa.get(key)).get("status") == "pass" for key in ("clean", "subtitled"))
    metadata_ready = bool(current and not missing and not problems and recorded_qa and human)
    checks = [
        {"id": "current_release", "label": "交付物属于当前制作版本", "status": "recorded" if current else "blocked"},
        {"id": "required_materials", "label": "提交材料记录齐全", "status": "recorded" if not missing and not problems else "blocked",
         "missing": missing, "problems": problems},
        {"id": "recorded_technical_qa", "label": "已记录的导出技术检查", "status": "recorded" if recorded_qa else "blocked"},
        {"id": "producer_final", "label": "当前版本已由出品人人工终审", "status": "recorded" if human else "human_required"},
        {"id": "fresh_acceptance", "label": "重新验收文件、音画及版本证据", "status": "not_run"},
    ]
    return {"schema": "ai-studio.delivery-readiness.v1", "project_id": project.get("id"),
            "project_revision": project.get("revision"), "production_revision": version,
            "status": "ready_for_fresh_acceptance" if metadata_ready else "blocked",
            "evidence_basis": "recorded_snapshot_only", "files_read": False,
            "engineering_ready": False, "platform_ready": False, "platform_approval": "not_verified",
            "checks": checks, "artifacts": artifacts,
            "platform_checklist": [{"id": ident, "label": label, "status": "human_required"} for ident, label in PLATFORM_CHECKS],
            "limitations": ["材料回执和历史质检不等于当前文件仍完好。", "该清单不是红果官方上传规范或审核通过证明。"]}


def bind_acceptance(project, report):
    """Used only with a newly executed local acceptance report by submission.main."""
    from studio.core import fingerprint
    result = snapshot(project)
    provenance = _object(report.get("acceptance"))
    bound = (report.get("schema") == "ai-studio.film-evaluation.v1"
             and provenance.get("schema") == "ai-studio.production-acceptance.v2"
             and report.get("project_id") == project.get("id")
             and report.get("production_revision") == project.get("production_revision")
             and provenance.get("input_snapshot_sha256") == fingerprint(project))
    ready = bool(bound and result["status"] == "ready_for_fresh_acceptance"
                 and report.get("technical_passed") is True and report.get("human_approved") is True
                 and report.get("verdict") == "human_approved")
    result.update(evidence_basis="new_local_acceptance" if bound else "acceptance_binding_failed",
                  engineering_ready=ready, status="engineering_ready_platform_pending" if ready else "blocked")
    result["checks"][-1]["status"] = "passed" if ready else "blocked"
    result["acceptance"] = {"bound": bound, "run_id": report.get("run_id"),
        "media_signature": report.get("media_signature"), "verdict": report.get("verdict"),
        "input_snapshot_sha256": provenance.get("input_snapshot_sha256")}
    return copy.deepcopy(result)
