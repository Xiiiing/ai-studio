"""Recoverable story, screenplay, visual direction and independent review loops."""
from __future__ import annotations

import copy
import json
import re

from .prompt_registry import load_prompt, derive_prompt

from studio.core import write_json
from studio.planning import export_storyboard
from factory.storyboard import direct_storyboard
from factory.schemas import resolved_review_schema
from .production_profiles import roles as profile_roles, explicit as explicit_profile, resolve as resolve_profile, speech_limit
from factory.schemas import (
    CreativeReviewError, ISSUE_SCHEMA, PROPOSALS_SCHEMA, PROPOSAL_REVIEW_SCHEMA,
    SCORE_KEYS, SCRIPT_REVIEW_SCHEMA, RESOLVED_STORY_SCHEMA, RESOLVED_REVIEW_SCHEMA, STYLES_SCHEMA, UPSTREAM_REFERENCES,
    atomic_text, cached_json_call, duration_plan, episode_schema, fingerprint,
    planning_dir, project_context, relative_path, report, review_passes,
    revision_shot_ids, technical_issues, utc_now,
)

CREATOR_SYSTEM = load_prompt("creator")

CRITIC_SYSTEM = load_prompt("independent_critic")

SCRIPT_SYSTEM = load_prompt("screenwriter")

STYLE_SYSTEM = load_prompt("visual_designer")


def _safe_context(project):
    context = project_context(project)
    if context.get("series_bible"):
        context["series_reference_policy"] = (
            "Use the pinned Series Bible as immutable identity/world constraints. "
            "Per-shot wardrobe, held props, position and plot state are separate. "
            "Only explicitly approved asset versions may be treated as reusable visual references.")
    memory = context.get("memory")
    if memory is not None and len(json.dumps(memory, ensure_ascii=False)) > 12000:
        context["memory"] = {"excerpt": json.dumps(memory, ensure_ascii=False)[:12000],
                             "truncated": True}
    return context


def _producer_feedback(project, revision_feedback=None):
    """Retain every producer direction with an explicit, shared precedence."""
    levels = [
        ("revision_feedback", revision_feedback),
        ("script_feedback", project.get("script_feedback")),
        ("story_feedback", project.get("story_feedback")),
    ]
    instructions = [{"source": name, "content": copy.deepcopy(value)}
                    for name, value in levels if value not in (None, "", {}, [])]
    return {
        "priority_order": ["revision_feedback", "script_feedback", "story_feedback", "selected_proposal"],
        "instructions": instructions,
        "conflict_rule": "按priority_order解决冲突；较新且具体的反馈覆盖旧反馈或原提案冲突部分，其余意见继续生效。出品人要求删除的支线、人物或道具不得被旧提案、旧稿或旧评审恢复。",
        "evaluation_target": "依据反馈修订后的完整故事目标评审；原提案只保留未被反馈覆盖的设定，不因遵守删除要求而扣分。",
    }


def _adaptation_material(project):
    if project.get("creation_mode", "original") != "adaptation":
        return []
    allowed = set(project.get("research", {}).get("authorized_adaptation_source_ids", []))
    return [{k: source.get(k) for k in ("id", "title", "summary", "url", "rights")}
            for source in project.get("sources", []) if source.get("id") in allowed]


def _review_issue(code, description, evidence="", shot_ids=None):
    return {"severity": "blocking", "code": code, "description": description,
            "evidence": evidence or description, "suggestion": "根据证据重写并重新评审",
            "shot_ids": shot_ids or []}


def _script_gate_issues(review):
    """Expose the actual numeric/major blockers without promoting minor notes."""
    labels = {"hook": "开场吸引力", "causality": "因果", "motivation": "人物动机",
              "originality": "原创组合", "closure": "结局闭合", "feasibility": "制作可行性"}
    scores = review.get("scores", {})
    issues = [copy.deepcopy(item) for item in review.get("issues", [])
              if item.get("severity") in ("blocking", "major")]
    values = [scores.get(key, 0) for key in SCORE_KEYS]
    low = [(key, scores.get(key, 0)) for key in SCORE_KEYS if scores.get(key, 0) < 6]
    if low:
        details = "、".join("%s（%s）=%s" % (labels[key], key, value) for key, value in low)
        issues.append(_review_issue("score_below_minimum",
            "独立评分单项未达 6 分门槛：" + details,
            "实际评分：" + json.dumps(scores, ensure_ascii=False) + "；要求每项至少 6 分"))
    average = sum(values) / len(values)
    if average < 7:
        issues.append(_review_issue("score_average_below_minimum",
            "独立评分均分 %.2f，未达 7 分门槛" % average,
            "六项合计 %s，均分 %.2f；要求均分至少 7 分" % (sum(values), average)))
    return issues


def _gate(kind, message, issues):
    return {"kind": kind, "message": message, "issues": issues,
            "allowed_actions": ["revise_brief", "regenerate", "provide_feedback"]}


def _proposal_doc(proposals, history):
    lines = ["# 故事候选方案", "", "版本经过独立评审；未通过的方案不会自动进入生产。", ""]
    for proposal in proposals:
        lines.extend(["## " + proposal["id"] + " · " + proposal["title"],
            "**一句话故事**：" + proposal["logline"],
            "**开场钩子**：" + proposal["hook"],
            "**故事梗概**：" + proposal["synopsis"],
            "**结局**：" + proposal["ending"],
            "**制作说明**：" + proposal["production_notes"],
            "**评审状态**：" + ("通过" if proposal.get("eligible") else "需要修改"), "",
            "评分：" + json.dumps(proposal.get("scores", {}), ensure_ascii=False), ""])
        for issue in proposal.get("review", {}).get("issues", []):
            lines.extend(["- " + issue["severity"] + "：" + issue["description"],
                          "  证据：" + issue["evidence"]])
        lines.append("")
    lines.extend(["## 审查轮次", ""])
    for item in history:
        lines.append("- 第%d轮：%s" % (item["round"], item.get("comparison", "")))
    return "\n".join(lines) + "\n"


