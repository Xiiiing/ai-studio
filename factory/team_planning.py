"""Bounded manager suggestions; the controller alone applies validated operations."""
from __future__ import annotations

import copy

from jsonschema import Draft202012Validator

from . import config as configuration, store, team_ledger as ledger
from .execution_context import current_execution
from .prompt_registry import load_prompt
from .schemas import cached_json_call


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


_TEXT = {"type": "string", "minLength": 1, "maxLength": 1500}
_TASK_IDS = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100, "uniqueItems": True}
OPERATION_SCHEMA = _object({
    "action": {"type": "string", "enum": ["prioritize", "pause", "resume", "assign", "retry", "revise"]},
    "task_id": {"type": "string"}, "priority": {"type": "integer", "minimum": -100, "maximum": 100},
    "owner": {"type": "string", "maxLength": 64}, "requirements": {"type": "string", "maxLength": 4000},
})
ROOT_PLAN_SCHEMA = _object({"summary": _TEXT, "delegations": {
    "type": "array", "minItems": 1, "maxItems": 7,
    "items": _object({"department": {"type": "string"}, "task_ids": _TASK_IDS,
                      "requirements": {"type": "string", "minLength": 1, "maxLength": 4000}}),
}})
DEPARTMENT_PLAN_SCHEMA = _object({"summary": _TEXT, "operations": {
    "type": "array", "minItems": 1, "maxItems": 200, "items": OPERATION_SCHEMA,
}})

STORY_CREATORS = ("creator", "story_editor", "screenwriter")
TEXT_REVIEWERS = ("independent_critic", "story_critic")


def allowed_owners(task):
    """Capabilities belong to existing roles, independently from model brands."""
    kind, scope = task.get("kind"), task.get("scope")
    if kind == "stage":
        if scope in {"proposals", "story_resolution", "screenplay"}:
            return list(STORY_CREATORS)
        if scope == "script_review":
            return list(TEXT_REVIEWERS)
        from .team_tasks import V2_TASKS
        if scope in V2_TASKS:
            return [V2_TASKS[scope]["owner"]]
    if kind in {"script", "dialogue_text"}:
        return [task["owner"]]
    if kind in ledger.KINDS:
        return [task.get("owner") if kind == "management" else ledger.KINDS[kind][1]]
    return []


def leader_availability(role, config=None):
    config = configuration.load_config() if config is None else config
    try:
        if role not in {group["lead"] for group in ledger.DEPARTMENTS}:
            raise ValueError("未知部门负责人")
        if role not in config.get("llm_roles", {}):
            raise ValueError("请先为负责人配置明确的模型绑定：" + role)
        resolved = configuration.resolve_llm(config, role)
        if resolved.get("enabled") is False:
            raise ValueError("该负责人的模型连接已停用：" + role)
        return {"available": True, "reason": None, "model": resolved.get("model"), "profile_id": resolved["profile"]}
    except ValueError as exc:
        return {"available": False, "reason": str(exc), "model": None, "profile_id": None}


def _check_shape(value, schema):
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    if error is not None:
        field = ".".join(str(part) for part in error.absolute_path)[:160] or "$"
        raise ValueError("负责人返回的安排格式无效（" + field + " / " + str(error.validator)[:40] + "）；原要求和成果已保留")


def _selected(command, tasks):
    ids = command.get("task_ids")
    if not isinstance(ids, list) or not ids or len(ids) > 100 or len(set(ids)) != len(ids) or not set(ids) <= tasks.keys():
        raise ValueError("安排只能使用明确选定的现有任务，不能创建新节点")
    if any(tasks[ident]["kind"] == "management" for ident in ids):
        raise ValueError("制作安排不能修改管理执行记录")
    return ids


def _manager(command, tasks):
    ids = _selected(command, tasks)
    target_type, target = command.get("target_type"), command.get("target_id")
    if target_type == "leader":
        group = next((g for g in ledger.DEPARTMENTS if g["lead"] == target), None)
    elif target_type == "department":
        group = next((g for g in ledger.DEPARTMENTS if g["id"] == target), None)
    else:
        departments = {tasks[ident]["department"] for ident in ids}
        group = next((g for g in ledger.DEPARTMENTS if g["id"] == next(iter(departments))), None) if len(departments) == 1 else ledger.DEPARTMENTS[0]
    if group is None:
        raise ValueError("未知部门或负责人")
    if group["id"] != "direction" and any(tasks[ident]["department"] != group["id"] for ident in ids):
        raise ValueError("部门负责人不能安排其他部门的任务")
    return group


def _projection(task):
    fields = ("id", "kind", "scope", "label", "generation", "owner", "department", "status", "priority",
              "input_version", "requirements", "review_status", "adoption_status", "hold_after", "paused")
    result = {key: copy.deepcopy(task.get(key)) for key in fields}
    result["requirements"] = str(task.get("requirements", ""))[:300]
    result["label"] = str(task.get("label", ""))[:100]
    version = result.get("input_version")
    if isinstance(version, str):
        result["input_version"] = version[:128]
    dependencies = task.get("dependencies", [])
    result.update(dependencies=copy.deepcopy(dependencies[:12]), dependency_count=len(dependencies),
                  allowed_owners=allowed_owners(task))
    return result


