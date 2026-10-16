"""Explicit creative intent for new productions; absent contracts stay legacy."""
from __future__ import annotations

import copy
import json

FIELDS = frozenset({"schema_version", "visual_style", "story_mode", "pacing", "motion"})
FORMATS = {
    "animation": {"anime_2d", "stylized_3d"},
    "live_action": {"photorealistic"},
    "motion_comic": {"illustrated"},
}
STYLE_PROMPTS = {
    "anime_2d": (
        "2D anime animation with clean drawn contours, cel shading and consistent drawn character designs. "
        "No photorealistic live-action appearance, 3D-rendered characters, comic panels, manga page layouts "
        "or illustrated slideshow delivery. The completed video must show actual continuous character "
        "or object animation, not only camera movement across a still illustration."),
    "stylized_3d": (
        "Stylized 3D animated film with clearly volumetric character designs, coherent materials and lighting. "
        "No photorealistic live-action appearance, 2D cel-drawn characters, comic panels or illustrated "
        "slideshow delivery. The completed video must show actual continuous character or object animation, "
        "not only camera movement across a still render."),
    "photorealistic": (
        "Photorealistic live-action short drama with natural adult human appearance, real-world textures "
        "and cinematic lighting. No anime, cartoon, illustration or stylized 3D-rendered appearance. "
        "The completed video must show actual continuous character or object movement, not only "
        "camera movement across a still photograph."),
    "illustrated": (
        "Illustrated motion comic with consistent drawn character designs and painterly or graphic "
        "illustration. Deliver a still keyframe with restrained programmatic camera movement; "
        "do not describe this medium as continuous character animation or live-action video."),
}


def normalize(value, format_name):
    """Validate a supplied v1 contract without inferring or changing its medium."""
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("创作契约须包含完整的版本、画风、故事模式、节奏和运动方式")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("不支持的创作契约版本")
    style = value["visual_style"]
    if not isinstance(style, str) or style not in FORMATS.get(format_name, set()):
        raise ValueError("画风与制作形态不一致：动画支持 2D 动漫或 3D 动画，真人支持写实，动态漫画支持插画")
    if value["story_mode"] != "standalone" or value["pacing"] != "short_drama":
        raise ValueError("当前创作契约须为独立闭环短剧")
    expected_motion = "still_camera" if format_name == "motion_comic" else "generated_video"
    if value["motion"] != expected_motion:
        raise ValueError("运动方式与制作形态不一致，动画和真人须使用生成视频")
    return copy.deepcopy(value)


def resolve(project):
    """Do not infer a new contract for historical projects/checkpoints."""
    if "creative_contract" not in project:
        return None
    return normalize(project["creative_contract"], project.get("format", "motion_comic"))


def style_prompt(project):
    contract = resolve(project)
    return STYLE_PROMPTS[contract["visual_style"]] if contract else ""


def instructions(project):
    contract = resolve(project)
    if contract is None:
        return ""
    motion = (
        "Each generated video shot must contain one simple continuous observable character or object action "
        "with a visible start and resulting state. Camera movement alone is insufficient. Keep a single "
        "continuous shot without internal montage; simplify complex choreography rather than replacing "
        "animation with a static keyframe. A keyframe is only the starting instant of the action."
        if contract["motion"] == "generated_video" else
        "Each illustrated keyframe depicts one observable instant. All faces and bodies remain still; "
        "only the programmatic camera moves. Do not promise generated character animation."
    )
    return "\nCREATIVE_CONTRACT_JSON:\n" + json.dumps(contract, ensure_ascii=False, sort_keys=True) + (
        "\nThe explicit creative contract is binding for every story, draft, review, visual option and shot. "
        "Create one self-contained short drama within the selected duration: open with a visible conflict, "
        "establish the protagonist's concrete goal, show causally motivated resistance and a consequential "
        "change, then deliver an earned payoff and resolve the central conflict on screen. No episode setup, "
        "to-be-continued ending, sequel dependence or cliffhanger replacing the ending. Do not cram a whole "
        "series into the duration; choose one complete dramatic event. Reviewers must flag missing closure, "
        "unclear agency, medium mismatch and camera-only animation as substantive failures. "
        "All visual candidates must remain in the selected medium; vary palette, lighting, line treatment "
        "or design within that medium instead of offering unrelated media. "
        + STYLE_PROMPTS[contract["visual_style"]] + " " + motion +
        " All dialogue remains off-screen voice-over; visible mouths stay closed. Do not promise lip sync. "
        "独立单部故事须在当前片长内闭环；开场可见冲突、清晰目标、因果转折与结尾兑现。"
        "画风和运动方式不得靠格式名称冒充，候选不能跨媒介，审核必须检查这些约束。")