def create_proposals(project, config, checkpoint):
    if project.get("creation_mode", "original") == "adaptation" and not _adaptation_material(project):
        raise CreativeReviewError("改编任务缺少经过授权检查的可读原著素材")
    folder = planning_dir(project)
    maximum_rounds = max(1, min(2, int(config.get("creative", {}).get("max_review_rounds", 2))))
    payload = {"project": _safe_context(project),
               "research": project.get("research", {}),
               "authorized_adaptation_material": _adaptation_material(project),
               "production_constraints": {"shots": len(duration_plan(project)), "roles": profile_roles(project),
                                           "max_dialogue_chars_per_shot": speech_limit(project)}}
    proposals, history = [], []
    for round_index in range(1, maximum_rounds + 1):
        report(checkpoint, stage="story_creation", progress=13 + round_index * 3,
               message="编剧正在形成三套故事方案（第%d轮）" % round_index)
        round_payload = copy.deepcopy(payload)
        if proposals:
            round_payload["previous_proposals"] = proposals
            round_payload["independent_feedback"] = history[-1]
            round_payload["rewrite_instruction"] = "保留成立的要素，修正问题；三个方案仍须明显不同。"
        generated = cached_json_call(project, config, "creator", "proposals-%d" % round_index,
            CREATOR_SYSTEM, round_payload, PROPOSALS_SCHEMA, checkpoint, max_tokens=7500)
        proposals = [{**item, "id": ident} for item, ident in
                     zip(generated["proposals"], ("A", "B", "C"))]
        report(checkpoint, stage="story_review", progress=17 + round_index * 3,
               message="独立审稿人正在核查三套故事的因果、动机和结局")
        judged = cached_json_call(project, config, "independent_critic",
            "proposal-review-%d" % round_index, CRITIC_SYSTEM,
            {"project": _safe_context(project), "proposals": proposals,
             "task": "分别评审A、B、C，覆盖全部三个方案，指出文本内的证据。"},
            PROPOSAL_REVIEW_SCHEMA, checkpoint, max_tokens=6000, temperature=0.25)
        grouped = {}
        for review in judged["reviews"]:
            grouped.setdefault(review["proposal_id"], []).append(review)
        title_counts = {}
        for proposal in proposals:
            title_counts[proposal["title"].strip()] = title_counts.get(proposal["title"].strip(), 0) + 1
        normalized_reviews = []
        for proposal in proposals:
            reviews = grouped.get(proposal["id"], [])
            if len(reviews) == 1:
                review = copy.deepcopy(reviews[0])
            else:
                review = {"proposal_id": proposal["id"], "scores": {key: 0 for key in SCORE_KEYS},
                    "summary": "独立评审未唯一覆盖本方案", "strengths": [],
                    "issues": [_review_issue("review_coverage", "缺少有效且唯一的独立评审")]}
            if {c["role"] for c in proposal["characters"]} != set(profile_roles(project)):
                review["issues"].append(_review_issue("character_roles", "故事人物未覆盖创作规格中的稳定角色"))
            if title_counts[proposal["title"].strip()] > 1:
                review["issues"].append(_review_issue("duplicate_proposal", "多个候选方案标题完全相同"))
            proposal["scores"] = review["scores"]
            proposal["review"] = review
            proposal["eligible"] = review_passes(review)
            normalized_reviews.append(review)
        history.append({"round": round_index, "reviews": normalized_reviews,
                        "comparison": judged["comparison"]})
        write_json(folder / "proposals.json", {"proposals": proposals, "rounds": history,
            "proposal_generation": project.get("proposal_generation", 1)})
        atomic_text(folder / "proposals.md", _proposal_doc(proposals, history))
        if all(proposal["eligible"] for proposal in proposals):
            break
    eligible_count = sum(bool(proposal["eligible"]) for proposal in proposals)
    all_passed = eligible_count == len(proposals)
    selectable = eligible_count > 0
    blockers = [{"proposal_id": proposal["id"], "issues": proposal["review"]["issues"],
                 "scores": proposal["scores"]} for proposal in proposals if not proposal["eligible"]]
    return {"proposals": proposals, "creation_review": {
            "passed": selectable, "all_passed": all_passed,
            "eligible_count": eligible_count, "rounds": history},
        "needs_decision": None if selectable else _gate("creative_review",
            "全部故事方案完成有界改写后仍未通过评审，请修改方向或重新提案。", blockers),
        "planning_artifacts": {**project.get("planning_artifacts", {}),
                               "proposals": relative_path(folder / "proposals.md")}}


def _selected_proposal(project):
    selected = project.get("selected_story")
    if isinstance(selected, dict):
        selected = selected.get("id")
    for proposal in project.get("proposals", []):
        if proposal.get("id") == selected:
            return proposal
    raise CreativeReviewError("请先选择一套已生成的故事方案")


