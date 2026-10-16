"""Version two creative steps, persisted independently by the graph.

The legacy composite creator remains unchanged for old checkpoints. Each step
uses the same content-addressed LLM receipts and quality gates as that creator.
"""

from __future__ import annotations

import copy

from studio.core import write_json
from studio.planning import export_storyboard
from . import creative as c
from .review_policy import automation, exhausted_message, max_review_rounds


def _revision_scope(project):
    previous = project.get("episode") or project.get("script") or {}
    old_shots = {shot["id"]: shot for shot in previous.get("shots", [])}
    affected = c.revision_shot_ids(project.get("script_feedback"), old_shots)
    return previous, old_shots, affected


def _context(project):
    proposal = c._selected_proposal(project)
    if proposal.get("eligible") is False:
        raise c.CreativeReviewError(
            "该故事方案尚未通过评审", proposal.get("review", {}).get("issues", [])
        )
    resolved = project.get("resolved_story_brief")
    goal = (
        {key: value for key, value in resolved.items() if key != "resolution_review"}
        if resolved
        else None
    )
    reference = (
        None
        if resolved
        else {
            key: value
            for key, value in proposal.items()
            if key not in ("review", "scores", "eligible")
        }
    )
    scope = {
        "hard_fields": ["requirements", "excluded_elements"],
        "working_plan_fields": [
            "synopsis",
            "ending",
            "causal_beats",
            "creative_plan",
            "retained_elements",
        ],
        "allow_restructure_second_half": not bool(_revision_scope(project)[2]),
        "rule": "保留用户要求、删除项与已选主题；AI工作方案可重构，实际setup/choice/payoff必须落实到镜头与声音。",
    }
    return proposal, resolved, goal, reference, scope


def resolve_target(project, config, checkpoint):
    _, _, affected = _revision_scope(project)
    value = (
        copy.deepcopy(project.get("resolved_story_brief"))
        if affected
        else c.resolve_story_brief(project, config, checkpoint)
    )
    folder = c.planning_dir(project)
    artifacts = copy.deepcopy(project.get("planning_artifacts", {}))
    if value:
        artifacts["resolved_story_brief"] = c.relative_path(
            folder / "resolved-story-brief.md"
        )
        artifacts["resolved_story_review"] = c.relative_path(
            folder / "resolved-story-review.json"
        )
    return {
        "resolved_story_brief": value,
        "planning_artifacts": artifacts,
        "story_resolution_complete_generation": project["script_generation"],
        "creative_round": 1,
        "creative_review_rounds": [],
        "screenplay_draft": None,
        "story_resolution_skipped": value is None or bool(affected),
        "script_review_complete_round": None,
        "script_review_route": None,
        "review_automation": automation(config, "script", 1, project["script_generation"],
                                        "writing", "准备编写剧本"),
    }


