"""Persistent graph with isolated decision nodes and resumable production effects."""
from __future__ import annotations

import copy
import sqlite3
from contextlib import contextmanager
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from studio.core import ROOT, write_json

from . import store
from .config import CHECKPOINTS, DATA, load_config, production_config, config_snapshot
from .llm import json_call, review_media
from .dialogue import compress_dialogue


class ProjectPaused(RuntimeError):
    pass


class ReviewBlocked(RuntimeError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


class State(TypedDict, total=False):
    project_id: str
    route: str
    story_generation: int
    style_generation: int
    final_generation: int
    story_answer: dict
    style_answer: dict
    final_answer: dict
    direct_production_revision: int
    restart_revision: int
    timeline_edit_version: str


def _project(state):
    p=store.get(state["project_id"],False)
    from .team_control import admit
    from .execution_context import current_execution
    context = current_execution.get() or {}
    admit(p["id"], "stage", context.get("stage", p.get("stage", "production")))
    if p.get("pause_requested"):
        raise ProjectPaused("已在检查点暂停")
    return p


def _checkpoint(pid):
    from .runtime_observations import checkpoint
    return lambda patch: checkpoint(pid,patch)


def _apply(pid, patch):
    from .team_control import assert_publishable
    from .execution_context import current_execution
    context = current_execution.get() or {}
    if context.get("project_id") == pid:
        assert_publishable(pid, "stage", context.get("stage", "production"))
    if patch.get("needs_decision"):
        patch={k:v for k,v in patch.items() if not k.endswith("_complete_generation") and k not in {"research_complete","revision_prepared"}}
        store.update(pid,patch)
        detail=patch["needs_decision"]
        raise ReviewBlocked("创作评审仍有需处理的问题",detail if isinstance(detail,dict) else {"detail":detail})
    return store.update(pid,patch)


def research(state):
    from .research import research_brief
    from studio.core import read_json
    p=_project(state)
    folder=ROOT/"production"/p["slug"]
    owner=folder/"factory_owner.json"
    if owner.exists():
        if read_json(owner).get("project_id") != p["id"]:
            raise ValueError("制片工程归属不匹配")
    else:
        if (folder/"episode.json").exists():
            raise ValueError("已保留现有未登记工程，不自动覆盖")
        write_json(owner,{"project_id":p["id"],"schema":"autonomous_factory.owner.v1"})
    if not p.get("research_complete"):
        store.update(p["id"],{"status":"researching","stage":"research","progress":5,
                              "message":"选题研究员正在检索公开资料和题材线索。"})
        patch=research_brief(p,load_config(),_checkpoint(p["id"]))
        patch["research_complete"]=True
        _apply(p["id"],patch)
    return {"route":"continue"}


def proposals(state):
    from .creative import create_proposals
    p=_project(state)
    if not p.get("research_complete"):
        research(state)
        p=_project(state)
    if p.get("proposals_complete_generation") != p["proposal_generation"]:
        store.update(p["id"],{"status":"creating","stage":"proposals","progress":12,
                              "message":"编剧团队提出三个原创方向，由独立评审检查冲突、动机和可制作性。"})
        patch=create_proposals(p,load_config(),_checkpoint(p["id"]))
        patch["proposals_complete_generation"]=p["proposal_generation"]
        _apply(p["id"],patch)
    return {"route":"continue","story_generation":p["proposal_generation"]}


def story_decision(state):
    p=_project(state)
    options=[{"id":v["id"],"label":v["title"]} for v in p["proposals"] if v.get("eligible",True)]
    if not options:
        raise ReviewBlocked("尚无通过评审的故事方案",{"kind":"creative_review"})
    version=state.get("story_generation",p["proposal_generation"])
    gate=store.gate(p["id"],"story",options,"故事提案评审已完成，请从合格方案中选择制作方向。",version)
    from .supervision import supervise_gate
    answer=store.answer_for(p["id"],gate["id"])
    if answer is None:
        answer=supervise_gate(p["id"],gate)
    if answer is None and p.get("team_mode") == "delegated":
        # A receipt may outlive a lost graph checkpoint, or a human may answer
        # during the model call. Consume that receipt without another request.
        answer=store.answer_for(p["id"],gate["id"])
    if answer is None:
        answer=interrupt(gate)
    if answer.get("gate_id") != gate["id"]:
        raise ValueError("Story decision checkpoint mismatch")
    return {"story_answer":answer,"story_generation":version}


def apply_story(state):
    p=_project(state)
    answer=state["story_answer"]
    if answer["gate_id"] in p.get("decision_effects",{}):
        return {"route":p["decision_effects"][answer["gate_id"]]["route"]}
    if answer["choice"]=="__repropose__":
        patch={"proposal_generation":p["proposal_generation"]+1,
               "proposals":[],"selected_story":None,"story_feedback":answer.get("feedback",""),
               "message":"根据反馈重新构思三个故事，旧提案和评审记录保留。"}
        return store.commit_decision(p["id"],answer,patch,"repropose")
    chosen=next(v for v in p["proposals"] if v["id"]==answer["choice"] and v.get("eligible",True))
    patch={"selected_story":chosen["id"],"title":chosen["title"],
           "story_feedback":answer.get("feedback",""),"progress":25}
    return store.commit_decision(p["id"],answer,patch,"continue")

def screenplay(state):
    from .creative import create_script_and_styles
    p=_project(state)
    if p.get("script_complete_generation") != p["script_generation"]:
        store.update(p["id"],{"status":"scripting","stage":"screenplay","progress":30,
                             "message":"编剧与导演正在完成剧本、人设、连续性设定和逐镜分镜。"})
        patch=create_script_and_styles(p,load_config(),_checkpoint(p["id"]))
        patch["script_complete_generation"]=p["script_generation"]
        _apply(p["id"],patch)
    return {"route":"continue","style_generation":p.get("style_generation",p["script_generation"])}


def visual_development(state):
    from .production import generate_style_previews
    p=_project(state)
    if p.get("style_previews_generation") != p.get("style_generation",p["script_generation"]):
        store.update(p["id"],{"status":"visual_development","stage":"style_previews","progress":40,
                             "message":"美术正在准备并审核视觉预览，已定稿的素材画风直接复用。"})
        config=load_config()
        from .creative_contract import resolve as resolve_contract
        from .drama_intent import enabled as whole_drama
        review_options=({"semantic_evaluator":lambda proj,shot,path,refs:review_media(proj,shot,path,refs,config)}
                        if resolve_contract(p) or whole_drama(p) else {})
        styles=generate_style_previews(p,production_config(p,config),_checkpoint(p["id"]),
                                      lambda:store.get(p["id"],False).get("pause_requested",False), **review_options)
        patch={"style_previews_generation":p.get("style_generation",p["script_generation"]),"progress":45}
        if isinstance(styles,list):
            patch["styles"]=styles
        elif isinstance(styles,dict) and "styles" in styles:
            patch.update(styles)
        store.update(p["id"],patch)
    return {"route":"continue"}


def style_decision(state):
    p=_project(state)
    options=[{"id":v["id"],"label":v.get("name",v["id"])} for v in p["styles"]]
    version=p.get("style_generation",p["script_generation"])
    title = "采用已集中确认的素材画风。" if len(p["styles"]) == 1 and p["styles"][0].get("material_reference") else "三套视觉预览已完成，请选择人物与画风。"
    gate=store.gate(p["id"],"style",options,title,version)
    from .supervision import supervise_gate
    answer=store.answer_for(p["id"],gate["id"])
    if answer is None and len(p["styles"]) == 1 and p["styles"][0].get("material_reference"):
        from .material_production import styles as material_styles
        confirmed = material_styles(p)
        if confirmed and confirmed[0]["id"] == p["styles"][0]["id"]:
            store.decide(p["id"], {"type": "style", "choice": confirmed[0]["id"], "decision_id": gate["id"]}, actor="producer_material_confirmation")
            answer = store.answer_for(p["id"], gate["id"])
    if answer is None:
        answer=supervise_gate(p["id"],gate)
    if answer is None and p.get("team_mode") == "delegated":
        # A receipt may outlive a lost graph checkpoint, or a human may answer
        # during the model call. Consume that receipt without another request.
        answer=store.answer_for(p["id"],gate["id"])
    if answer is None:
        answer=interrupt(gate)
    if answer.get("gate_id") != gate["id"]:
        raise ValueError("Style decision checkpoint mismatch")
    return {"style_answer":answer,"style_generation":version}


def apply_style(state):
    p=_project(state)
    answer=state["style_answer"]
    if answer["gate_id"] in p.get("decision_effects",{}):
        return {"route":p["decision_effects"][answer["gate_id"]]["route"]}
    if answer["choice"] == "__restyle__":
        feedback=answer.get("feedback", "")
        if not isinstance(feedback,str) or not feedback.strip():
            raise ValueError("请说明对视觉方案不满意的原因")
        generation=p.get("style_generation",p["script_generation"])+1
        history=copy.deepcopy(p.get("style_history",[]))
        history.append({"generation":generation-1,"styles":copy.deepcopy(p["styles"]),
                        "feedback":feedback,"at":store.now()})
        return store.commit_decision(p["id"],answer,
            {"style_generation":generation,"style_history":history,"selected_style":None,
             "style_feedback":feedback,"style_previews_generation":None,
             "visual_design_complete_generation":None,"progress":38,
             "message":"美术根据意见重新设计视觉方向；旧预览和累计费用保留。"},"restyle")
    chosen=next(v for v in p["styles"] if v["id"]==answer["choice"])
    script=copy.deepcopy(p["script"])
    script["visual_style"]=chosen["prompt"]
    write_json(ROOT/"production"/p["slug"]/"episode.json",script)
    patch={"selected_style":chosen["id"],"script":script,"episode":script,
           "style_feedback":answer.get("feedback",""),"progress":50}
    return store.commit_decision(p["id"],answer,patch,"continue")

def production(state):
    from .production import generate_project
    p=_project(state)
    if p.get("revision_request") and p.get("revision_prepared") != p["production_revision"]:
        return {"route":"revision"}
    if p.get("creative_quality_review_required"):
        raise ReviewBlocked("已启用新创作契约，请先重新独立审核当前稿件", {"type":"script_review", "message":"新契约尚无当前版本审核，原素材已保留。"})
    if (p.get("script_review") or {}).get("passed") is not True:
        raise ReviewBlocked("当前剧本尚未通过独立评审，不能开始制作",
                            {"type":"edit_feedback" if p.get("revision_request") else "script_review",
                             "message":"请修改评审指出的问题后重新审稿。","review":p.get("script_review")})
    if p.get("delivery_complete_revision") == p["production_revision"] and p.get("delivery"):
        return {"route":"continue","final_generation":p["production_revision"]}
    store.update(p["id"],{"status":"producing","stage":"production","progress":52,
                         "message":"已确认创作与视觉方向；系统开始制作、配音、质检和镜头级返修。"})
    config=load_config()
    delivery=generate_project(p,production_config(p,config),_checkpoint(p["id"]),
                              lambda:store.get(p["id"],False).get("pause_requested",False),
                              semantic_evaluator=lambda proj,shot,path,refs:review_media(proj,shot,path,refs,config),
                              dialogue_rewriter=compress_dialogue)
    from .shot_continuity import verify_delivery
    transition_review = verify_delivery(p, delivery, config, lambda: store.get(p["id"], False).get("pause_requested", False))
    if transition_review:
        delivery = {**delivery, "shot_continuity_review": transition_review}
        store.update(p["id"], {"shot_continuity_review": transition_review})
    from .drama_intent import enabled as whole_drama
    if whole_drama(p) and p.get("record_kind") == "episode":
        from .drama_delivery import review_episode
        current = store.update(p["id"], {"delivery": delivery, "status": "producing"})
        review_episode(current, config)
    store.update(p["id"],{"delivery":delivery,"delivery_complete_revision":p["production_revision"],
                         "progress":95,"error":None,"needs_decision":None,
                         "message":"完整初版已导出并完成自动检查，请查看成片后决定通过或修改。"})
    return {"route":"continue","final_generation":p["production_revision"]}


def final_decision(state):
    p=_project(state)
    version=state.get("final_generation",p["production_revision"])
    gate=store.gate(p["id"],"final",
        [{"id":"approve","label":"通过终版"},{"id":"revise","label":"局部修改"},{"id":"reedit","label":"重新剪辑"}],
        "完整初版已交付，请观看并决定最终版本。",version)
    from .supervision import supervise_gate
    answer=store.answer_for(p["id"],gate["id"])
    from .drama_intent import enabled as whole_drama
    from .edit_versions import adopted_current
    if answer is None and not whole_drama(p) and not adopted_current(p):
        answer=supervise_gate(p["id"],gate)
    if answer is None and p.get("team_mode") == "delegated":
        # A receipt may outlive a lost graph checkpoint, or a human may answer
        # during the model call. Consume that receipt without another request.
        answer=store.answer_for(p["id"],gate["id"])
    if answer is None:
        answer=interrupt(gate)
    if answer.get("gate_id") != gate["id"]:
        raise ValueError("Final decision checkpoint mismatch")
    return {"final_answer":answer,"final_generation":version}


def apply_final(state):
    p=_project(state)
    answer=state["final_answer"]
    applied=p.get("decision_effects",{}).get(answer["gate_id"])
    if applied:
        if applied["route"]=="done":
            store.remember(p["id"])
        return {"route":applied["route"]}
    if answer["choice"]=="approve":
        patch={"status":"approved","stage":"delivered","progress":100,
               "accepted_at":store.now(),"accepted_by":answer.get("actor","producer"),
               "message":"终版已通过，工程、素材、字幕与各版成片均保留。"}
        result=store.commit_decision(p["id"],answer,patch,"done")
        store.remember(p["id"])
        return result
    patch={"revision_request":answer,"production_revision":p["production_revision"]+1,
           "delivery":None,"error":None,"needs_decision":None}
    if answer["choice"]=="revise":
        patch["script_generation"]=p["script_generation"]+1
    return store.commit_decision(p["id"],answer,patch,"revision")

EDIT_SCHEMA={"type":"object","additionalProperties":False,
  "properties":{"supported":{"type":"boolean"},"reason":{"type":"string"},
    "changes":{"type":"array","items":{"type":"string"}},
    "edit_plan":{"type":"object","additionalProperties":False,"properties":{
      "shot_order":{"type":"array","items":{"type":"string"}},
      "trim_seconds":{"type":"object","additionalProperties":{"type":"number","minimum":1}},
      "music_gain":{"type":"number","minimum":0,"maximum":.3},
      "cut_style":{"type":"string","enum":["hard"]}},
      "required":["shot_order","trim_seconds","music_gain","cut_style"]}},
  "required":["supported","reason","changes","edit_plan"]}


def revision(state):
    from .creative import revise_script
    p=_project(state)
    request=p["revision_request"]
    if (p.get("revision_prepared") == p["production_revision"]
            and (p.get("script_review") or {}).get("passed") is True):
        return {"route":"continue"}
    store.update(p["id"],{"status":"revising","stage":"revision","progress":45,
                         "message":"导演正在将修改意见落实到镜头或剪辑单；已通过且未变更的素材继续复用。"})
    if request["choice"]=="reedit":
        result=json_call(load_config(),"edit_director",
            "你是短剧剪辑师。只在已有镜头上落实用户重剪意见。允许重排镜头、缩短镜长、调整配乐增益；不支持新动作、新配音或新剧情。"
            "shot_order必须是原有全部镜头ID的排列，每镜仅出现一次；trim_seconds只能缩短且不得短于对白结束所需的时长。"
            "无法落实或意见不明确时supported=false并说明；不要生成不产生实际改变的剪辑单。",
            {"feedback":request["feedback"],"shots":p["script"]["shots"],
             "current_order":[s["id"] for s in p["script"]["shots"]],
             "current_edit_plan":p.get("edit_plan"),"default_music_gain":.12},
            EDIT_SCHEMA,ROOT/"production"/p["slug"]/"planning",max_tokens=2400,temperature=.25)
        if not result["supported"]:
            raise ReviewBlocked(result["reason"],{"type":"edit_feedback","message":result["reason"]})
        ids=[s["id"] for s in p["script"]["shots"]]
        if sorted(result["edit_plan"]["shot_order"])!=sorted(ids):
            raise ReviewBlocked("剪辑单中的镜头与当前工程不一致")
        patch={"edit_plan":result["edit_plan"],"edit_feedback":request["feedback"],
               "reedit_requested":True,"edit_changes":result["changes"]}
        write_json(ROOT/"production"/p["slug"]/"planning"/f"edit-plan-v{p['production_revision']}.json",result)
    else:
        patch=revise_script(p,load_config(),_checkpoint(p["id"]),request["feedback"])
        patch.update(reedit_requested=False,edit_plan=None)
        if patch.get("needs_decision") or (patch.get("script_review") or {}).get("passed") is not True:
            problem=patch.get("needs_decision") or {"type":"script_review","review":patch.get("script_review")}
            patch["needs_decision"]={"type":"edit_feedback",
                                    "message":"局部修改未通过独立评审，请补充修改意见后重试。",
                                    "review":problem}
            patch["delivery"]=None
            _apply(p["id"],patch)
    patch.update(revision_prepared=p["production_revision"],delivery=None)
    _apply(p["id"],patch)
    return {"route":"continue"}


def build_graph(checkpointer, workflow_version=1):
    if workflow_version == 3:
        return _build_graph_v2(checkpointer, pinned=True)
    if workflow_version == 2:
        return _build_graph_v2(checkpointer)
    if workflow_version != 1:
        raise ValueError("不支持的工作流版本")
    from .team_tasks import TASKS, tracked_node
    builder=StateGraph(State)
    for name,fn in [("research",research),("proposals",proposals),("story_decision",story_decision),
                    ("apply_story",apply_story),("apply_style",apply_style),("apply_final",apply_final),
                    ("screenplay",screenplay),("visual_design",visual_design),("visual_development",visual_development),
                    ("style_decision",style_decision),("production",production),
                    ("final_decision",final_decision),("revision",revision)]:
        builder.add_node(name,tracked_node(name,fn) if name in TASKS else fn)
    builder.add_edge(START,"research")
    builder.add_edge("research","proposals")
    builder.add_edge("proposals","story_decision")
    builder.add_edge("story_decision","apply_story")
    builder.add_conditional_edges("apply_story",lambda s:s["route"],
                                  {"repropose":"proposals","continue":"screenplay"})
    builder.add_edge("screenplay","visual_development")
    builder.add_edge("visual_development","style_decision")
    builder.add_edge("style_decision","apply_style")
    builder.add_conditional_edges("apply_style",lambda s:s["route"],
                                  {"continue":"production","restyle":"visual_design"})
    builder.add_edge("visual_design","visual_development")
    builder.add_conditional_edges("production",lambda s:s["route"],
                                  {"continue":"final_decision","revision":"revision"})
    builder.add_edge("final_decision","apply_final")
    builder.add_conditional_edges("apply_final",lambda s:s["route"],{"done":END,"revision":"revision"})
    builder.add_edge("revision","production")
    return builder.compile(checkpointer=checkpointer)


@contextmanager
def durable_graph(workflow_version=1):
    DATA.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(CHECKPOINTS),check_same_thread=False,timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        saver=SqliteSaver(conn)
        yield build_graph(saver) if workflow_version == 1 else build_graph(saver,workflow_version)
    finally:
        conn.close()


def advance(pid):
    # One invocation can cross several automatic nodes, but stops at an
    # interrupt. All of those nodes share the same connection/role snapshot.
    with config_snapshot():
        result = _advance(pid)
        from .drama_intent import enabled as whole_drama
        if whole_drama(store.get(pid, False)):
            # A fresh graph invocation can first arrive at an intermediate
            # interrupt; consume delegated choices in the same owned job.
            for _ in range(2):
                current = store.get(pid, False)
                if current['status'] not in {'awaiting_story', 'awaiting_style'}:
                    break
                result = _advance(pid)
                if store.get(pid, False)['status'] == current['status']:
                    break  # An explicit escalation remains a real decision.
        return result


def _advance(pid):
    p=store.get(pid,False)
    if p.get("pause_requested") or p["status"]=="paused":
        raise ProjectPaused("已在检查点暂停")
    if p["status"] in {"blocked","failed","cancelled"}:
        return {"blocked":True,"status":p["status"]}
    from .edit_versions import handle_adopted_final, require_adopted_current
    require_adopted_current(p)
    handled = handle_adopted_final(p)
    if handled is not None:
        return handled
    with durable_graph(p.get("workflow_version",1)) as graph:
        config={"configurable":{"thread_id":pid},"recursion_limit":100}
        snap=graph.get_state(config)
        if p.get("workflow_restart_from"):
            snap=_restart_graph(graph,snap,p,config)
        if p.get("production_edit_revision"):
            from .production_edits import resume_edited_graph
            snap=resume_edited_graph(graph,snap,p,config)
        from .edit_versions import resume_edited_graph as resume_timeline_graph
        snap=resume_timeline_graph(graph,snap,p,config)
        if not snap.values:
            return graph.invoke({"project_id":pid},config)
        pending=[i for task in snap.tasks for i in task.interrupts]
        if pending:
            gate=pending[0].value
            answer=store.answer_for(pid,gate["id"])
            if answer is None and gate.get("type") in {"story","style"} and store.get(pid,False).get("team_mode")=="delegated":
                if store.get(pid,False).get("status")=="queued":
                    store.update(pid,{"status":"awaiting_"+gate["type"],"stage":gate["type"]+"_decision"})
                from .supervision import supervise_gate
                answer=supervise_gate(pid,gate)
                if answer is None:
                    answer=store.answer_for(pid,gate["id"])
            if answer is None:
                p=store.get(pid,False)
                if p.get("pause_requested"):
                    raise ProjectPaused("已暂停")
                store.update(pid,{"status":"awaiting_"+gate["type"],"stage":gate["type"]+"_decision"})
                return {"waiting":gate}
            return graph.invoke(Command(resume=answer),config)
        if snap.next:
            return graph.invoke(None,config)
        return {"finished":True}


def story_resolution(state):
    from .creative_runtime import resolve_target
    p=_project(state)
    if p.get('story_resolution_complete_generation') != p['script_generation']:
        store.update(p['id'],{'status':'scripting','stage':'story_resolution','progress':27,
                              'message':'故事编辑正在落实出品人目标，独立目标审稿检查要求与因果。'})
        _apply(p['id'],resolve_target(p,load_config(),_checkpoint(p['id'])))
    resolved=_project(state).get('resolved_story_brief')
    return {'route':'continue','skipped':_project(state).get('story_resolution_skipped',resolved is None)}


def screenplay_v2(state):
    from .creative_runtime import round_limit_patch, write_screenplay
    p=_project(state)
    if p.get('story_resolution_complete_generation') != p['script_generation']:
        story_resolution(state)
        p=_project(state)
    blocked=round_limit_patch(p,load_config())
    if blocked:
        _apply(p['id'],blocked)
    marker=[p['script_generation'],p.get('creative_round',1)]
    if p.get('screenplay_complete_round') != marker:
        store.update(p['id'],{'status':'scripting','stage':'screenplay','progress':30,
                              'message':'编剧正在编写剧情、人物动作与对白。'})
        _apply(p['id'],write_screenplay(p,load_config(),_checkpoint(p['id'])))
    return {'route':'continue'}


def storyboard(state):
    from .creative_runtime import direct_screenplay, round_limit_patch
    p=_project(state)
    blocked=round_limit_patch(p,load_config())
    if blocked:
        _apply(p['id'],blocked)
    marker=[p['script_generation'],p.get('creative_round',1)]
    if p.get('storyboard_complete_round') != marker:
        store.update(p['id'],{'status':'scripting','stage':'storyboard','progress':33,
                              'message':'分镜导演正在落实镜头、画面与连续性依据。'})
        _apply(p['id'],direct_screenplay(p,load_config(),_checkpoint(p['id'])))
    return {'route':'continue'}


def script_review(state):
    from .creative_runtime import review_screenplay, round_limit_patch
    from .review_policy import automation, exhausted_message, max_review_rounds
    p=_project(state)
    if p.get('script_complete_generation') == p['script_generation']:
        return {'route':'continue'}
    blocked=round_limit_patch(p,load_config())
    if blocked:
        _apply(p['id'],blocked)
    if (p.get('script_review_complete_round')==p.get('screenplay_complete_round')
            and p.get('script_review_route') in {'repair','continue'}):
        if p['script_review_route'] == 'repair':
            from .creative_contracts import can_repair
            saved_review = p.get('draft_review') or p.get('script_review', {}).get('review', {})
            if not can_repair(p, saved_review):
                raise ReviewBlocked('当前审核需确认创作方向或补齐证据，未自动重写', {'type': 'script_review', 'issues': saved_review.get('issues', [])})
            # A legacy checkpoint can contain the failed review at its former
            # two-round ceiling. Move forward once, without reviewing it again.
            reviewed_generation,index=p['script_review_complete_round']
            if reviewed_generation != p['script_generation']:
                raise ReviewBlocked('审核版本与当前剧本不匹配，请刷新项目')
            limit=max_review_rounds(load_config())
            if p.get('creative_round',1) == index:
                status='repairing' if index < limit else 'blocked'
                patch={'review_automation':automation(load_config(),'script',index,
                    reviewed_generation,status,
                    '按已保存的独立审核问题继续自动返修' if index < limit else '自动返修轮次已用尽')}
                if index < limit:
                    patch.update(creative_round=index+1,needs_decision=None)
                else:
                    from .creative import _gate, _script_gate_issues
                    patch['needs_decision']=_gate('script_review',exhausted_message('剧本',index),
                        _script_gate_issues(p.get('draft_review') or p.get('script_review',{}).get('review',{})))
                _apply(p['id'],patch)
        return {'route':p['script_review_route']}
    store.update(p['id'],{'status':'scripting','stage':'script_review','progress':36,
                          'message':'独立审稿人正在检查剧本与分镜，未通过则定向返修。'})
    patch=review_screenplay(p,load_config(),_checkpoint(p['id']))
    _apply(p['id'],patch)
    return {'route':'continue' if patch['script_review']['passed'] else 'repair'}


def visual_design(state):
    from .creative_runtime import design_visuals
    p=_project(state)
    generation=p.get('style_generation',p['script_generation'])
    if p.get('visual_design_complete_generation') != generation:
        store.update(p['id'],{'status':'visual_development','stage':'visual_design','progress':39,
                              'message':'美术指导根据本剧定稿与剧本准备视觉方向。'})
        _apply(p['id'],design_visuals(p,load_config(),_checkpoint(p['id'])))
    return {'route':'continue','style_generation':generation}


def _build_graph_v2(checkpointer, pinned=False):
    from .team_tasks import tracked_node
    builder=StateGraph(State)
    nodes={'research':research,'proposals':proposals,'story_decision':story_decision,
           'apply_story':apply_story,'story_resolution':story_resolution,'screenplay':screenplay_v2,
           'storyboard':storyboard,'script_review':script_review,'visual_design':visual_design,
           'visual_development':visual_development,'style_decision':style_decision,'apply_style':apply_style,
           'production':production,'final_decision':final_decision,'apply_final':apply_final,'revision':revision}
    for name,fn in nodes.items():
        builder.add_node(name,tracked_node(name,fn) if name not in {'story_decision','apply_story','style_decision','apply_style','final_decision','apply_final'} else fn)
    for source,target in [((START if not pinned else 'unused_start'),'research'),('research','proposals'),('proposals','story_decision'),
                          ('story_decision','apply_story'),('story_resolution','screenplay'),
                          ('screenplay','storyboard'),('storyboard','script_review'),
                          ('visual_design','visual_development'),('visual_development','style_decision'),
                          ('style_decision','apply_style'),('final_decision','apply_final'),('revision','production')]:
        if source != 'unused_start': builder.add_edge(source,target)
    if pinned: builder.add_edge(START, 'story_resolution')
    builder.add_conditional_edges('apply_story',lambda s:s['route'],{'repropose':'proposals','continue':'story_resolution'})
    builder.add_conditional_edges('script_review',lambda s:s['route'],{'repair':'screenplay','continue':'visual_design'})
    builder.add_conditional_edges('apply_style',lambda s:s['route'],{'restyle':'visual_design','continue':'production'})
    if pinned:
        def unit_done(state):
            p = _project(state)
            store.update(p['id'], {'status': 'unit_ready', 'stage': 'unit_ready', 'progress': 100})
            return {'route': 'continue'}
        builder.add_node('unit_ready', unit_done)
        builder.add_edge('unit_ready', END)
        builder.add_conditional_edges('production', lambda s: 'revision' if s['route'] == 'revision' else
            'unit' if store.get(s['project_id'], False).get('record_kind') == 'scene_unit' else 'continue',
            {'continue':'final_decision','revision':'revision','unit':'unit_ready'})
    else:
        builder.add_conditional_edges('production',lambda s:s['route'],{'continue':'final_decision','revision':'revision'})
    builder.add_conditional_edges('apply_final',lambda s:s['route'],{'done':END,'revision':'revision'})
    return builder.compile(checkpointer=checkpointer)


def _restart_graph(graph,snapshot,project,configuration):
    """Fence explicit recovery in the graph checkpoint before consuming a command.

    A crash between update_state and the store write sees restart_revision and
    clears the same command without scheduling another generation.
    """
    version=project.get('workflow_version',1)
    target=project['workflow_restart_from']
    revision=project.get('workflow_restart_revision')
    if type(revision) is not int or revision > project['revision']:
        raise ValueError('恢复请求版本无效')
    routes={'research':(START,'continue'),'proposals':('research','continue'),
            'story_resolution':('apply_story','continue'),'screenplay':('story_resolution','continue'),
            'visual_design':('script_review','continue'), 'storyboard':('screenplay','continue'),
            'script_review':('storyboard','continue')} if version in {2,3} else {
            'research':(START,'continue'),'proposals':('research','continue'),
            'story_resolution':('apply_story','continue'),'screenplay':('apply_story','continue'),
            'visual_design':('apply_style','restyle')}
    routes['revision']=('apply_final','revision')
    if target not in routes:
        raise ValueError('未知恢复节点')
    if snapshot.values.get('restart_revision') != revision:
        predecessor,route=routes[target]
        graph.update_state(configuration,{'project_id':project['id'],'route':route,'restart_revision':revision},as_node=predecessor)
        snapshot=graph.get_state(configuration)
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        current=store._read(db,project['id'])
        if current.get('workflow_restart_revision') == revision:
            current.pop('workflow_restart_from',None)
            current.pop('workflow_restart_revision',None)
            store._write(db,current)
    return snapshot