def _canonical_episode(raw, project):
    plan = duration_plan(project)
    episode = copy.deepcopy(raw)
    episode.update({"slug": project["slug"], "fps": 24, "width": 720, "height": 1280,
                    "generation": {"width": 736, "height": 1312},
                    "total_seconds": sum(plan), "format": project.get("format", "motion_comic")})
    if len(episode.get("shots", [])) != len(plan):
        raise CreativeReviewError("生成的镜头数不符合创作规格")
    if explicit_profile(project):
        episode["production_profile"] = resolve_profile(project)
        episode["characters"] = copy.deepcopy(episode["production_profile"]["characters"])
        episode["character_names"] = {item["character_id"]: item["name"] for item in episode["characters"]}
    for role in profile_roles(project):
        noun = {"female": "woman", "male": "man"}.get(role, "person")
        if not re.search(r"\badult\b", episode["cast"][role], re.I):
            episode["cast"][role] = "Fictional adult " + noun + ". " + episode["cast"][role]
    for i, shot in enumerate(episode["shots"]):
        sid = "S%02d" % (i + 1)
        shot.update({"id": sid, "seconds": plan[i], "lip_sync": False})
        for j, line in enumerate(shot["dialogue"]):
            line.update({"id": sid + "_L%d" % (j + 1), "start": 0.2})
        suffix = " No visible text, captions, logos, or watermarks."
        if project.get("format") == "motion_comic":
            suffix += " Single illustrated keyframe; every visible person's mouth is closed."
            camera = {
                "push_in": "A slow restrained camera push-in on the still keyframe.",
                "pull_out": "A slow restrained camera pull-out from the still keyframe.",
                "pan_left": "A slow camera pan to the left across the still keyframe.",
                "pan_right": "A slow camera pan to the right across the still keyframe.",
                "hold": "Hold the still keyframe with an almost imperceptible camera drift.",
            }[shot["camera_motion"]]
            shot["motion_prompt"] = camera + " Keep all faces, bodies and closed mouths still; voice-over only."
            shot["delivery_method"] = "generated_keyframe_with_programmatic_camera"
        else:
            suffix += " All dialogue is off-screen voice-over; visible mouths stay closed."
        prompt, ending = shot["prompt"].rstrip(), suffix.strip()
        while prompt.endswith(ending):
            prompt = prompt[:-len(ending)].rstrip()
        shot["prompt"] = prompt + suffix
    return episode


def _script_doc(episode, review):
    structure_names = {"setup": "铺垫", "inciting_incident": "触发事件", "turning_point": "转折",
                       "climax": "高潮", "resolution": "解决"}
    lines = ["# " + episode["title"], "", episode["logline"], "",
             "## 完整故事", "", episode.get("synopsis", ""), "",
             "## 结局", "", episode.get("ending", ""), "",
             "## 叙事结构", ""]
    for key, label in structure_names.items():
        lines.append("- " + label + "：" + episode.get("story_structure", {}).get(key, ""))
    lines.extend(["", "## 人物设定", ""])
    for role in episode["cast"]:
        lines.extend(["### " + episode["character_names"][role] + " (" + role + ")",
                      episode["cast"][role], ""])
    lines.extend(["## 逐镜制作稿", ""])
    elapsed = 0
    for shot in episode["shots"]:
        lines.extend(["### %s · %g–%g秒 · %s" % (
            shot["id"], elapsed, elapsed + shot["seconds"], shot.get("location", "")),
            "**剧情节拍**：" + shot.get("dramatic_beat", ""),
            "**可见画面**：" + shot.get("action", ""),
            "**连续性**：" + shot.get("continuity", ""),
            "**画外音**：" + ("；".join(episode["character_names"][line["role"]] + "：" + line["text"]
                for line in shot.get("dialogue", [])) or "无"),
            "**音效意图**：" + shot.get("sfx", ""),
            "**关键帧提示词**：" + shot["prompt"],
            "**运镜规格**：" + shot["motion_prompt"], ""])
        elapsed += shot["seconds"]
    lines.extend(["## 独立评审", "", review.get("summary", ""),
                  "评分：" + json.dumps(review.get("scores", {}), ensure_ascii=False), ""])
    for issue in review.get("issues", []):
        lines.extend(["- " + issue["severity"] + "：" + issue["description"],
                      "  证据：" + issue["evidence"]])
    lines.extend(["", "## 制作形式", "",
        "配音按画外音编排，未启用同步口型。动态漫剧采用真实生成的关键帧与程序运镜，后续媒体质检独立进行。",
        "", "## 方法参考", ""])
    lines.extend("- " + item["name"] + " · " + item["url"] + " · " + item["use"]
                 for item in UPSTREAM_REFERENCES)
    return "\n".join(lines) + "\n"


def _shot_metadata(episode, project):
    previous = {s["id"]: s for s in project.get("shots", []) if isinstance(s, dict) and "id" in s}
    result = []
    for shot in episode["shots"]:
        signature = fingerprint({"shot": shot, "production_profile": resolve_profile(project)}) if explicit_profile(project) else fingerprint(shot)
        old = previous.get(shot["id"], {})
        kept = copy.deepcopy(old) if old.get("script_signature") == signature else {}
        result.append({**kept, **copy.deepcopy(shot), "script_signature": signature,
                       "status": kept.get("status", "planned"),
                       "attempts": kept.get("attempts", [])})
    return result


def _revision_visual_style(project, config, checkpoint):
    """Compile approved visual traits for changed shots without changing old assets."""
    chosen = project.get("selected_style")
    if not chosen:
        return None
    ident = chosen.get("id") if isinstance(chosen, dict) else chosen
    style = next((item for item in project.get("styles", []) if item.get("id") == ident),
                 chosen if isinstance(chosen, dict) else {})
    if not style.get("prompt"):
        raise CreativeReviewError("已选画风缺少可追溯规格，不能为局部返修编译画风")
    approved = {key: copy.deepcopy(style.get(key)) for key in ("id", "name", "prompt", "palette")}
    schema = {"type": "object", "additionalProperties": False,
        "properties": {
            "render_style_prompt": {"type": "string", "minLength": 20, "maxLength": 1400},
            "retained_visual_traits": {"type": "array", "minItems": 3, "maxItems": 10,
                                      "items": {"type": "string", "minLength": 1}},
            "removed_scene_elements": {"type": "array", "maxItems": 16,
                                      "items": {"type": "string", "minLength": 1}}},
        "required": ["render_style_prompt", "retained_visual_traits", "removed_scene_elements"]}
    value = cached_json_call(project, config, "prompt_style_compiler", "revision-visual-traits",
        "你是制作提示词编辑，只从已批准画风提炼可用于任意镜头的视觉特征，不创作新画风。"
        "render_style_prompt使用英文，保留媒介、笔触、纹理、色板、通用光照与造型方式。"
        "删除特定人物、职业衣着、道具及其持有位置、具体地点、故事动作、事件时序与转场，"
        "也不把被删除的剧情写成否定提示词。不能因原样例提到某物就要求每镜出现它。"
        "retained_visual_traits用中文简列保留的画风依据，removed_scene_elements记录删除的场景性要求。"
        "这些删除记录只作审计，不能加入渲染文本。只返回指定JSON。",
        {"approved_style": approved}, schema, checkpoint, max_tokens=1200, temperature=0.15)
    if not value["render_style_prompt"].strip():
        raise CreativeReviewError("局部返修画风编译为空")
    evidence = {"schema": "factory.revision_visual_traits.v1", "approved_style": approved,
                "source_signature": fingerprint(approved), **value}
    path = planning_dir(project) / ("revision-visual-traits-" + fingerprint(evidence)[:16] + ".json")
    write_json(path, evidence)
    return {"prompt": value["render_style_prompt"].strip(),
            "source_signature": evidence["source_signature"], "artifact": relative_path(path)}