def write_screenplay(project, config, checkpoint):
    max_review_rounds(config)
    proposal, resolved, goal, reference, scope = _context(project)
    index = project.get("creative_round", 1)
    plan = c.duration_plan(project)
    previous, old_shots, affected = _revision_scope(project)
    revision_style = (
        c._revision_visual_style(project, config, checkpoint) if affected else None
    )
    request = {
        "project": c._safe_context(project),
        "selected_proposal": reference,
        "resolved_story_brief": goal,
        "creative_scope": scope,
        "timeline": [
            {"id": "S%02d" % (i + 1), "seconds": seconds}
            for i, seconds in enumerate(plan)
        ],
        "authorized_adaptation_material": c._adaptation_material(project),
        "previous_episode": previous if affected else None,
        "only_revise_shot_ids": affected,
        "approved_visual_traits": revision_style["prompt"] if revision_style else None,
        "producer_feedback": c._producer_feedback(project),
        "proposal_reference_only_where_feedback_does_not_override": True,
    }
    if index > 1:
        request["independent_feedback"] = project.get("draft_review")
        request["previous_attempt"] = copy.deepcopy(project.get("screenplay_draft"))
        if resolved and not affected:
            request["rewrite_instruction"] = (
                "保留requirements和删除项，在已选主题内重构AI工作方案；用实际setup/choice/payoff解决评审证据，不恢复旧提案或已删除支线。"
            )
    system = c.SCRIPT_SYSTEM.replace("你是短剧编剧兼分镜导演", "你是短剧编剧", 1) + (
        "\n独立分镜导演将在你之后编排prompt/motion_prompt/camera_motion/reference_group。"
        "你完成剧情、action、对白与连续性，并提供制作字段初稿；导演不能替你改剧情。"
    )
    system = c.derive_prompt(c.SCRIPT_SYSTEM, system, "separate_storyboard")
    raw = c.cached_json_call(
        project,
        config,
        "screenwriter",
        "screenplay-%d" % index,
        system,
        request,
        c.episode_schema(len(plan), project),
        checkpoint,
        max_tokens=11500,
        temperature=0.65,
    )
    episode = c._canonical_episode(raw, project)
    if affected and previous:
        for i, shot in enumerate(episode["shots"]):
            if shot["id"] not in affected:
                episode["shots"][i] = copy.deepcopy(old_shots[shot["id"]])
            elif revision_style:
                shot["render_style_prompt"] = revision_style["prompt"]
                shot["render_style_source"] = {
                    key: revision_style[key] for key in ("source_signature", "artifact")
                }
        episode["cast"] = copy.deepcopy(previous["cast"])
        episode["character_names"] = copy.deepcopy(
            previous.get("character_names", episode.get("character_names", {}))
        )
        episode["visual_style"] = previous["visual_style"]
    path = c.planning_dir(project) / (
        "screenplay-draft-v%s-r%d-%s.json"
        % (project["script_generation"], index, c.fingerprint(episode)[:12])
    )
    write_json(path, episode)
    return {
        "previous_screenplay_draft": copy.deepcopy(project.get("screenplay_draft")) if index > 1 else None,
        "screenplay_draft": episode,
        "review_automation": automation(config, "script", index, project["script_generation"],
                                        "writing", "按独立审核返修原稿" if index > 1 else "编写剧本"),
        "screenplay_complete_round": [project["script_generation"], index],
        "planning_artifacts": {
            **project.get("planning_artifacts", {}),
            "screenplay_draft": c.relative_path(path),
        },
    }


def direct_screenplay(project, config, checkpoint):
    _, _, goal, reference, _ = _context(project)
    index = project.get("creative_round", 1)
    value = c.direct_storyboard(
        project,
        config,
        project["screenplay_draft"],
        checkpoint,
        approved_story=goal or reference,
        round_index=index,
        shot_ids=_revision_scope(project)[2] or None,
        producer_feedback=c._producer_feedback(project),
        repair_issues=(project.get("draft_review") or {}).get("issues", []),
        previous_attempt=project.get("previous_screenplay_draft"),
    )
    return {
        "screenplay_draft": value["episode"],
        "storyboard_issues": value["issues"],
        "storyboard_receipt": value["receipt"],
        "storyboard_complete_round": [project["script_generation"], index],
        "planning_artifacts": {
            **project.get("planning_artifacts", {}),
            "storyboard_direction": value["artifact"],
        },
    }


