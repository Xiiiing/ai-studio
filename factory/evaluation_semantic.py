"""Optional temporal and cross-shot VLM review using the existing model adapter."""
from __future__ import annotations

import copy
import json

from .evaluation_io import check_receipt, row
from .evaluation_sampling import sample_video
from .series_bindings import shot_roles


def pairs_for(project):
    shots = project.get("shots", [])
    cast = (project.get("episode") or project.get("script") or {}).get("cast", {})
    pairs, last = [], {}
    for index, shot in enumerate(shots):
        if index:
            pairs.append((shots[index - 1]["id"], shot["id"]))
        for role in shot_roles(shot, cast):
            if role in last:
                pairs.append((last[role], shot["id"]))
            last[role] = shot["id"]
    return list(dict.fromkeys(pairs))[:120]


def pair_schema(evidence_ids):
    check = {"type":"object", "additionalProperties":False,
        "properties":{"status":{"type":"string","enum":["pass","fail","uncertain"]},
                      "evidence_ids":{"type":"array","minItems":1,"maxItems":8,"uniqueItems":True,
                                      "items":{"type":"string","enum":evidence_ids}},
                      "reason":{"type":"string","minLength":4,"maxLength":1200}},
        "required":["status","evidence_ids","reason"]}
    return {"type":"object","additionalProperties":False,"properties":{
        "checks":{"type":"object","additionalProperties":False,
                  "properties":{key:copy.deepcopy(check) for key in ("identity","props","causality")},
                  "required":["identity","props","causality"]}},"required":["checks"]}


def evaluate_semantics(project, root, config, output_dir):
    from .llm import review_media, _request, _image_url, _review_expectations
    from .config import llm_identity
    from .providers import config_revision
    if config.get("quality", {}).get("semantic_enabled", True) is not True:
        return [row(name, "vlm_review", "not_run", source="vlm") for name in ("causality", "identity", "props", "action")]
    checks, samples, definitions = [], {}, {shot["id"]:shot for shot in project.get("shots", [])}
    references = {}
    for role, receipt in project.get("assets", {}).get("characters", {}).items():
        try:
            references[role] = check_receipt(root, receipt)
        except (ValueError, OSError, KeyError):
            checks.append(row("identity", "reference_integrity", "unavailable", evidence=[{"role":role}], source="vlm"))
    for sid, shot in definitions.items():
        try:
            path, digest = check_receipt(root, shot.get("video") or {})
            frames, metadata = sample_video(path)
            samples[sid] = frames
            cast = (project.get("episode") or project.get("script") or {}).get("cast", {})
            paths = [references[role][0] for role in shot_roles(shot,cast) if role in references][:4]
            result = review_media(project, shot, path, paths, config=config, output_dir=output_dir)
            status = result.get("status") if result.get("status") in {"pass", "fail"} else "unavailable"
            checks.append(row("action", "temporal_vlm", status, shot_id=sid, metrics=result,
                              evidence=[{"media_sha256":digest, "sampling":metadata}], source="vlm"))
        except (ValueError, OSError, RuntimeError) as exc:
            checks.append(row("action", "temporal_vlm", "unavailable", shot_id=sid, evidence=[str(exc)[:1000]], source="vlm"))
    for previous, current in pairs_for(project):
        if previous not in samples or current not in samples:
            for category in ("identity", "props", "causality"):
                checks.append(row(category, "cross_shot_vlm", "unavailable", shot_id=current, evidence=[{"previous_shot":previous}], source="vlm"))
            continue
        evidence, content = [], []
        for sid, frames in ((previous, samples[previous][-2:]), (current, samples[current][:2])):
            for frame in frames:
                key = sid + ":frame_" + str(frame["frame_index"])
                evidence.append({"id":key, "shot_id":sid, "timestamp":frame["timestamp"]})
                content.extend([{"type":"text","text":key + " at " + str(frame["timestamp"]) + "s"},
                                {"type":"image_url","image_url":{"url":_image_url(frame["image"])}}])
        for role, (path, digest) in list(references.items())[:4]:
            key = "REFERENCE:" + role
            evidence.append({"id":key, "role":role, "media_sha256":digest, "scope":"identity_reference_not_candidate_action"})
            content.extend([{"type":"text","text":key + "，仅作身份与外形参考，不是剧情证据"},
                            {"type":"image_url","image_url":{"url":_image_url(path)}}])
        expected = [_review_expectations(definitions[sid]) for sid in (previous, current)]
        content.insert(0, {"type":"text", "text":json.dumps({"project_id":project.get("id"),
            "production_revision":project.get("production_revision"),"shots":expected,
            "pinned_references":{sid:project.get("series_references", {}).get(sid, []) for sid in (previous,current)},
            "evidence_catalog":evidence}, ensure_ascii=False)})
        try:
            schema = pair_schema([value["id"] for value in evidence])
            result = _request(config, "visual_critic",
                "你是独立跨镜审片员。分别检查稳定身份、道具数量/持有人与可见因果。只引用evidence_catalog中实际帧号，"
                "明确区分身份与允许变化的衣服、手持物和剧情状态。静帧不能证明未采样时段发生了交接；证据不足用uncertain。"
                "不能因参考ID相同就认定画中人物相同，不得把相机运动当作人物表演。JSON输出。",
                None, schema, output_dir, max_tokens=2200, temperature=0, content=content)
            from jsonschema import validate
            validate(result, schema)
            for category, value in result["checks"].items():
                checks.append(row(category, "cross_shot_vlm", "review_needed" if value["status"] == "uncertain" else value["status"],
                    shot_id=current, evidence=[entry for entry in evidence if entry["id"] in value["evidence_ids"]],
                    metrics={"previous_shot":previous, "reason":value["reason"], "model_identity":llm_identity(config,"visual_critic"),
                             "config_revision":config_revision(config)}, source="vlm"))
        except Exception as exc:
            for category in ("identity", "props", "causality"):
                checks.append(row(category, "cross_shot_vlm", "unavailable", shot_id=current, evidence=[type(exc).__name__ + ": " + str(exc)[:500]], source="vlm"))
    return checks