def _styles(project, config, episode, checkpoint):
    report(checkpoint, stage="visual_design" if project.get("workflow_version",1)==2 else "visual_development", progress=42, message="美术指导正在形成三套画风方向")
    context=_safe_context(project)
    # Keep the registered top-level contract and byte-identical old cache input
    # for first-generation projects without visual feedback.
    if project.get("style_feedback"):
        context["visual_direction_revision"]={
            "generation":project.get("style_generation",project.get("script_generation",1)),
            "producer_visual_feedback":project["style_feedback"],
            "previous_directions":[{k:v.get(k) for k in ("name","description","prompt","palette")}
                                   for v in project.get("styles",[])],
            "instruction":"落实producer_visual_feedback；被否决方向只用于理解原因，不重复生成。"}
    value = cached_json_call(project, config, "visual_designer", "visual-styles", STYLE_SYSTEM,
        {"project": context, "title": episode["title"],
         "synopsis": episode["synopsis"], "cast": episode["cast"],
         "locations": list(dict.fromkeys(s.get("location", "") for s in episode["shots"]))},
        STYLES_SCHEMA, checkpoint, max_tokens=3600, temperature=0.7)
    return [{**style, "id": "style-%d" % (i + 1), "format": project.get("format", "motion_comic")}
            for i, style in enumerate(value["styles"])]


def _resolved_evidence_catalog(target):
    """Assign deterministic IDs only to exact narrative fragments."""
    catalog = []
    def collect(path, value):
        if isinstance(value, str):
            pieces = re.findall(r'.+?(?:[。！？!?；;][”’"」』]*|$)', value, re.S)
            for sentence in pieces:
                for offset in range(0, len(sentence), 240):
                    fragment = sentence[offset:offset + 240].strip()
                    if fragment:
                        catalog.append({"id": "EV" + fingerprint({
                            "path": path, "text": fragment})[:12],
                            "path": path, "text": fragment})
        elif isinstance(value, list):
            for index, item in enumerate(value):
                collect(path + "[%d]" % index, item)
        elif isinstance(value, dict):
            for key in sorted(value):
                collect(path + "." + key, value[key])
    for key in ("title", "logline", "synopsis", "ending", "characters", "causal_beats"):
        collect(key, target.get(key))
    return list({item["id"]: item for item in catalog}.values())