def review_screenplay(project, config, checkpoint):
    proposal, resolved, goal, _, scope = _context(project)
    episode = project["screenplay_draft"]
    index = project.get("creative_round", 1)
    structural = (
        c.technical_issues(episode, project)
        + c._excluded_story_issues(episode, resolved)
        + project.get("storyboard_issues", [])
    )
    review = c.cached_json_call(
        project,
        config,
        "independent_critic",
        "screenplay-review-%d" % index,
        c.CRITIC_SYSTEM,
        {
            "project": c._safe_context(project),
            "selected_story": resolved["logline"] if resolved else proposal["logline"],
            "resolved_story_brief": goal,
            "creative_scope": scope,
            "required_checks": "逐项核对新目标requirements及excluded_elements。删除项不得重现；角色动作、责任和道歉归属须与因果链一致，不以作者自称合规作为证据。",
            "producer_feedback": c._producer_feedback(project),
            "proposal_reference_only_where_feedback_does_not_override": True,
            "episode": episode,
            "technical_findings": structural,
            "task": "以出品人反馈修订后的故事目标评价完整成片因果链、结局与反转证据。原提案冲突部分已被反馈覆盖；不得因遵守删除要求而扣分，仍保留明确被删除要素则指出镜头证据并阻断。",
        },
        c.SCRIPT_REVIEW_SCHEMA,
        checkpoint,
        max_tokens=5800,
        temperature=0.25,
    )
    review["issues"] = structural + review["issues"]
    review["passed"] = c.review_passes(review)
    history = copy.deepcopy(project.get("creative_review_rounds", []))
    history = [row for row in history if row["round"] != index]
    history.append(
        {
            "round": index,
            "review": copy.deepcopy(review),
            "storyboard": project.get("storyboard_receipt"),
        }
    )
    record = {
        "passed": review["passed"],
        "rounds": history,
        "review": review,
        "producer_feedback": c._producer_feedback(project),
    }
    folder = c.planning_dir(project)
    review_path = folder / (
        "script-review-v%s-r%d-%s.json"
        % (project["script_generation"], index, c.fingerprint(record)[:12])
    )
    write_json(review_path, record)
    write_json(folder / "script-review.json", record)
    write_json(folder / "episode.json", episode)
    write_json(
        folder
        / (
            "episode-v%s-%s.json"
            % (project["script_generation"], c.fingerprint(episode)[:12])
        ),
        episode,
    )
    c.atomic_text(folder / "script.md", c._script_doc(episode, review))
    export_storyboard(episode, folder / "storyboard.csv")
    patch = {
        "review_automation": automation(config, "script", index, project["script_generation"],
                                        "passed" if review["passed"] else
                                        "repairing" if index < max_review_rounds(config) else "blocked",
                                        "独立审核已通过" if review["passed"] else
                                        "按本轮独立审核问题继续自动返修" if index < max_review_rounds(config) else
                                        "自动返修轮次已用尽"),
        "script_review_complete_round": [project["script_generation"], index],
        "script_review_route": "continue" if review["passed"] else "repair",
        "draft_review": review,
        "creative_review_rounds": history,
        "script_review": record,
        "script": episode,
        "episode": episode,
        "shots": c._shot_metadata(episode, project),
        "script_updated_at": c.utc_now(),
        "planning_artifacts": {
            **project.get("planning_artifacts", {}),
            "script": c.relative_path(folder / "script.md"),
            "storyboard": c.relative_path(folder / "storyboard.csv"),
            "episode": c.relative_path(folder / "episode.json"),
            "script_review": c.relative_path(review_path),
        },
    }
    if review["passed"]:
        patch.update(
            script_complete_generation=project["script_generation"], needs_decision=None
        )
    elif index < max_review_rounds(config):
        patch["creative_round"] = index + 1
        patch["needs_decision"] = None
    else:
        patch["needs_decision"] = c._gate(
            "script_review",
            exhausted_message("剧本", index),
            c._script_gate_issues(review),
        )
    return patch


def design_visuals(project, config, checkpoint):
    if (project.get("script_review") or {}).get("passed") is not True:
        raise c.CreativeReviewError("剧本尚未通过独立评审，不能生成视觉方案")
    styles = c._styles(project, config, project["script"], checkpoint)
    folder = c.planning_dir(project)
    version = project.get("style_generation", project["script_generation"])
    path = folder / ("styles-v%s-%s.json" % (version, c.fingerprint(styles)[:12]))
    write_json(path, styles)
    write_json(folder / "styles.json", styles)
    return {
        "styles": styles,
        "visual_design_complete_generation": version,
        "planning_artifacts": {
            **project.get("planning_artifacts", {}),
            "styles": c.relative_path(path),
        },
    }
