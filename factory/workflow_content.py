"""Current business materials for each real graph step, separate from call history."""
from __future__ import annotations

import json
from .workflow_presentation import LABELS

SOURCES = {
    "research": "research", "sources": "research", "proposals": "proposals",
    "creation_review": "proposals", "selected_story": "story_decision",
    "resolved_story_brief": "story_resolution", "resolution_review": "story_resolution",
    "screenplay_draft": "screenplay", "storyboard": "storyboard", "storyboard_receipt": "storyboard",
    "script": "script_review", "script_review": "script_review", "styles": "visual_design",
    "style_previews": "visual_development", "selected_style": "style_decision",
    "shots": "production", "audio": "production", "delivery": "production",
    "technical_checks": "production", "final_answer": "final_decision", "accepted_delivery": "delivery",
}


def step_materials(project, root, artifact_url):
    from .team_blueprint import topology
    nodes, _ = topology(project.get("workflow_version", 1))
    ids = {n["id"] for n in nodes}
    artifacts = project.get("planning_artifacts") or {}
    proposals, styles = project.get("proposals") or [], project.get("styles") or []
    selected_story, selected_style = project.get("selected_story"), project.get("selected_style")
    if isinstance(selected_story, str):
        selected_story = next((v for v in proposals if v.get("id") == selected_story), selected_story)
    if isinstance(selected_style, str):
        selected_style = next((v for v in styles if v.get("id") == selected_style), selected_style)
    final = next((d for d in reversed(project.get("decisions") or []) if d.get("type") == "final"), None)
    values = {
        **project, "selected_story": selected_story, "selected_style": selected_style,
        "sources": (project.get("research") or {}).get("sources") or project.get("sources"),
        "resolution_review": (project.get("resolved_story_brief") or {}).get("resolution_review"),
        "producer_feedback": project.get("script_feedback") or project.get("story_feedback"),
        "style_previews": [{"id": s.get("id"), "name": s.get("name"), "preview": s.get("preview_url") or s.get("preview")} for s in styles],
        "audio": (project.get("assets") or {}).get("audio"),
        "approved_assets": project.get("assets"), "revised_script": project.get("script"),
        "final_answer": final if final and final.get("status") == "answered" else None,
        "accepted_delivery": project.get("delivery") if project.get("status") == "approved" else None,
    }
    aliases = {"resolution_review": "resolved_story_review", "storyboard_receipt": "storyboard_direction",
               "revised_script": "script"}

    def material(key, side):
        value = values.get(key)
        has_value = value is not None and value != "" and value != [] and value != {}
        recorded = artifacts.get(aliases.get(key, key))
        if key in ("delivery", "accepted_delivery"):
            recorded = (project.get("delivery") or {}).get("manifest_url")
        url = artifact_url(root, recorded)
        source = SOURCES.get(key)
        if source not in ids:
            source = "screenplay" if source in ("story_resolution", "storyboard", "script_review") else source
        row = {"key": key, "label": LABELS.get(key, key), "source": "current_project_snapshot",
               "source_label": "当前项目材料", "source_node": source,
               "state": "available" if has_value or url else "not_recorded" if side == "inputs" else "not_generated"}
        if has_value:
            # Full documents remain available through their files; keep the polling snapshot bounded.
            raw = json.dumps(value, ensure_ascii=False, default=str)
            if len(raw.encode("utf-8")) <= 65536:
                row["value"] = value
            else:
                row.update(truncated=True, state="too_large")
        if url:
            row["artifact_url"] = url
        elif recorded:
            row["artifact_state"] = "missing"
        return row

    result = []
    for node in nodes:
        entry = {"id": node["id"], "inputs": [material(k, "inputs") for k in node["input_keys"]],
                 "outputs": [material(k, "outputs") for k in node["output_keys"]]}
        if node["id"] == "visual_development":
            for style in styles[:12]:
                url = artifact_url(root, style.get("preview_url") or (style.get("preview") or {}).get("path"))
                if url:
                    entry["outputs"].append({"label": style.get("name") or "视觉预览", "artifact_url": url})
        if node["id"] in ("production", "final_decision", "delivery"):
            for key, label in (("video_url", "成片"), ("subtitle_url", "字幕"), ("timeline_url", "剪辑时间线")):
                url = artifact_url(root, (project.get("delivery") or {}).get(key))
                if url:
                    entry["inputs" if node["id"] == "final_decision" else "outputs"].append({"label": label, "artifact_url": url})
        result.append(entry)
    return result