def _call(project, command, tasks, role, ids, requirements, schema, config):
    availability = leader_availability(role, config)
    if not availability["available"]:
        raise ValueError(availability["reason"])
    payload = {"project": {"id": project["id"], "title": str(project.get("title", ""))[:100],
                           "brief": str(project.get("brief", ""))[:1000], "format": project.get("format")},
               "directive": {"instruction_id": command["id"], "requirements": requirements,
                             "preserve": command.get("preserved_tasks", command.get("preserve", [])), "after": command.get("after", "auto")},
               "tasks": [_projection(tasks[ident]) for ident in ids],
               "constraints": {"selected_task_ids": ids, "can_create_tasks": False, "can_authorize_fees": False,
                               "can_change_budget": False, "can_approve_final": False, "can_change_review_verdict": False}}
    if len(store.encoded(payload).encode("utf-8")) > 96 * 1024:
        raise ValueError("所选任务的管理上下文过大，请按部门或镜头分组提交；原要求已保留")
    scope = command["id"] + "." + role
    with ledger.unit(project["id"], "management", scope, owner=role, input_version=ledger.fingerprint(payload), inputs=payload) as token:
        context = current_execution.set({"project_id": project["id"], "stage": "team_management",
                                         "task_id": "management:" + scope, "run_id": token["run_id"] if token else None})
        try:
            value = cached_json_call(project, config, role, "team-management-" + role,
                                     load_prompt(role, "2.0.0"), payload, schema, temperature=.2)
            _check_shape(value, schema)
            return value, "management:" + scope
        finally:
            current_execution.reset(context)


def _validate_delegations(command, tasks, value):
    _check_shape(value, ROOT_PLAN_SCHEMA)
    selected, seen, groups = set(_selected(command, tasks)), set(), set()
    departments = {g["id"]: g for g in ledger.DEPARTMENTS if g["id"] != "direction"}
    for delegation in value["delegations"]:
        department = delegation["department"]
        ids = set(delegation["task_ids"])
        if department not in departments or department in groups or not ids <= selected or ids & seen:
            raise ValueError("总制片派单超出明确任务范围、重复或使用了未知部门")
        if any(tasks[ident]["department"] != department for ident in ids):
            raise ValueError("总制片必须将任务交给其所属部门负责人")
        groups.add(department)
        seen.update(ids)
    if seen != selected:
        raise ValueError("总制片派单必须完整覆盖明确选择的任务")


def validate_plan(project, command, tasks, plan, config=None):
    """Rechecked inside the applying transaction even when model I/O is mocked."""
    config = configuration.load_config() if config is None else config
    if not isinstance(plan, dict) or set(plan) - {"summary", "operations", "delegations", "management_tasks"}:
        raise ValueError("安排包含未授权的字段，不能修改预算、费用或终片权限")
    _check_shape({"summary": plan.get("summary"), "operations": plan.get("operations")}, DEPARTMENT_PLAN_SCHEMA)
    selected = set(_selected(command, tasks))
    manager = _manager(command, tasks)
    seen = {}
    for operation in plan["operations"]:
        ident, action = operation["task_id"], operation["action"]
        if ident not in selected:
            raise ValueError("负责人只能安排出品人明确选定的任务")
        if manager["id"] != "direction" and tasks[ident]["department"] != manager["id"]:
            raise ValueError("部门负责人不能安排其他部门的任务")
        previous = seen.setdefault(ident, set())
        if action in previous or ({action} | previous >= {"pause", "resume"}) or ({action} | previous >= {"retry", "revise"}):
            raise ValueError("同一任务的安排重复或相互矛盾")
        previous.add(action)
        if action != "prioritize" and operation["priority"] != 0:
            raise ValueError("只有优先级操作能够设置 priority")
        if action != "assign" and operation["owner"]:
            raise ValueError("只有转派操作能够设置负责人")
        if action not in {"retry", "revise"} and operation["requirements"]:
            raise ValueError("修改要求只能用于明确重做或修改任务")
        if action in {"retry", "revise"} and not operation["requirements"].strip():
            raise ValueError("重做或修改任务必须提供明确要求")
        if action == "assign":
            owner = operation["owner"]
            eligible = allowed_owners(tasks[ident])
            if owner not in eligible:
                raise ValueError("没有该成员可承接的能力匹配任务；可用成员：" + "、".join(eligible))
            if owner != tasks[ident]["owner"]:
                if owner not in config.get("llm_roles", {}):
                    raise ValueError("转派成员尚未配置明确的模型绑定：" + owner)
                resolved = configuration.resolve_llm(config, owner)
                if resolved.get("enabled") is False:
                    raise ValueError("转派成员的模型连接已停用")
            if tasks[ident]["status"] == "running":
                raise ValueError("当前任务尚未收尾，不能改变正在执行的成员身份")
    requested = command.get("operation")
    if requested in {"retry", "revise"} and any(requested not in seen.get(ident, set()) for ident in selected):
        raise ValueError("负责人必须落实每个明确选定任务的本次" + ("重做" if requested == "retry" else "修改") + "要求，不能仅调整队列")
    if "delegations" in plan or "management_tasks" in plan:
        delegations, management = plan.get("delegations"), plan.get("management_tasks")
        if not isinstance(delegations, list) or not isinstance(management, list) or any(not isinstance(ident, str) for ident in management):
            raise ValueError("管理执行或交接记录无效")
        roles = [manager["lead"]]
        covered, departments = set(), set()
        for delegation in delegations:
            _check_shape(delegation, _object({"department": {"type": "string"}, "task_ids": _TASK_IDS, "summary": _TEXT}))
            group = next((g for g in ledger.DEPARTMENTS if g["id"] == delegation["department"] and g["id"] != "direction"), None)
            ids = set(delegation["task_ids"])
            if manager["id"] != "direction" or group is None or group["id"] in departments or not ids <= selected or ids & covered:
                raise ValueError("管理交接范围重复、越界或使用了未知部门")
            if any(tasks[ident]["department"] != group["id"] for ident in ids):
                raise ValueError("管理交接的任务不属于对应部门")
            departments.add(group["id"])
            covered.update(ids)
            roles.append(group["lead"])
        if manager["id"] == "direction" and covered != selected:
            raise ValueError("总制片交接未覆盖明确选择的任务")
        expected = {"management:" + command["id"] + "." + role for role in roles}
        if len(set(management)) != len(management) or set(management) != expected:
            raise ValueError("管理执行记录不对应本次真实负责人调用")
        with store.connection() as db:
            for role in roles:
                ident = "management:" + command["id"] + "." + role
                row = db.execute("SELECT kind,owner,status FROM team_work_items WHERE project_id=? AND id=?", (project["id"], ident)).fetchone()
                attempt = db.execute("SELECT 1 FROM team_unit_attempts WHERE project_id=? AND task_id=?", (project["id"], ident)).fetchone()
                if row is None or row["kind"] != "management" or row["owner"] != role or row["status"] not in {"completed", "reused"} or not attempt:
                    raise ValueError("管理执行记录缺少真实完成依据")


