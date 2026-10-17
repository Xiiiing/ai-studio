"""Logical view of the executable graph, not an independent scheduler."""
from __future__ import annotations

from .operator_console import obj, rows, PHASES, current_gate
from .team_tasks import snapshot as tasks_snapshot

GROUPS = (
    {"id": "direction", "label": "统筹与决策", "lead": "executive_producer", "roles": ["executive_producer", "producer"], "responsibility": "比较合格方案、汇总风险；出品人掌握费用与终审。"},
    {"id": "story", "label": "故事开发", "lead": "screenwriter", "roles": ["researcher", "creator", "story_editor", "screenwriter", "storyboard_director"], "responsibility": "把需求转成有因果、有人物动机的可执行剧本和分镜。"},
    {"id": "media", "label": "视觉与声音", "lead": "visual_designer", "roles": ["visual_designer", "prompt_style_compiler", "image_generator", "video_generator", "speech", "dialogue_editor", "editor"], "responsibility": "制作人物、镜头与对白，并完成剪辑、字幕和混音。"},
    {"id": "review", "label": "独立质量审核", "lead": "independent_critic", "roles": ["story_critic", "independent_critic", "visual_critic"], "responsibility": "独立检查叙事、画面与连续性；未通过则返回指定阶段修复。"},
)
def topology(workflow_version=1):
    from .workflows import build_graph
    from .team_tasks import contracts
    graph=(build_graph(None) if workflow_version==1 else build_graph(None,workflow_version)).get_graph().to_json()
    tasks={row['id']:row for row in contracts(workflow_version)}
    decisions={
        'story_decision': ('故事决策', ['proposals'], ['selected_story']),
        'style_decision': ('视觉决策', ['styles', 'style_previews'], ['selected_style']),
        'final_decision': ('终片审阅', ['delivery', 'technical_checks'], ['final_answer']),
        'delivery': ('正式交付', ['final_answer'], ['accepted_delivery']),
    }
    mapped={'apply_story':'story_decision','apply_style':'style_decision','apply_final':'delivery'}
    nodes={}
    for actual in graph['nodes']:
        executable=actual['id']
        if executable in ('__start__','__end__'):
            continue
        ident=mapped.get(executable,executable)
        if ident in nodes:
            nodes[ident]['execution_nodes'].append(executable)
            continue
        if ident == 'unit_ready':
            spec={'label':'制作单元就绪','owner':'editor','reviewer':'visual_critic','members':['editor','visual_critic'],
                  'inputs':['delivery'],'outputs':['unit_ready']}
        elif ident in decisions:
            label,inputs,outputs=decisions[ident]
            spec={'label':label,'owner':'producer','reviewer':None,
                  'members':['executive_producer','producer'],'inputs':inputs,'outputs':outputs}
        else:
            spec=tasks[ident]
        members=list(dict.fromkeys(spec['members']+([spec['reviewer']] if spec.get('reviewer') else [])))
        nodes[ident]={'id':ident,'label':spec['label'],'owner':spec['owner'],'reviewer':spec.get('reviewer'),
                      'members':members,'execution_nodes':[executable],
                      '_input_keys':spec['inputs'], '_output_keys':spec['outputs'],
                      'input':' / '.join(spec['inputs']),'output':' / '.join(spec['outputs'])}
    edges=[]
    for edge in graph['edges']:
        a=mapped.get(edge['source'],edge['source']); b=mapped.get(edge['target'],edge['target'])
        if a in ('__start__','__end__') or b in ('__start__','__end__') or a==b:
            continue
        label=str(edge.get('data') or 'continue')
        edges.append({'source':a,'target':b,'label':label,'loop':bool(edge.get('conditional')) and label!='continue'})
    from .workflow_presentation import decorate
    return decorate(list(nodes.values()), workflow_version),edges


