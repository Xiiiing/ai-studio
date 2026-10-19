"""Producer console API, served at the original studio address."""
from __future__ import annotations

import re
import socket
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs,unquote,urlsplit

from .environment import load_environment
load_environment()

from studio.core import ROOT
from studio.http_security import HTTPError, validate_bind
from studio.web import Handler as LocalMediaHandler
from . import __version__, store
from .capabilities import snapshot
from .config import load_config
from .system_info import public_system_info


class Handler(LocalMediaHandler):
    server_version="DramaFactory/"+__version__

    factory_routes = True

    def json(self, value, status=200):
        def public(item):
            if isinstance(item, dict): return {k: public(v) for k, v in item.items() if k != 'execution_config'}
            if isinstance(item, list): return [public(v) for v in item]
            return item
        return super().json(public(value), status)

    def do_GET(self):
        path=unquote(urlsplit(self.path).path)
        try:
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/prompts(?:/(calls)(?:/([a-f0-9]{32}))?)?", path)
            if match:
                from . import prompt_workbench as prompts
                if urlsplit(self.path).query:
                    raise ValueError("提示词查询参数无效")
                return self.json(prompts.calls(match[1], match[3], root=ROOT) if match[2] else prompts.inventory(match[1]))
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/generation-pipeline", path)
            if match:
                from .generation_pipeline import preview
                if urlsplit(self.path).query:
                    raise ValueError("制作计划查询参数无效")
                return self.json(preview(store.get(match[1], False)))
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/agent-loop(?:/([a-f0-9]{64}))?", path)
            if match:
                from . import agent_loop
                if urlsplit(self.path).query:
                    raise ValueError("Agent查询参数无效")
                project = store.get(match[1], False)
                return self.json(agent_loop.detail(project,match[2]) if match[2] else agent_loop.snapshot(project))
            if path == "/api/factory/system":
                return self.json(public_system_info())
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/quality-baselines(?:/([a-f0-9]{32}|compare))?", path)
            if match:
                from . import quality_baselines
                pid, operation = match.groups()
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if operation == "compare":
                    if set(query) != {"baseline", "candidate"} or any(len(v) != 1 for v in query.values()):
                        raise ValueError("请选择基线版本和候选版本")
                    return self.json(quality_baselines.compare(pid, query["baseline"][0], query["candidate"][0]))
                if query:
                    raise ValueError("基线查询参数无效")
                return self.json(quality_baselines.detail(pid, operation) if operation else quality_baselines.inventory(pid))
            match = re.fullmatch(r'/api/factory/projects/([a-f0-9]{12})/handoffs(?:/([a-f0-9]{32}))?', path)
            if match:
                from . import role_handoffs
                from .team_flow import _project_root
                pid, ident = match.groups()
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if set(query) - ({'offset', 'limit'} if not ident else set()) or any(
                        len(v) != 1 or not re.fullmatch(r'[0-9]{1,9}', v[0]) for v in query.values()):
                    raise ValueError('交接分页无效')
                project = store.get(pid, False)
                folder = _project_root(project, root=ROOT) / 'planning'
                return self.json(role_handoffs.detail(folder, project, ident) if ident else
                    role_handoffs.snapshot(folder, project, **{k: int(v[0]) for k, v in query.items()}))
            if path == '/api/factory/dramas':
                from .dramas import list_dramas
                return self.json(list_dramas())
            match = re.fullmatch(r'/api/factory/dramas/([a-f0-9]{12})/episodes/([a-f0-9]{12})',path)
            if match:
                from .dramas import episode_snapshot
                return self.json(episode_snapshot(*match.groups()))
            match = re.fullmatch(r'/api/factory/dramas/([a-f0-9]{12})/download',path)
            if match:
                from .drama_exports import archive
                query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                if set(query)-{'batch_id'} or any(len(v)!=1 or not re.fullmatch(r'[a-f0-9]{12}',v[0]) for v in query.values()): raise ValueError('下载批次参数无效')
                return self.send_file(archive(match[1],query.get('batch_id',[None])[0]))
            match = re.fullmatch(r'/api/factory/dramas/([a-f0-9]{12})(?:/(episodes|batches))?', path)
            if match:
                from .dramas import snapshot as drama_snapshot
                result = drama_snapshot(match[1])
                return self.json({match[2]: result[match[2]], 'revision': result['revision']} if match[2] else result)
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/(team|tasks)(?:/([a-z_]+:[A-Za-z0-9_.-]{1,120}))?", path)
            if match:
                from .team_ledger import team_snapshot, task_page, task_detail
                pid, view, task_id = match.groups()
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                allowed = {"offset", "limit", "department", "status", "shot_id", "owner"} if view == "tasks" and not task_id else set()
                if set(query) - allowed or any(len(v) != 1 for v in query.values()) or (task_id and view != "tasks"):
                    raise ValueError("团队查询参数无效")
                if task_id:
                    return self.json(task_detail(pid, task_id))
                if view == "team":
                    return self.json(team_snapshot(pid))
                params = {k: v[0] for k, v in query.items()}
                for name in ("offset", "limit"):
                    if name in params:
                        if not re.fullmatch(r"[0-9]{1,9}", params[name]):
                            raise ValueError("任务分页无效")
                        params[name] = int(params[name])
                return self.json(task_page(pid, **params))
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/interventions", path)
            if match:
                from .team_control import instructions
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if set(query) - {"offset", "limit"} or any(len(v) != 1 or not re.fullmatch(r"[0-9]{1,9}", v[0]) for v in query.values()):
                    raise ValueError("指令分页无效")
                return self.json(instructions(match[1], **{k: int(v[0]) for k, v in query.items()}))
            if path == "/api/factory/library/tasks":
                from .material_tasks import tasks
                query = parse_qs(urlsplit(self.path).query)
                if set(query) - {"project_id"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError("素材任务查询参数无效")
                return self.json(tasks(query.get("project_id", [None])[0]))
            if path == "/api/factory/library" or re.fullmatch(r"/api/factory/library/assets/[A-Za-z][A-Za-z0-9_-]{0,63}", path):
                from . import material_library as materials
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if set(query) - {"project_id", "q", "kind", "offset", "version"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError("素材查询参数无效")
                params = {k: v[0] for k, v in query.items()}
                if path == "/api/factory/library":
                    return self.json(materials.inventory(params.get("project_id"), params.get("q", ""), params.get("kind") or None, int(params.get("offset", 0))))
                with store.connection() as db:
                    project = store._read(db, params["project_id"]) if params.get("project_id") else None
                    value = materials.accessible(materials.get(db, path.rsplit("/", 1)[1], int(params["version"]) if "version" in params else None), project)
                    return self.json(value)
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/materials", path)
            if match:
                from .material_library import snapshot as material_snapshot
                return self.json(material_snapshot(match[1]))
            if path=="/api/factory/production-profiles":
                from .production_profiles import catalog
                return self.json(catalog())
            if path=="/api/factory/workflow":
                from .workflow_spec import export_spec
                return self.json(export_spec())
            if path=="/api/factory/workflow-templates":
                from .workflow_templates import templates
                return self.json({"templates": templates()})
            match=re.fullmatch(r"/api/factory/workflow-templates/([a-f0-9]{64})",path)
            if match:
                from .workflow_templates import load_template
                return self.json(load_template(match[1]))
            if path=="/api/factory/models":
                from .providers import public_inventory
                return self.json(public_inventory())
            if path=="/api/health":
                return self.json({"service":"drama-studio","root":str(ROOT),"status":"ready",
                                  "version":__version__,"factory":True,"control_host":socket.gethostname()})
            if path in {"/api/factory/overview", "/api/factory/console"}:
                from .operator_console import dashboard
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                paging_keys = {"offset", "limit", "attention_offset", "attention_limit"}
                if set(query) - (paging_keys | {"q", "filter"}) or any(len(values) != 1 for values in query.values()):
                    raise ValueError("总览查询参数须为已支持的单个值")
                if any(not re.fullmatch(r"[0-9]{1,9}", values[0]) for key, values in query.items() if key in paging_keys):
                    raise ValueError("分页参数须为单个非负整数")
                params = {key: int(values[0]) if key in paging_keys else values[0] for key, values in query.items()}
                return self.json({**dashboard(**params),"capabilities":snapshot(),"version":__version__})
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/workspace",path)
            if match:
                from .operator_console import workspace
                return self.json(workspace(match.group(1)))
            if path=="/api/factory/capabilities":
                return self.json(snapshot())
            match=re.fullmatch(r"/api/factory/jobs/([a-f0-9]{32})",path)
            if match:
                from .job_api import snapshot as job_snapshot
                return self.json(job_snapshot(match.group(1)))
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/shots",path)
            if match:
                from .shot_workbench import snapshot as shot_snapshot
                return self.json(shot_snapshot(match.group(1)))
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/series",path)
            if match:
                from .series_bible import summary as series_summary
                return self.json(series_summary(match.group(1)))
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/cloud-budget",path)
            if match:
                from .cloud_budget import summary
                return self.json(summary(match.group(1)))
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/delivery-readiness",path)
            if match:
                from .delivery_readiness import snapshot as delivery_snapshot
                return self.json(delivery_snapshot(store.get(match.group(1),False)))
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/flow",path)
            if match:
                from .team_flow import build_flow
                return self.json(build_flow(store.get(match.group(1))))
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})",path)
            if match:
                return self.json(store.get(match.group(1)))
            if path=="/":
                index = ROOT / "ui/factory/dist/index.html"
                if not index.is_file():
                    return self.json({"error": "工作台尚未构建，请在开发环境运行 npm ci 和 npm run build，或安装包含已构建 UI 的发布包。", "code": "ui_build_missing"}, 503)
                return self.send_file(index, root=ROOT / "ui/factory/dist")
            if path.startswith("/factory/static/"):
                rel=path[len("/factory/static/"):]
                target=ROOT/"ui/factory/dist"/rel
                if target.suffix not in {".css",".js",".svg",".png",".jpg",".jpeg",".webp",".woff2",".txt"}:
                    return self.json({"error":"Static asset not found"},404)
                return self.send_file(target,root=ROOT/"ui/factory/dist")
            if path=="/legacy":
                return self.json({"error":"旧版已移除，请使用新版制片控制台。","code":"legacy_retired"},410)
            return super().do_GET()
        except HTTPError as exc:
            return self.json({"error":str(exc),"code":exc.code},exc.status)
        except KeyError as exc:
            return self.json({"error":str(exc)},404)
        except (ValueError,FileNotFoundError) as exc:
            return self.json({"error":str(exc)},400)
        except (BrokenPipeError,ConnectionResetError):
            pass
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return self.json({"error":"服务暂时无法完成请求，请检查本地日志。"},500)

    def do_POST(self):
        route=urlsplit(self.path).path
        if not route.startswith("/api/factory/"):
            return super().do_POST()
        try:
            payload=self.read_json_body()
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/agent-loop(?:/(settle))?", route)
            if match:
                from .agent_loop import configure, settle
                if urlsplit(self.path).query:
                    raise ValueError("Agent写入参数无效")
                return self.json((settle if match[2] else configure)(match[1], payload))
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/generation-pipeline(?:/(settle))?", route)
            if match:
                from .generation_pipeline import configure, settle
                if urlsplit(self.path).query:
                    raise ValueError("制作计划写入参数无效")
                return self.json((settle if match[2] else configure)(match[1], payload))
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/prompts/(save|preview|validate|activate|apply|trial|settle)", route)
            if match:
                from . import prompt_workbench as prompts
                if urlsplit(self.path).query:
                    raise HTTPError(400, "invalid_prompt_query", "提示词写入参数无效")
                pid, operation = match.groups()
                store.get(pid, False)
                if operation in {"save", "activate"}:
                    return self.json(getattr(prompts, operation)(payload))
                function = prompts.settle_trial if operation == "settle" else getattr(prompts, operation)
                return self.json(function(pid, payload))
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/quality-baselines", route)
            if match:
                from .quality_baselines import create
                if urlsplit(self.path).query:
                    raise ValueError("基线写入参数无效")
                return self.json(create(match[1], payload, root=ROOT), 201)
            if route == '/api/factory/dramas':
                from .dramas import create as create_drama
                return self.json(create_drama(payload), 201)
            match = re.fullmatch(r'/api/factory/dramas/([a-f0-9]{12})/(confirm|generate|actions)', route)
            if match:
                from .dramas import confirm, generate, control
                return self.json({'confirm': confirm, 'generate': generate, 'actions': control}[match[2]](match[1], payload), 202)
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/interventions(?:/(preview|[a-f0-9]{32}/dismiss))?", route)
            if match:
                from .team_control import submit, preview, dismiss
                pid, operation = match.groups()
                result = preview(pid, payload) if operation == "preview" else dismiss(pid, operation.split('/')[0], payload) if operation else submit(pid, payload)
                return self.json(result, 200 if operation == "preview" else 202)
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/tasks/([a-z_]+:[A-Za-z0-9_.-]{1,120})/actions", unquote(route))
            if match:
                from .team_control import task_action
                return self.json(task_action(*match.groups(), payload), 202)
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/team/actions", route)
            if match:
                from .team_control import enable
                return self.json(enable(match[1], payload), 202)
            if route == "/api/factory/library/tasks":
                from .material_tasks import create
                return self.json({"task": create(payload)}, 202)
            match = re.fullmatch(r"/api/factory/library/tasks/(mt_[a-f0-9]{32})", route)
            if match:
                from .material_tasks import control
                return self.json({"task": control(match[1], payload)}, 202)
            if route == "/api/factory/library":
                from .material_library import mutate
                return self.json({"asset": mutate(payload)}, 201)
            match = re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/materials", route)
            if match:
                from .material_library import mutate_project
                return self.json({"project": mutate_project(match[1], payload)}, 202)
            match=re.fullmatch(r"/api/factory/jobs/([a-f0-9]{32})/(pause|cancel)",route)
            if match:
                from .job_api import control
                result,status=control(*match.groups(),payload)
                return self.json(result,status)
            if route=="/api/factory/workflow-templates":
                from .workflow_templates import import_template
                return self.json(import_template(payload))
            if route=="/api/factory/models/discover":
                from .providers import discover_models
                if set(payload)-{'profile','api_key'} or 'profile' not in payload:
                    raise ValueError('请提供待检查连接')
                return self.json(discover_models(payload['profile'],payload.get('api_key')))
            if route=="/api/factory/models/test":
                from .providers import probe_profile
                if (set(payload) not in ({"profile_id"}, {"profile_id","kind"}) or
                        not isinstance(payload["profile_id"],str) or
                        payload.get("kind","text") not in ("text","video","image","audio")):
                    raise ValueError("请指定一个已保存的连接编号和连接类型")
                if payload.get("kind") in ("image", "audio"):
                    from .media_profiles import registry, probe
                    profiles,_=registry(load_config(),payload["kind"])
                    if payload["profile_id"] not in profiles: raise ValueError("未知制作连接")
                    return self.json(probe(profiles[payload["profile_id"]],payload["kind"]))
                if payload.get("kind")=="video":
                    from .video_providers import probe_video_profile
                    return self.json(probe_video_profile(payload["profile_id"],load_config()))
                return self.json(probe_profile(payload["profile_id"],timeout=8))
            if route=="/api/factory/models":
                from .providers import save_registry
                required={"profiles","roles","expected_revision"}
                optional={"video_profiles","video_roles","image_profiles","image_roles","audio_profiles","audio_roles","secrets"}
                if not required <= set(payload) or set(payload) - required - optional:
                    raise ValueError("保存需要完整连接、角色分配和当前配置版本")
                video={key:payload[key] for key in optional if key in payload}
                if ("video_profiles" in payload) != ("video_roles" in payload): raise ValueError("视频连接与分工必须一起保存")
                if "video_profiles" in payload:
                    if not isinstance(payload["video_profiles"],dict) or not isinstance(payload["video_roles"],dict):
                        raise ValueError("视频连接和分工应为对象")
                    video.update(video_profiles=payload["video_profiles"],video_roles=payload["video_roles"])
                return self.json(save_registry(payload["profiles"],payload["roles"],
                                               payload["expected_revision"],**video))
            if route=="/api/factory/projects":
                return self.json({"project":store.create(payload)},202)
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/shots/(S\d{2,3})",route)
            if match:
                from .shot_edits import mutate as mutate_shot
                return self.json({"project":mutate_shot(*match.groups(),payload)},202)
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/series",route)
            if match:
                from .series_bible import mutate as series_mutate
                return self.json({"result":series_mutate(match.group(1),payload)},202)
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/(cloud-authorize|cloud-confirm-cost)",route)
            if match:
                from .cloud_budget import authorize, confirm_cost
                from .video_providers import resolve_video
                pid, operation = match.groups()
                if operation == "cloud-authorize":
                    from . import cloud_budget
                    with store.connection() as db:
                        batch=cloud_budget._batch(db,payload.get("batch_id"))
                    kind=batch["scope"].get("media_kind")
                    if kind:
                        from .media_profiles import resolve
                        profile={**resolve(load_config(),kind),"_media_kind":kind}
                    else: profile=resolve_video(load_config())
                    result=authorize(pid,payload,profile)
                else: result=confirm_cost(pid,payload)
                return self.json({"project":result} if operation == "cloud-authorize" else result,202)
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/(decision|action)",route)
            if match:
                pid,action=match.groups()
                result=store.decide(pid,payload) if action=="decision" else store.action(pid,payload)
                return self.json({"project":result},202)
            return self.json({"error":"Not found"},404)
        except HTTPError as exc:
            return self.json({"error":str(exc),"code":exc.code},exc.status)
        except KeyError as exc:
            return self.json({"error":str(exc)},404)
        except (ValueError,TypeError,RuntimeError) as exc:
            return self.json({"error":str(exc)},409)
        except (BrokenPipeError,ConnectionResetError):
            pass
        except Exception:
            import traceback
            traceback.print_exc()
            return self.json({"error":"服务暂时无法完成请求，请检查本地日志。"},500)


def main():
    config=load_config()["web"]
    host,port=validate_bind(config["host"],config["port"])
    store.initialize()
    class LocalServer(ThreadingHTTPServer):
        address_family=socket.AF_INET6 if ":" in host else socket.AF_INET
    server=LocalServer((host,port),Handler)
    server.daemon_threads=True
    authority=f"[{host}]" if ":" in host else host
    print(f"DRAMA_FACTORY_READY http://{authority}:{server.server_port}",flush=True)
    server.serve_forever()


if __name__=="__main__":
    main()