def plan_instruction(project, command, tasks, config=None):
    config = configuration.load_config() if config is None else copy.deepcopy(config)
    group = _manager(command, tasks)
    ids = _selected(command, tasks)
    management, delegations, operations = [], [], []
    if group["id"] == "direction":
        root, task_id = _call(project, command, tasks, group["lead"], ids, command["instruction"], ROOT_PLAN_SCHEMA, config)
        management.append(task_id)
        _validate_delegations(command, tasks, root)
        for delegation in root["delegations"]:
            department = next(g for g in ledger.DEPARTMENTS if g["id"] == delegation["department"])
            part, task_id = _call(project, command, tasks, department["lead"], delegation["task_ids"], delegation["requirements"], DEPARTMENT_PLAN_SCHEMA, config)
            narrowed = {**command, "target_type": "department", "target_id": department["id"], "task_ids": delegation["task_ids"]}
            validate_plan(project, narrowed, tasks, part, config)
            operations.extend(part["operations"])
            management.append(task_id)
            delegations.append({"department": department["id"], "task_ids": delegation["task_ids"], "summary": part["summary"]})
        summary = root["summary"]
    else:
        part, task_id = _call(project, command, tasks, group["lead"], ids, command["instruction"], DEPARTMENT_PLAN_SCHEMA, config)
        management.append(task_id)
        operations, summary = part["operations"], part["summary"]
    plan = {"summary": summary, "operations": operations, "delegations": delegations, "management_tasks": management}
    validate_plan(project, command, tasks, plan, config)
    return plan


def task_config(config, role):
    """Only the executing stage actor is remapped; reviewers retain their route."""
    context = current_execution.get() or {}
    pid, stage = context.get("project_id"), context.get("stage")
    from .team_tasks import V2_TASKS
    if stage not in V2_TASKS or role != V2_TASKS[stage]["owner"] or not ledger._existing(pid):
        return config
    with store.connection() as db:
        project = store._read(db, pid)
        if not ledger.enabled(project):
            return config
        row = db.execute("SELECT * FROM team_work_items WHERE project_id=? AND id=?", (pid, "stage:" + stage)).fetchone()
        if not row:
            return config
        task = ledger._data(dict(row))
    owner = task["owner"]
    if owner == role:
        return config
    if owner not in allowed_owners(task) or owner not in config.get("llm_roles", {}):
        raise ValueError("任务成员缺少能力匹配的明确模型绑定")
    configuration.resolve_llm(config, owner)
    routed = copy.deepcopy(config)
    routed.setdefault("llm_roles", {})[role] = config["llm_roles"][owner]
    routed.setdefault("_team_actual_members", {})[role] = owner
    configuration.resolve_llm(routed, role)
    return routed
