#!/usr/bin/env python3
"""Seed an explicitly isolated UI preview. No worker, network or model calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
NOTICE = "示例模拟数据，仅供新版界面体验；未调用模型、未产生供应商费用。操作会保存，但未启动 worker，不会继续生成。"
PREFIX = "cami-preview-seed-v1:"


def validate_workspace(value):
    workspace = Path(value).expanduser().resolve()
    if workspace.name != "cami-preview" or workspace.parent.name != "runtime":
        raise ValueError("预览目录必须是 runtime/cami-preview")
    if not SOURCE.is_relative_to(workspace):
        raise ValueError("请在 runtime/cami-preview 内的隔离发布目录运行播种脚本")
    for name in ("DRAMA_FACTORY_DATA", "DRAMA_FACTORY_CONFIG"):
        raw = os.environ.get(name)
        if not raw or not Path(raw).is_absolute():
            raise ValueError(name + " 必须显式指定绝对路径")
        target = Path(raw).resolve()
        if not target.is_relative_to(workspace) or target == workspace:
            raise ValueError(name + " 必须位于隔离的预览目录内")
    configuration = json.loads(Path(os.environ["DRAMA_FACTORY_CONFIG"]).read_text())
    profiles = [configuration.get("llm", {}), *configuration.get("llm_profiles", {}).values(),
                *configuration.get("video_profiles", {}).values()]
    if any(profile and profile.get("enabled") is not False for profile in profiles):
        raise ValueError("预览配置中的模型连接必须全部显式禁用")
    media = SOURCE / "production"
    if media.is_symlink() or not media.resolve().is_relative_to(workspace):
        raise ValueError("预览媒体目录必须位于隔离发布目录内，不能链接正式素材")
    return workspace


def make_media(folder):
    """CPU-only illustrations and six-second test pattern, never an AI output."""
    from PIL import Image, ImageDraw
    import imageio_ffmpeg

    folder.mkdir(parents=True, exist_ok=True)
    images = []
    for name, background, accent, caption in (
        ("warm.png", "#f4e9df", "#bb6b44", "A / WARM PAPER"),
        ("blue.png", "#e4ebf2", "#496786", "B / COOL INK"),
    ):
        target = folder / name
        if not target.exists():
            image = Image.new("RGB", (360, 640), background)
            draw = ImageDraw.Draw(image)
            draw.rectangle((24, 165, 336, 420), fill=accent)
            draw.ellipse((134, 210, 226, 302), fill=background)
            draw.rounded_rectangle((108, 314, 252, 438), radius=24, fill=accent)
            draw.text((24, 40), "AI STUDIO / SYNTHETIC PREVIEW", fill=accent)
            draw.text((24, 505), caption, fill=accent)
            draw.text((24, 540), "CPU illustration / no model calls", fill=accent)
            image.save(target)
        images.append(target)
    clip = folder / "preview.mp4"
    if not clip.exists():
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-n", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=24:duration=6",
                        "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(clip)], check=True, timeout=45)
    return images, clip


def sample_script():
    return {
        "title": "示例 · 旧书店的留言",
        "logline": "年轻修书师从一本旧书中的留言，发现父亲未寄出的道歉。",
        "cast": {"char_lead": "成年修书师林舟", "char_father": "成年父亲林远"},
        "shots": [
            {"id": "S01", "seconds": 6, "dramatic_beat": "发现夹在旧书里的留言",
             "prompt": "Synthetic preview: bookshop with an unopened handwritten letter",
             "motion_prompt": "Slow camera push", "camera_motion": "slow_push",
             "characters": ["char_lead"], "dialogue": [], "status": "planned", "shot_revision": 1},
            {"id": "S02", "seconds": 6, "dramatic_beat": "林舟认出父亲的字迹，决定回家",
             "prompt": "Synthetic preview: adult reading a letter beside a bookshop window",
             "motion_prompt": "Static framing", "camera_motion": "hold",
             "characters": ["char_lead"], "dialogue": [], "status": "planned", "shot_revision": 1},
        ],
    }


def task_record(project, stage, status):
    from factory import store, team_tasks

    contract = team_tasks.definition(stage, project)
    signature = team_tasks._signature(project, stage)
    return {"run_id": "preview-" + stage, "task_id": stage + ":" + signature,
            "stage": stage, "owner": contract["owner"], "reviewer": contract["reviewer"],
            "status": status, "attempt": 1, "input_signature": signature,
            "started_at": store.now(), "finished_at": store.now(),
            "error_type": "SyntheticReviewExample" if status == "blocked" else None,
            "model": "示例模拟 · 未调用模型", "completion_semantics": "synthetic_preview_not_execution_evidence"}


def seed_one(kind, title, build):
    from factory import store

    request_id = PREFIX + kind
    with store.connection() as database:
        existing = database.execute("SELECT project_id FROM idempotency WHERE key=?", (request_id,)).fetchone()
    if existing:
        # Never restore an old gate or overwrite a person's choices on restart.
        return {"kind": kind, "id": existing["project_id"], "created": False}
    project = store.create({"request_id": request_id, "title": title, "brief": NOTICE,
                            "format": "motion_comic", "duration_seconds": 60,
                            "team_mode": "guided", "workflow_version": 2, "is_validation": True})
    job = store.claim_job(project["id"])
    try:
        build(project)
        store.update(project["id"], {"preview_sample": True, "preview_notice": NOTICE})
        store.event(project["id"], NOTICE, "preview_sample", {"synthetic": True, "model_calls": 0})
    except BaseException:
        if job:
            store.finish_job(job["id"], "Preview seeding failed", lease=job["lease"])
        raise
    if job:
        store.finish_job(job["id"], lease=job["lease"])
    return {"kind": kind, "id": project["id"], "created": True}


def story(project):
    from factory import store

    proposals = [
        {"id": "A", "title": "示例 · 旧书店的留言", "eligible": True,
         "logline": "修书师发现父亲未寄出的道歉，必须决定是否赴约。",
         "synopsis": "以一本旧书连接父子关系，围绕发现、误解和主动赴约展开。",
         "review": {"summary": "模拟审核：冲突明确，适合短片结构。"}},
        {"id": "B", "title": "示例 · 末班车的约定", "eligible": True,
         "logline": "两位多年未见的朋友在末班车相遇，重新面对当年的承诺。",
         "synopsis": "通过一段有限时长的车程，推动人物说出长期隐瞒的真相。",
         "review": {"summary": "模拟审核：场景集中，结尾需要强调人物的主动选择。"}},
    ]
    current = store.update(project["id"], {"proposals": proposals, "proposals_complete_generation": 1,
                                         "creation_review": {"passed": True, "synthetic": True}, "progress": 22})
    store.update(project["id"], {"team_tasks": {"proposals": task_record(current, "proposals", "succeeded")}})
    store.gate(project["id"], "story", proposals + [{"id": "__repropose__", "label": "全部不满意，重新提案"}],
               "示例故事候选比较；提交后保存决定，预览没有生成执行器。", 1)


def script_review(project):
    from factory import store

    issues = [{"code": "motivation", "severity": "blocking", "shot_ids": ["S02"],
               "description": "模拟审核：林舟从读信到决定回家的动机跳跃过快。",
               "evidence": "示例分镜 S02 只有情绪反应，没有说明他为什么愿意主动赴约。",
               "suggestion": "保留旧书店场景，在 S02 加入父亲当年留给他的修书工具，强化情感触发。"}]
    script = sample_script()
    current = store.update(project["id"], {"selected_story": "A", "script": script, "shots": script["shots"],
        "stage": "script_review", "status": "blocked", "progress": 43,
        "script_review": {"passed": False, "issues": issues, "synthetic": True},
        "needs_decision": {"type": "creative_review", "kind": "script_review", "stage": "script_review",
                           "message": "示例独立审稿未通过，请填写修改要求。", "issues": issues}})
    store.update(project["id"], {"team_tasks": {"script_review": task_record(current, "script_review", "blocked")}})


def visual(project):
    from factory import store

    folder = SOURCE / "production" / project["slug"] / "preview"
    images, _ = make_media(folder)
    styles = [{"id": "style-A", "name": "示例 · 暖纸写意", "description": "暖白纸纹与柔和棕色，突出旧书店的亲近感。",
               "preview_url": "/media/" + images[0].relative_to(SOURCE).as_posix()},
              {"id": "style-B", "name": "示例 · 冷调墨色", "description": "克制的蓝灰色与几何构图，强调人物之间的距离。",
               "preview_url": "/media/" + images[1].relative_to(SOURCE).as_posix()}]
    script = sample_script()
    current = store.update(project["id"], {"selected_story": "A", "script": script, "shots": script["shots"],
        "styles": styles, "script_complete_generation": 1, "style_generation": 1, "style_previews_generation": 1,
        "script_review": {"passed": True, "synthetic": True}, "progress": 55})
    store.update(project["id"], {"team_tasks": {"visual_design": task_record(current, "visual_design", "succeeded")}})
    store.gate(project["id"], "style", styles, "示例 CPU 插图，供比较布局；可选择全部不满意并填写重新设计意见。", 1)


def final_review(project):
    from factory import store

    folder = SOURCE / "production" / project["slug"] / "preview"
    images, clip = make_media(folder)
    script = sample_script()
    shot = {**script["shots"][0], "dramatic_beat": "示例 CPU 测试图案，用于验证播放器；不是模型成片。",
            "image_url": "/media/" + images[0].relative_to(SOURCE).as_posix(),
            "video_url": "/media/" + clip.relative_to(SOURCE).as_posix()}
    store.update(project["id"], {"selected_story": "A", "selected_style": "style-A", "script": script,
        "shots": [shot], "progress": 95, "delivery_complete_revision": 1,
        "script_review": {"passed": True, "synthetic": True},
        "delivery": {"video_url": shot["video_url"], "files": [], "synthetic": True},
        "production": {"phase": "delivery_ready", "gpu_seconds": 0}})
    store.gate(project["id"], "final", [{"id": "approve", "label": "确认示例终片"},
                                      {"id": "revise", "label": "填写返修意见"},
                                      {"id": "reedit", "label": "重新剪辑"}],
               "示例终审：六秒 CPU 测试图案，只用于播放器和决策交互体验。", 1)


def source_recovery(project):
    from factory import store

    store.update(project["id"], {"status": "blocked", "stage": "research", "progress": 8,
        "creation_mode": "adaptation", "needs_decision": {"type": "creative_review", "kind": "source_rights",
        "stage": "research", "message": "示例素材缺少创作授权，补充自有素材或切换原创后可继续。",
        "issues": [{"code": "source_rights", "description": "模拟问题：当前故事参考仅可分析，尚无改编授权。",
                    "suggestion": "提交自有或已获授权的素材；也可以保留主题并切换原创。"}]}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="隔离的 runtime/cami-preview 目录")
    args = parser.parse_args()
    validate_workspace(args.workspace)
    from factory import store

    store.initialize()
    samples = [seed_one(kind, title, build) for kind, title, build in (
        ("story", "示例 · 故事方向待选择", story),
        ("script", "示例 · 独立审稿与剧本返修", script_review),
        ("visual", "示例 · 人物与画风比较", visual),
        ("final", "示例 · 终片审看与返修", final_review),
        ("sources", "示例 · 补充素材或切回原创", source_recovery),
    )]
    print(json.dumps({"samples": samples, "notice": NOTICE}, ensure_ascii=False))


if __name__ == "__main__":
    main()
