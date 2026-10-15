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


def _project(state):
    p=store.get(state["project_id"],False)
    if p.get("pause_requested"):
        raise ProjectPaused("已在检查点暂停")
    return p


def _checkpoint(pid):
    return lambda patch: store.update(pid,patch)


def _apply(pid, patch):
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
    return {"route":"continue","style_generation":p["script_generation"]}


def visual_development(state):
    from .production import generate_style_previews
    p=_project(state)
    if p.get("style_previews_generation") != p["script_generation"]:
        store.update(p["id"],{"status":"visual_development","stage":"style_previews","progress":40,
                             "message":"美术正在生成三套真实视觉预览，完成后交给出品人选择。"})
        styles=generate_style_previews(p,production_config(p,load_config()),_checkpoint(p["id"]),
                                      lambda:store.get(p["id"],False).get("pause_requested",False))
        patch={"style_previews_generation":p["script_generation"],"progress":45}
        if isinstance(styles,list):
            patch["styles"]=styles
        elif isinstance(styles,dict) and "styles" in styles:
            patch.update(styles)
        store.update(p["id"],patch)
    return {"route":"continue"}


def style_decision(state):
    p=_project(state)
    options=[{"id":v["id"],"label":v.get("name",v["id"])} for v in p["styles"]]
    version=state.get("style_generation",p["script_generation"])
    gate=store.gate(p["id"],"style",options,"三套视觉预览已完成，请选择人物与画风。",version)
    from .supervision import supervise_gate
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
        return {"route":"continue"}
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


def build_graph(checkpointer):
    from .team_tasks import TASKS, tracked_node
    builder=StateGraph(State)
    for name,fn in [("research",research),("proposals",proposals),("story_decision",story_decision),
                    ("apply_story",apply_story),("apply_style",apply_style),("apply_final",apply_final),
                    ("screenplay",screenplay),("visual_development",visual_development),
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
    builder.add_edge("apply_style","production")
    builder.add_conditional_edges("production",lambda s:s["route"],
                                  {"continue":"final_decision","revision":"revision"})
    builder.add_edge("final_decision","apply_final")
    builder.add_conditional_edges("apply_final",lambda s:s["route"],{"done":END,"revision":"revision"})
    builder.add_edge("revision","production")
    return builder.compile(checkpointer=checkpointer)


@contextmanager
def durable_graph():
    DATA.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(CHECKPOINTS),check_same_thread=False,timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        saver=SqliteSaver(conn)
        yield build_graph(saver)
    finally:
        conn.close()


def advance(pid):
    # One invocation can cross several automatic nodes, but stops at an
    # interrupt. All of those nodes share the same connection/role snapshot.
    with config_snapshot():
        return _advance(pid)


def _advance(pid):
    p=store.get(pid,False)
    if p.get("pause_requested") or p["status"]=="paused":
        raise ProjectPaused("已在检查点暂停")
    if p["status"] in {"blocked","failed","cancelled"}:
        return {"blocked":True,"status":p["status"]}
    with durable_graph() as graph:
        config={"configurable":{"thread_id":pid},"recursion_limit":100}
        snap=graph.get_state(config)
        if p.get("production_edit_revision"):
            from .production_edits import resume_edited_graph
            snap=resume_edited_graph(graph,snap,p,config)
        if not snap.values:
            return graph.invoke({"project_id":pid},config)
        pending=[i for task in snap.tasks for i in task.interrupts]
        if pending:
            gate=pending[0].value
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