def resolve_story_brief(project, config, checkpoint=None, feedback=None):
    """Resolve global producer changes before expanding another full screenplay."""
    proposal = _selected_proposal(project)
    directions = _producer_feedback(project, feedback)
    if not directions["instructions"]:
        return None
    reference = {key: copy.deepcopy(proposal.get(key)) for key in (
        "id", "title", "logline", "synopsis", "ending", "characters")}
    for key in ("synopsis", "ending"):
        reference[key] = str(reference.get(key) or "")[:1000]
    sources = {"selected_proposal": json.dumps(reference, ensure_ascii=False)}
    sources.update({entry["source"]: (entry["content"] if isinstance(entry["content"], str)
                                      else json.dumps(entry["content"], ensure_ascii=False))
                    for entry in directions["instructions"]})
    system = """你是短剧故事目标编辑。这里只做简短目标解析，不写分镜，不复述旧稿。
selected_proposal是待修改的历史提案；producer_feedback来自出品人，是必须落实的新目标。
按priority_order覆盖冲突，解析后写出一个已经完成修改的新故事：主冲突、因果节拍、人物动机和闭合结局。
必须删除的支线应从logline、synopsis、ending、人物动机和causal_beats中完全消失；不要只改时间或换角色名称。
requirements只拆解出品人明确要求，每条只含一个可由正文验证的条件，不粘贴整段反馈；各条需分别覆盖主冲突闭环、具体创意要求和制作限制。
requirements的source_id只能是输入的出品人反馈，不能把历史提案或你自选的礼物、互动、结尾作为用户硬约束。
creative_plan写清角色职业/当晚任务造成的具体需求、可见setup、主动choice、实际payoff及因果差异；这些事实必须同时写入synopsis或causal_beats，不能只在说明栏自称创新。
更换赠礼品种不等于新机制。让回馈来自角色具体需求和选择，并造成可见结果；不要写回家、开车、工作行程等无关尾声凑时长。
synopsis、ending、causal_beats、creative_plan和retained_elements是AI可重构方案；用户硬要求和删除项优先，第二版可以重构机制而不只是修词。
excluded_elements只列明确禁止或删除的事项；“无需/不要求”不等于禁止，不能扩大删除范围。
每条requirement或excluded_element的source_id必须来自输入，source_quote必须逐字摘录相应来源，不得改写引文。
删除项的forbidden_terms只列旧提案或反馈中明确出现、不得再回到故事中的专名或专属道具称呼；不要列仍需保留的通用名词，也不要发明词。
source_quote中的删除目标只用于追溯，禁止在新故事正文中把被删除情节重新讲一遍。
两位虚构成年角色恰为female和male，15镜60至90秒能讲完，结局解决本集主冲突。
用中文给出简洁且因果成立的新目标，严格JSON。"""
    payload = {"project": _safe_context(project), "selected_proposal": reference,
               "producer_feedback": directions,
               "source_ids": list(sources),
               "task": "先按反馈完成全局改稿，之后编剧只会收到这份新目标，不再收到旧提案或旧全文。"}
    folder = planning_dir(project)
    source_signature = fingerprint({"reference": reference, "producer_feedback": directions,
                                    "schema": "resolved-story-brief.v3",
                                    **({"production_profile": resolve_profile(project)} if explicit_profile(project) else {})})
    review_system = """你是独立短剧因果审稿人，只评估当前紧凑目标的实际剧情，不写新故事。
六项逐项核查：时间/地点/物品持有变化的因果链；误会与责任；物品认领证据；出品人要求；结局；制作可行性。
requirements_review必须逐个R编号判断实际落实；每条要求单独选择正文证据，不能以其他条已满足代替。若要求被合并成整段、遗漏或扩大原反馈，应在producer_requirements中指出。
originality给0至10整数分，评估当前主题内的“角色具体需求→可见前置线索→主动选择→结果兑现”。6分需有可信的具体因果组合；只换礼物或物品名称、重复目送/风景不算创新。不能为放行抬分。
核对synopsis、causal_beats与ending：逐一追踪每件被转交道具的初始和最终持有人；转给他人后不能无解释又在原人手中，一份物品不能凭空成为两份。
误会的触发信号必须先于误会；事后出现的澄清证据不能倒过来充当起因。遗失、拾得、寻找失主和归还路径均应具体成立。
谁作出错误判断谁承担相应责任；被误判者受惊本身不是其需要道歉的理由。
blocking/major须有实际正文矛盾或使故事无法成立的关键缺口；熟悉物品结合场景和反应可以合理认领，不要求全球唯一证明。
不能只因理论上存在相似物或省略可合理推断的过场而阻断；误会起因在事后出现、被交出的道具无解释回到原持有人仍是真矛盾。
以producer_feedback为目标，删除要求须落实在实际故事中，不因偏离旧提案而扣分。
evidence_catalog由程序从正文提取。每项check的evidence_ids只能选择目录中最相关的1至4个ID。
问题issues也必须选正文证据ID，由程序还原原文；reason/description解释证据如何成立或矛盾，不需要抄写引文。
要求文本和creative_plan自述不在证据目录，不能把作者自称满足要求或创新当作落实证明；检查synopsis、causal_beats与ending中的真实事件、人物选择与结局。
缺少必要环节时，选择最接近的相关情节ID并解释缺口，不得因为目录没有某事件就替故事补造该事件。
只要任何必要条件不成立，对应check和总status为fail，issues给出具体的blocking或major问题。
纯可选润色列minor；可行性按15镜60至90秒、两名成年角色和项目媒介判断，不因偏好否决。
用中文严格返回schema；只返回证据ID，不返回自由抄写的evidence_quotes或evidence。"""
    from factory.config import llm_identity
    review_policy_signature = fingerprint({
        "reviewer_model": llm_identity(config, "story_critic"),
        "policy": "causal-responsibility-v4.atomic-requirements-and-originality",
        "system": review_system, "schema": resolved_review_schema(["EVIDENCE"]),
        "constraints": {key: project.get(key) for key in (
            "brief", "format", "duration_seconds", "creation_mode")}})
    value = None
    review_only = False
    start_revision = 1
    previous_policy_record = None
    try:
        saved = json.loads((folder / "resolved-story-brief.json").read_text(encoding="utf-8"))
        if saved.get("schema") != "resolved-story-brief.v3":
            previous_policy_record = relative_path(folder / (
                "resolved-story-prior-review-" + fingerprint(saved)[:16] + ".json"))
            write_json(folder / previous_policy_record.split("/")[-1], saved)
        target_signature = fingerprint({key: saved.get(key)
                                        for key in RESOLVED_STORY_SCHEMA["properties"]})
        same_target = (saved.get("source_signature") == source_signature and
                       saved.get("reviewed_target_signature") == target_signature)
        if same_target and saved.get("review_policy_signature") == review_policy_signature:
            if (saved.get("resolution_review", {}).get("passed") is True and
                    not _excluded_story_issues(saved, saved)):
                report(checkpoint, message="复用已通过独立评审的故事目标；剧本重试保持该目标不变")
                return copy.deepcopy(saved)
            if saved.get("resolution_revision") == 2:
                prior = saved.get("resolution_review", {}).get("rounds", [])
                issues = prior[-1].get("review", {}).get("issues", []) if prior else []
                raise CreativeReviewError("当前目标已经达到两版上限且未通过，请调整创作要求后再继续", issues)
        elif same_target and saved.get("resolution_revision") in (1, 2):
            # A changed review contract re-evaluates the exact stored target.
            review_only = True
            start_revision = saved["resolution_revision"]
            value = {key: copy.deepcopy(saved.get(key))
                     for key in RESOLVED_STORY_SCHEMA["properties"]}
            previous_policy_record = relative_path(folder / (
                "resolved-story-prior-review-" + fingerprint(saved)[:16] + ".json"))
            write_json(folder / previous_policy_record.split("/")[-1], saved)
    except CreativeReviewError:
        raise
    except (OSError, ValueError, TypeError):
        pass
    if value is None:
        report(checkpoint, stage="story_resolution", progress=28,
               message="制片人反馈正在转换为明确的新故事目标、保留项和删除项")
        value = cached_json_call(project, config, "story_editor", "resolved-story-brief",
            system, payload, RESOLVED_STORY_SCHEMA, checkpoint, max_tokens=3800, temperature=.2)
    compact = lambda text: re.sub(r"\s+", "", str(text))
    history = []
    revisions = (start_revision,) if review_only else (1, 2)
    for revision in revisions:
        for group in ("requirements", "excluded_elements"):
            for index, entry in enumerate(value[group], 1):
                origin = sources.get(entry["source_id"])
                if origin is None or compact(entry["source_quote"]) not in compact(origin):
                    raise CreativeReviewError("故事目标解析引用了无效来源或非原文证据",
                                              [{"group": group, "entry": entry}])
                entry["id"] = ("R" if group == "requirements" else "E") + "%02d" % index
                if "forbidden_terms" in entry:
                    terms = list(dict.fromkeys(entry["forbidden_terms"] + entry.get("semantic_only_terms", [])))
                    entry["forbidden_terms"] = [term for term in terms if any(
                        compact(term) in compact(source) for source in sources.values())]
                    entry["semantic_only_terms"] = [term for term in terms if term not in entry["forbidden_terms"]]
        if {character["role"] for character in value["characters"]} != set(profile_roles(project)):
            raise CreativeReviewError("修订后的故事目标缺少固定制作角色")
        catalog = _resolved_evidence_catalog(value)
        catalog_path = folder / ("resolved-story-evidence-catalog-" + fingerprint(catalog)[:16] + ".json")
        write_json(catalog_path, {"schema": "story-evidence-catalog.v1", "items": catalog})
        evidence = {item["id"]: item["text"] for item in catalog}
        report(checkpoint, stage="story_resolution_review", progress=29,
               message="独立审稿人依据正文证据编号核对因果与责任（第%d版）" % revision)
        clean_target = {key: copy.deepcopy(value[key]) for key in RESOLVED_STORY_SCHEMA["properties"]}
        raw_review = cached_json_call(
            project, config, "story_critic", "resolved-story-review-ids-%d" % revision,
            review_system, {"project": _safe_context(project), "producer_feedback": directions,
                            "resolved_target": clean_target, "evidence_catalog": catalog},
            resolved_review_schema(list(evidence), [item["id"] for item in value["requirements"]]),
            checkpoint, max_tokens=4200, temperature=.1)
        review = copy.deepcopy(raw_review)
        findings = _excluded_story_issues(value, value)
        for name, check in review["checks"].items():
            check["evidence_quotes"] = [evidence[ident] for ident in check["evidence_ids"]]
            if check["status"] == "fail":
                findings.append(_review_issue("resolved_" + name, check["reason"],
                                               " / ".join(check["evidence_quotes"])))
        for ident, check in review["requirements_review"].items():
            check["evidence_quotes"] = [evidence[item] for item in check["evidence_ids"]]
            if check["status"] == "fail":
                findings.append(_review_issue("resolved_requirement_" + ident,
                    "用户要求" + ident + "未落实：" + check["reason"],
                    " / ".join(check["evidence_quotes"])))
        originality = review["originality"]
        originality["evidence_quotes"] = [evidence[item] for item in originality["evidence_ids"]]
        if originality["score"] < 6:
            findings.append(_review_issue("resolved_originality_below_minimum",
                "目标原创组合评分%s，未达6分门槛：%s" % (
                    originality["score"], originality["reason"]),
                " / ".join(originality["evidence_quotes"])))
        for issue in review["issues"]:
            issue["evidence_quotes"] = [evidence[ident] for ident in issue["evidence_ids"]]
            issue["evidence"] = " / ".join(issue["evidence_quotes"])
        review["issues"] = findings + review["issues"]
        passed = (review["status"] == "pass" and
                  all(check["status"] == "pass" for check in review["checks"].values()) and
                  not any(issue["severity"] in ("blocking", "major") for issue in review["issues"]))
        review["passed"] = passed
        review["evidence_mode"] = "catalog_ids"
        review["evidence_catalog"] = relative_path(catalog_path)
        review["review_policy_signature"] = review_policy_signature
        value.update(schema="resolved-story-brief.v3", source_signature=source_signature,
                     resolution_revision=revision,
                     source_mapping={key: "project." + key if key != "selected_proposal"
                                     else "proposal:" + str(proposal["id"]) for key in sources})
        artifact = "resolved-story-brief-v%s-r%d-%s.json" % (
            project.get("script_generation", 1), revision, fingerprint(value)[:12])
        write_json(folder / artifact, value)
        history.append({"revision": revision, "target": relative_path(folder / artifact),
                        "review": copy.deepcopy(review),
                        "policy_reassessment": review_only})
        review_record = {"passed": passed, "maximum_target_versions": 2,
            "minimum_originality_score": 6, "evidence_mode": "catalog_ids", "maximum_review_evidence_repairs": 0,
            "review_evidence_repairs_used": 0, "policy_reassessment": review_only,
            "previous_policy_record": previous_policy_record, "rounds": history}
        write_json(folder / "resolved-story-review.json", review_record)
        write_json(folder / ("resolved-story-review-ids-v%s-r%d-%s.json" % (
            project.get("script_generation", 1), revision, fingerprint(raw_review)[:12])),
            {"target": relative_path(folder / artifact), "evidence_catalog": relative_path(catalog_path),
             "raw_review": raw_review, "review": review, "policy_reassessment": review_only})
        if passed or revision == 2 or review_only:
            break
        report(checkpoint, stage="story_resolution", progress=28,
               message="紧凑目标正在按独立评审修订；最多再改这一版")
        value = cached_json_call(project, config, "story_editor", "resolved-story-brief-revision-2",
            system + "\n现在修订当前紧凑目标。保留用户硬要求与删除项，AI自选回馈机制、后半段节拍及结局可重构；针对原创不足必须改变实际因果机制。纠正时序和道具持有人矛盾；不恢复旧提案或已删支线。来源引文沿用已有有效引文或逐字摘录producer_feedback。",
            {"project": _safe_context(project), "producer_feedback": directions,
             "current_resolved_target": value, "independent_feedback": review,
             "source_ids": list(sources),
             "task": "这是唯一一次紧凑目标返修。逐项满足用户要求和原创门槛，必要时重构AI方案的回馈机制；纠正因果、责任、物品来源与认领，不能增加无关支线。"},
            RESOLVED_STORY_SCHEMA, checkpoint, max_tokens=3800, temperature=.2)
    value["review_policy_signature"] = review_policy_signature
    value["reviewed_target_signature"] = fingerprint({key: value.get(key)
                                                     for key in RESOLVED_STORY_SCHEMA["properties"]})
    value["resolution_review"] = review_record
    write_json(folder / "resolved-story-brief.json", value)
    lines = ["# 按出品人反馈修订后的故事目标", "", "状态：" + ("通过独立目标评审" if passed else "仍有阻断项"),
             "目标版本：" + str(value["resolution_revision"]), "", "## " + value["title"],
             value["logline"], "", value["synopsis"], "", "结局：" + value["ending"],
             "", "## 因果节拍", *("- " + item for item in value["causal_beats"]),
             "", "## 必须达成"]
    lines.extend("- " + item["id"] + "：" + item["instruction"] + "；来源 " +
                 item["source_id"] + "「" + item["source_quote"] + "」" for item in value["requirements"])
    lines.extend(["", "## 必须删除"])
    lines.extend("- " + item["id"] + "：" + item["element"] + "；来源 " +
                 item["source_id"] + "「" + item["source_quote"] + "」" for item in value["excluded_elements"])
    lines.extend(["", "## 独立目标评审", review["summary"]])
    lines.extend("- " + issue["description"] + "；证据：" + issue["evidence"]
                 for issue in review["issues"])
    atomic_text(folder / "resolved-story-brief.md", "\n".join(lines) + "\n")
    if not passed:
        raise CreativeReviewError("紧凑故事目标的独立评审仍有阻断项，已保留目标版本与评审证据",
                                  review["issues"])
    return value