def project_graph(project, attention=None, running=False):
    records = {row["id"]: row for row in tasks_snapshot(project)["tasks"]}
    gate = current_gate(project)
    stage = project.get("stage")
    version=project.get("workflow_version",1)
    aliases = {"story_creation":"proposals","story_review":"proposals","script_creation":"screenplay",
               "storyboard_direction":"storyboard" if version==2 else "screenplay",
               "style_previews":"visual_development","story_resolution_review":"story_resolution" if version==2 else "screenplay","delivered":"delivery"}
    if version==1:
        aliases.update(script_review="screenplay",story_resolution="screenplay")
    active = aliases.get(stage, stage)
    if gate and project.get("status") not in {"blocked", "failed"} and not project.get("pause_requested"):
        active = gate["type"] + "_decision"
    if project.get("status") == "approved":
        active = "delivery"
    decisions = project.get("decisions")
    history = rows(decisions[-200:] if isinstance(decisions, list) else [], 200)
    gates = {kind: next((d for d in reversed(history) if d.get("type") == kind), {}) for kind in ("story", "style", "final")}
    nodes = []
    specification,edges=topology(version)
    for spec in specification:
        ident,label,owner,members,executable,inputs,outputs=(spec[k] for k in ("id","label","owner","members","execution_nodes","input","output"))
        status, evidence = "pending", "not_recorded"
        row = records.get(ident)
        if row and row["current"]:
            status, evidence = row["status"], "task_receipt"
        if ident.endswith("_decision"):
            kind = ident.split("_", 1)[0]; d = gates.get(kind, {})
            version = project.get({"story": "proposal_generation", "style": "style_generation", "final": "production_revision"}[kind],project.get("script_generation") if kind=="style" else None)
            if d.get("status") == "answered" and (version is None or d.get("id") == f"{project.get('id')}:{kind}:{version}"):
                status, evidence = "succeeded", "decision_receipt"
        if ident == "delivery" and project.get("status") == "approved":
            status, evidence = "succeeded", "producer_approval"
        if ident == 'unit_ready' and project.get('status') == 'unit_ready' and project.get('delivery'):
            status, evidence = 'succeeded', 'unit_delivery_receipt'
        if ident == active:
            if project.get("status") in {"blocked", "failed"}: status = "blocked"
            elif project.get("status") == "paused" or project.get("pause_requested"): status = "paused"
            elif attention and attention.get("kind") in {"story", "style", "final"}: status = "human"
            elif running: status = "running"
            elif project.get("status") == "approved": status = "succeeded"
            elif project.get("status") == "waiting_remote": status = "waiting"
            evidence = "current_project_state"
        if project.get("status") == "cancelled" and status != "succeeded": status = "cancelled"
        delegated = ident in {"story_decision", "style_decision"} and project.get("team_mode") == "delegated"
        if delegated: owner = "executive_producer"
        if project.get("workflow_version", 1) in {2, 3} and project.get("team_control_version") == 1:
            from .team_ledger import ROLE_DEPARTMENT
            assigned = obj(project.get("team_assignments")).get("stage:" + ident)
            if assigned in ROLE_DEPARTMENT:
                members = list(dict.fromkeys(assigned if member == owner else member for member in members))
                owner = assigned
        record = obj(obj(row).get("record"))
        nodes.append({**spec, "id": ident, "label": label, "owner": owner, "reviewer": spec.get("reviewer"), "members": members, "execution_nodes": executable,
                      "input": inputs, "output": outputs, "status": status, "evidence": evidence,
                      "run_id": record.get("run_id"),
                      "attempt": record.get("attempt"), "started_at": record.get("started_at"), "finished_at": record.get("finished_at"),
                      "decision_mode": "delegated" if delegated else "human" if ident.endswith("decision") else None})
    phase = obj(project.get("production")).get("phase") if stage == "production" else stage
    from .workflow_presentation import presentation
    return {"schema": "ai-studio.production-graph.v2", "workflow_version":project.get("workflow_version",1), "project_id": project.get("id"), "revision": project.get("revision"),
            "active_node": active, "current_activity": PHASES.get(phase, phase), "nodes": nodes,
            "edges": edges, "presentation": presentation(nodes, project.get("workflow_version", 1)),
            "production_tasks":list(obj(project.get("production_tasks")).values()),
            "groups": list(GROUPS), "semantics": "execution_nodes map to the existing LangGraph; no editable runtime wiring or inferred approvals"}
