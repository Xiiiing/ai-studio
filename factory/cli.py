"""Local operator CLI. All work uses the existing store, queue, and worker."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

from studio.core import ROOT
from . import store

STATUS = {
    "queued": "已入后台队列", "researching": "选题研究", "creating": "创作提案",
    "scripting": "编写剧本", "visual_development": "生成风格预览",
    "producing": "制作中", "reviewing": "质检中", "revising": "返修中",
    "awaiting_story": "等待选择故事", "awaiting_style": "等待选择画风",
    "awaiting_final": "等待终审", "paused": "已暂停", "blocked": "需要处理阻断",
    "failed": "执行失败", "approved": "终审流程已完成", "cancelled": "已取消",
}


def _text_options(parser, name, required=False):
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--" + name)
    group.add_argument("--" + name + "-file", help="读取 UTF-8 文本文件")


def _shots(value):
    parts = list(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))
    if not parts:
        raise argparse.ArgumentTypeError("请提供明确镜号，例如 S03,S05")
    return parts


def parser():
    result = argparse.ArgumentParser(description="AI 短剧制片：复用现有后台执行器，无需浏览器")
    sub = result.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", aliases=["run"], help="提交新项目到现有后台队列")
    _text_options(create, "brief", required=True)
    create.add_argument("--title", default="")
    create.add_argument("--format", choices=["motion_comic", "live_action", "animation"], default="motion_comic")
    create.add_argument("--seconds", type=int, default=60)
    create.add_argument("--gpu-minutes", type=float, default=120)
    create.add_argument("--wall-minutes", type=float, default=180)
    create.add_argument("--max-attempts", type=int, default=3)
    create.add_argument("--request-id", help="同一创建请求重试时沿用；同编号不能改变参数")
    create.add_argument("--validation", action="store_true")
    create.add_argument("--json", action="store_true")
    for command in ["status", "pause", "resume", "revise", "advance"]:
        item = sub.add_parser(command, help={
            "status": "查看状态、待办、预算和版本",
            "pause": "请求在可恢复边界暂停",
            "resume": "从既有检查点继续；预算耗尽时需提高相应总上限",
            "revise": "制作阻断后按明确镜号返修",
            "advance": "仅离线排障：持有同一个 worker.lock 才能执行",
        }[command])
        item.add_argument("project")
        item.add_argument("--json", action="store_true")
        if command in {"resume", "revise"}:
            _text_options(item, "feedback", required=command == "revise")
        if command == "resume":
            item.add_argument("--gpu-minutes", type=float)
            item.add_argument("--wall-minutes", type=float)
        if command == "revise":
            item.add_argument("--shots", "--shot-ids", dest="shot_ids", type=_shots, required=True)
            item.add_argument("--expected-revision", type=int, required=True,
                              help="status 中的项目 revision，不是 production_revision")
    choose = sub.add_parser("choose", help="提交故事、画风或终审的现有决策")
    choose.add_argument("project")
    choose.add_argument("--type", choices=["story", "style", "final"], required=True)
    choose.add_argument("--choice", required=True)
    choose.add_argument("--decision-id", help="使用 status 中的编号，防止选中更新后的待办；也支持幂等重试")
    _text_options(choose, "feedback")
    act = sub.add_parser("action", help="兼容入口：暂停、继续、重试评审或重新提案")
    act.add_argument("project")
    act.add_argument("action", choices=["pause", "resume", "retry", "repropose"])
    act.add_argument("--gpu-minutes", type=float)
    act.add_argument("--wall-minutes", type=float)
    act.add_argument("--shots", "--shot-ids", dest="shot_ids", type=_shots)
    _text_options(act, "feedback")
    sub.add_parser("list", help="以 JSON 列出现有项目")
    roles = sub.add_parser("roles", help="读取有效角色路由；不探测模型服务")
    roles.add_argument("--json", action="store_true")
    models = sub.add_parser("models", help="模型连接目录、只读探测、配置导入导出")
    models.add_argument("operation", nargs="?", choices=["list", "probe", "export", "apply"], default="list")
    models.add_argument("profile", nargs="?", help="probe 的配置档 ID")
    models.add_argument("--file", help="导出或导入的 UTF-8 JSON 文件")
    models.add_argument("--expected-revision", help="apply 的 CAS 配置版本；省略则使用导出文件内的 revision")
    models.add_argument("--json", action="store_true")
    workflow = sub.add_parser("workflow", help="导出实际执行图、角色与制作接口说明")
    workflow.add_argument("--json", action="store_true")
    outputs = sub.add_parser("outputs", aliases=["export"], help="列出当前交付版本已存在的本地文件")
    outputs.add_argument("project")
    outputs.add_argument("--json", action="store_true")
    return result


def _text(args, name):
    filename = getattr(args, name + "_file", None)
    value = Path(filename).read_text(encoding="utf-8") if filename else getattr(args, name, None) or ""
    value = value.strip()
    limit = 6000 if name == "brief" else 4000
    if len(value) > limit:
        raise ValueError(f"{name} 最多 {limit} 字，请精简说明")
    return value


def _media_path(reference, project):
    if isinstance(reference, dict):
        reference = reference.get("path") or reference.get("url") or reference.get("download_url")
    if not isinstance(reference, str) or not reference:
        raise ValueError("没有本地文件路径")
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        raise ValueError("仅列出此服务器上的本地制作文件")
    raw = unquote(parsed.path)
    if raw.startswith("/media/"):
        raw = raw[len("/media/"):]
    path = Path(raw)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    allowed = (ROOT / "production" / project["slug"]).resolve()
    if not path.is_relative_to(allowed):
        raise ValueError("文件不属于当前项目")
    return path


def collect_outputs(project):
    """Read receipts and stat existing files; never render, package, or approve."""
    delivery = project.get("delivery") or {}
    raw = []
    for key in ("artifacts", "files"):
        value = delivery.get(key) or []
        raw.extend(value if isinstance(value, list) else [value])
    for key in ("path", "video_url", "clean_url", "subtitle_url", "cover_url", "timeline_url", "manifest_url"):
        if delivery.get(key):
            raw.append({"path": delivery[key]})
    seen, files, unavailable = set(), [], []
    for receipt in raw:
        receipt = {"path": receipt} if isinstance(receipt, str) else receipt
        if not isinstance(receipt, dict):
            continue
        try:
            path = _media_path(receipt, project)
            if str(path) in seen:
                continue
            seen.add(str(path))
            if not path.is_file():
                raise ValueError("记录中的文件当前不存在")
            item = {"name": receipt.get("name") or path.name, "path": str(path), "bytes": path.stat().st_size}
            if receipt.get("sha256"):
                item["recorded_sha256"] = receipt["sha256"]
            files.append(item)
        except (ValueError, OSError) as error:
            unavailable.append({"path": receipt.get("path") or receipt.get("url"), "reason": str(error)})
    return {"project_id": project["id"], "status": project["status"],
            "production_revision": project.get("production_revision"), "files": files,
            "unavailable": unavailable}


def _number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def print_status(project):
    print(f"{project.get('title') or '未命名项目'}  [{project['id']}]")
    print(f"状态：{STATUS.get(project['status'], project['status'])} ({project['status']})"
          f"  阶段：{project.get('stage', '—')}  进度：{project.get('progress', 0)}%")
    print(f"项目 revision：{project.get('revision')}  剧本代次：{project.get('script_generation')}"
          f"  制作版本：{project.get('production_revision')}")
    budget, usage = project.get("budget") or {}, project.get("production") or {}
    gpu = _number(usage.get("gpu_seconds")) / 60 if "gpu_seconds" in usage else _number(budget.get("spent_gpu_minutes"))
    wall = _number(usage.get("elapsed_seconds")) / 60
    print(f"GPU：{gpu:.2f}/{budget.get('gpu_minutes', '—')} 卡分钟"
          f"  制作计时：{wall:.2f}/{budget.get('wall_minutes', '—')} 分钟"
          f"  每个素材版本最多尝试：{budget.get('max_attempts', '—')}")
    shots = project.get("shots") or []
    if shots:
        print("镜头：" + "，".join(f"{key} {count}" for key, count in Counter(s.get("status", "planned") for s in shots).items()))
        failed = [s["id"] for s in shots if s.get("status") in {"failed", "rejected", "error"}]
        if failed:
            print("当前失败镜头：" + ", ".join(failed))
    need = project.get("needs_decision") or {}
    if need:
        print(f"阻断 [{need.get('type', 'unknown')}]：{need.get('message') or need.get('reason') or project.get('error') or '请查看 --json 中的评审明细'}")
        if need.get("type") == "budget":
            print("耗尽分量：" + ", ".join(need.get("exhausted_limits") or ["gpu"]) + "；resume 需提供相应的新总上限。")
        if need.get("type") == "production":
            print("需要改分镜时：revise + --shots + --expected-revision + --feedback；普通 resume 不改变分镜。")
    elif project.get("error"):
        print("错误：" + str(project["error"]))
    for gate in (d for d in project.get("decisions", []) if d.get("status") == "pending"):
        print(f"待办 {gate['type']}：{gate['id']}  {gate.get('title', '')}")
        collection = project.get("proposals" if gate["type"] == "story" else "styles", [])
        for option in gate.get("options", []):
            option = option if isinstance(option, dict) else {"id": option}
            details = next((v for v in collection if v.get("id") == option["id"]), {})
            label = option.get("label") or details.get("title") or details.get("name") or option["id"]
            print(f"  {option['id']}  {label}")
            for key in ("logline", "synopsis", "description"):
                if details.get(key):
                    print("    " + str(details[key]))
            if details.get("preview"):
                try:
                    preview = _media_path(details["preview"], project)
                    print(f"    预览：{preview}" if preview.is_file() else "    预览文件当前不存在")
                except (ValueError, OSError) as error:
                    print(f"    预览不可用：{error}")
        print(f"选择时可附 --decision-id {gate['id']}，绑定当前待办。")
    if project.get("delivery"):
        print("当前成片与工程路径：outputs " + project["id"])
        if project["status"] == "awaiting_final":
            print("当前版本尚待终审；文件生成不代表已获批准。")
    if project.get("pause_requested") and project["status"] != "paused":
        print("暂停请求已记录，正在等待当前执行单元的可恢复边界。")


def _advance(project_id):
    import fcntl
    from .config import DATA
    from .worker import handle
    with (DATA / "worker.lock").open("a+") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("后台 worker 已在线；请使用 choose/resume 等命令提交，由它继续执行。advance 仅供离线排障。")
        job = store.claim_job(project_id)
        if not job:
            raise RuntimeError("该项目没有可执行任务；请先完成待办决策或明确恢复暂停的任务")
        handle(job)
        return store.get(project_id)


def execute(args):
    command = args.command
    if command == "roles":
        from .config import list_roles
        return list_roles()
    if command == "models":
        from .providers import public_inventory, probe_profile, save_registry
        if args.operation == "probe":
            if not args.profile:
                raise ValueError("probe 需要明确的配置档 ID")
            return probe_profile(args.profile)
        inventory = public_inventory()
        if args.operation == "list":
            return inventory
        if args.operation == "export":
            value = {"schema_version": 1, "revision": inventory["revision"],
                     "llm_profiles": inventory["editable_profiles"],
                     "llm_roles": inventory["role_assignments"]}
            if args.file:
                from studio.core import write_json
                target = Path(args.file).expanduser().resolve()
                if target.exists():
                    raise ValueError("导出目标已存在，请使用新的文件名")
                write_json(target, value)
                return {"path": str(target), "revision": inventory["revision"],
                        "message": "已导出连接配置与角色绑定；仅含密钥环境变量名"}
            return value
        if not args.file:
            raise ValueError("apply 需要 --file")
        path = Path(args.file)
        if path.stat().st_size > 65536:
            raise ValueError("模型配置文件不能超过 64 KB")
        value = json.loads(path.read_text(encoding="utf-8"),
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("非有限数值")))
        if not isinstance(value, dict):
            raise ValueError("模型配置应是 JSON 对象")
        revision = args.expected_revision or value.get("revision")
        if not revision:
            raise ValueError("请先 models export，或提供 --expected-revision")
        return save_registry(value.get("llm_profiles"), value.get("llm_roles"), revision)
    if command == "workflow":
        from .workflow_spec import export_spec
        return export_spec()
    store.initialize()
    if command in {"create", "run"}:
        values = {"brief": _text(args, "brief"), "title": args.title, "format": args.format,
                  "duration_seconds": args.seconds, "is_validation": args.validation,
                  "budget": {"gpu_minutes": args.gpu_minutes, "wall_minutes": args.wall_minutes,
                             "max_attempts": args.max_attempts}}
        if args.request_id:
            values["request_id"] = args.request_id
        return store.create(values)
    if command == "list":
        return store.list_projects()
    if command == "status":
        return store.get(args.project)
    if command in {"outputs", "export"}:
        return collect_outputs(store.get(args.project, False))
    if command == "choose":
        decision_id = args.decision_id
        if not decision_id:
            project = store.get(args.project, False)
            gate = next((d for d in reversed(project["decisions"])
                         if d["type"] == args.type and d["status"] == "pending"), None)
            if not gate:
                raise ValueError("没有对应的待办决策；请运行 status 检查")
            decision_id = gate["id"]
        return store.decide(args.project, {"type": args.type, "choice": args.choice,
                           "feedback": _text(args, "feedback"), "decision_id": decision_id})
    if command == "advance":
        return _advance(args.project)
    values = {"action": args.action if command == "action" else command, "feedback": _text(args, "feedback")}
    for key in ("gpu_minutes", "wall_minutes", "shot_ids", "expected_revision"):
        value = getattr(args, key, None)
        if value is not None:
            values[key] = value
    return store.action(args.project, values)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        if getattr(args, "json", False) or args.command in {"create", "list", "choose", "action", "advance", "models", "workflow"}:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "roles":
            print("角色 / 配置档 / 模型 / 端点 / 视觉输入")
            for role in result:
                print(f"{role['role']} ({role['label']}) / {role['profile']} / {role['model']} / {role['endpoint']}"
                      f" / {'需要' if role['requires_vision'] else '可用' if role['vision'] else '文本'}")
            print("这是有效配置，未进行在线健康探测；多个角色可以共享同一权重。总调度与预算由 LangGraph/worker 代码负责。")
        elif args.command in {"outputs", "export"}:
            print(f"项目 {result['project_id']} / {result['status']} / 制作版本 {result['production_revision']}")
            for item in result["files"]:
                print(f"{item['path']}  ({item['bytes']} bytes)")
                if item.get("recorded_sha256"):
                    print("  制作回执 SHA256：" + item["recorded_sha256"])
            if not result["files"]:
                print("当前没有可列出的交付文件；此命令不会启动制作或重新剪辑。")
            for item in result["unavailable"]:
                print(f"未列入：{item['path']} — {item['reason']}")
        else:
            print_status(result)
        return 0
    except (ValueError, KeyError, RuntimeError, OSError, sqlite3.Error) as error:
        print("错误：" + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