def _excluded_story_issues(episode, resolved):
    """Enforce source-backed literal exclusions without trusting critic praise."""
    if not resolved:
        return []
    narrative = {key: episode.get(key) for key in (
        "title", "logline", "synopsis", "ending", "story_structure", "character_names",
        "cast", "shots", "characters", "causal_beats", "creative_plan")}
    text = json.dumps(narrative, ensure_ascii=False)
    issues = []
    for entry in resolved.get("excluded_elements", []):
        hits = [term for term in entry.get("forbidden_terms", []) if term in text]
        if hits:
            issues.append(_review_issue("producer_excluded_element",
                "成稿重新出现出品人要求删除的要素：" + entry["element"],
                "源要求" + entry.get("id", "") + "；实际命中：" + "、".join(hits)))
    return issues


def _make_script(project, config, checkpoint, feedback=None):
    proposal = _selected_proposal(project)
    if proposal.get("eligible") is False:
        raise CreativeReviewError("该故事方案存在未解决的评审问题，不能直接进入制作",
                                  proposal.get("review", {}).get("issues", []))
    folder = planning_dir(project)
    plan = duration_plan(project)
    previous = project.get("episode") or project.get("script")
    producer_feedback = _producer_feedback(project, feedback)
    local_feedback = feedback if feedback not in (None, "", {}, []) else project.get("script_feedback")
    old_shots = {s["id"]: s for s in (previous or {}).get("shots", [])}
    affected = revision_shot_ids(local_feedback, old_shots)
    revision_style = _revision_visual_style(project, config, checkpoint) if affected else None
    resolved = (copy.deepcopy(project.get("resolved_story_brief")) if affected else
                resolve_story_brief(project, config, checkpoint, feedback))
    proposal_reference = None if resolved else {
        key: value for key, value in proposal.items() if key not in ("review", "scores", "eligible")}
    resolved_goal = {key: value for key, value in resolved.items() if key != "resolution_review"} if resolved else None
    creative_scope = {
        "hard_fields": ["requirements", "excluded_elements"],
        "working_plan_fields": ["synopsis", "ending", "causal_beats", "creative_plan", "retained_elements"],
        "allow_restructure_second_half": not bool(affected),
        "rule": "保留用户要求、删除项与已选主题；AI自选回馈机制和后半段可重构，实际setup/choice/payoff必须落实到镜头与声音。"}
    payload = {"project": _safe_context(project), "selected_proposal": proposal_reference,
        "resolved_story_brief": resolved_goal, "creative_scope": creative_scope,
        "timeline": [{"id": "S%02d" % (i + 1), "seconds": seconds} for i, seconds in enumerate(plan)],
        "authorized_adaptation_material": _adaptation_material(project),
        "previous_episode": previous if affected else None,
        "producer_feedback": producer_feedback, "proposal_reference_only_where_feedback_does_not_override": True,
        "only_revise_shot_ids": affected,
        "approved_visual_traits": revision_style["prompt"] if revision_style else None}
    separate_storyboard = config.get("creative", {}).get("separate_storyboard", False)
    if not isinstance(separate_storyboard, bool):
        raise ValueError("creative.separate_storyboard must be a boolean")
    writer_system = SCRIPT_SYSTEM
    if separate_storyboard:
        writer_system = SCRIPT_SYSTEM.replace("你是短剧编剧兼分镜导演", "你是短剧编剧", 1) + (
            "\n独立分镜导演将在你之后编排prompt/motion_prompt/camera_motion/reference_group。"
            "你先完成剧情、action、对白与连续性，并提供这些制作字段的初稿；导演不能替你改剧情。")
        writer_system = derive_prompt(SCRIPT_SYSTEM, writer_system, "separate_storyboard")
    history, episode, final_review, direction = [], None, None, None
    for index in range(1, 3):
        report(checkpoint, stage="script_creation", progress=29 + index * 2,
               message=("编剧正在完成剧情、人物、对白和逐镜动作（第%d轮）" if separate_storyboard else
                        "编剧与导演正在编写完整剧本和逐镜分镜（第%d轮）") % index)
        request = copy.deepcopy(payload)
        if episode is not None:
            if not resolved or affected:
                request["previous_attempt"] = episode
            else:
                request["rewrite_instruction"] = "保留requirements和删除项，在已选主题内重构AI工作方案；可重新设计回馈机制与后半结构。用实际setup/choice/payoff解决原创和结局问题，不依赖换礼物或追加尾声。逐项处理独立评审证据。"
            request["independent_feedback"] = final_review
        raw = cached_json_call(project, config, "screenwriter", "screenplay-%d" % index,
            writer_system, request, episode_schema(len(plan), project), checkpoint,
            max_tokens=11500, temperature=0.65)
        episode = _canonical_episode(raw, project)
        if affected and previous:
            for i, shot in enumerate(episode["shots"]):
                if shot["id"] not in affected:
                    episode["shots"][i] = copy.deepcopy(old_shots[shot["id"]])
                elif revision_style:
                    shot["render_style_prompt"] = revision_style["prompt"]
                    shot["render_style_source"] = {key: revision_style[key]
                                                   for key in ("source_signature", "artifact")}
            episode["cast"] = copy.deepcopy(previous["cast"])
            episode["character_names"] = copy.deepcopy(previous.get("character_names", episode["character_names"]))
            episode["visual_style"] = previous["visual_style"]
        director_issues = []
        if separate_storyboard:
            direction = direct_storyboard(project, config, episode, checkpoint,
                approved_story=resolved_goal or proposal_reference,
                shot_ids=affected or None, round_index=index,
                producer_feedback=producer_feedback,
                repair_issues=(final_review or {}).get("issues", []))
            episode, director_issues = direction["episode"], direction["issues"]
        structural = (technical_issues(episode, project) +
                      _excluded_story_issues(episode, resolved) + director_issues)
        report(checkpoint, stage="script_review", progress=32 + index * 3,
               message="独立审稿人正在逐镜检查剧情闭合、声音槽位和连续性")
        final_review = cached_json_call(project, config, "independent_critic",
            "screenplay-review-%d" % index, CRITIC_SYSTEM,
            {"project": _safe_context(project), "selected_story": resolved["logline"] if resolved else proposal["logline"],
             "resolved_story_brief": resolved_goal, "creative_scope": creative_scope,
             "required_checks": "逐项核对新目标requirements及excluded_elements。删除项不得重现；角色动作、责任和道歉归属须与因果链一致，不以作者自称合规作为证据。",
             "producer_feedback": producer_feedback,
             "proposal_reference_only_where_feedback_does_not_override": True,
             "episode": episode, "technical_findings": structural,
             "task": "以出品人反馈修订后的故事目标评价完整成片因果链、结局与反转证据。原提案冲突部分已被反馈覆盖；不得因遵守删除要求而扣分，仍保留明确被删除要素则指出镜头证据并阻断。"},
            SCRIPT_REVIEW_SCHEMA, checkpoint, max_tokens=5800, temperature=0.25)
        final_review["issues"] = structural + final_review["issues"]
        final_review["passed"] = review_passes(final_review)
        history.append({"round": index, "review": copy.deepcopy(final_review),
                        **({"storyboard": direction["receipt"]} if direction else {})})
        write_json(folder / "script-review.json", {"passed": final_review["passed"], "rounds": history,
                   "producer_feedback": producer_feedback, "resolved_story_brief": resolved})
        write_json(folder / "episode.json", episode)
        atomic_text(folder / "script.md", _script_doc(episode, final_review))
        export_storyboard(episode, folder / "storyboard.csv")
        if final_review["passed"]:
            break
    passed = final_review["passed"]
    if affected and project.get("styles"):
        # A rejected local script revision does not revoke the approved visual
        # specification: later feedback still needs it to compile changed shots.
        styles = copy.deepcopy(project["styles"])
    elif passed:
        styles = _styles(project, config, episode, checkpoint)
    else:
        styles = []
    write_json(folder / "styles.json", styles)
    if styles:
        lines = ["# 视觉方向", "", "下列为视觉开发规格；真实样片由制作引擎随后生成。", ""]
        for style in styles:
            lines.extend(["## " + style["name"], style["description"],
                "色板：" + " · ".join(style["palette"]), style["prompt"], ""])
        atomic_text(folder / "styles.md", "\n".join(lines) + "\n")
    snapshot = "episode-v%s-%s.json" % (project.get("script_generation", 1), fingerprint(episode)[:12])
    write_json(folder / snapshot, episode)
    return {"script": episode, "episode": episode, "styles": styles,
        "resolved_story_brief": resolved,
        "shots": _shot_metadata(episode, project),
        "script_review": {"passed": passed, "rounds": history, "review": final_review,
                          "producer_feedback": producer_feedback},
        "needs_decision": None if passed else _gate("script_review",
            "剧本经过一次自动返修后仍有阻断项，请补充创作反馈后再继续。",
            _script_gate_issues(final_review)),
        "planning_artifacts": {**project.get("planning_artifacts", {}),
            "resolved_story_brief": relative_path(folder / "resolved-story-brief.md") if resolved else None,
            "script": relative_path(folder / "script.md"),
            "storyboard": relative_path(folder / "storyboard.csv"),
            "episode": relative_path(folder / "episode.json"),
            "script_review": relative_path(folder / "script-review.json"),
            **({"storyboard_direction": direction["artifact"]} if direction else {}),
            "styles": relative_path(folder / "styles.json")},
        "script_updated_at": utc_now()}


def create_script_and_styles(project, config, checkpoint):
    return _make_script(project, config, checkpoint)


def revise_script(project, config, checkpoint, feedback):
    if not feedback:
        raise CreativeReviewError("请提供需要修改的剧情或镜头说明")
    return _make_script(project, config, checkpoint, feedback=feedback)
