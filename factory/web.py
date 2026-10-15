"""Producer console API, served at the original studio address."""
from __future__ import annotations

import re
import socket
from http.server import ThreadingHTTPServer
from urllib.parse import unquote,urlsplit

from studio.core import ROOT
from studio.http_security import HTTPError, validate_bind
from studio.web import Handler as LegacyHandler
from . import __version__, store
from .capabilities import snapshot
from .config import load_config


class Handler(LegacyHandler):
    server_version="DramaFactory/"+__version__

    factory_routes = True

    def do_GET(self):
        path=unquote(urlsplit(self.path).path)
        try:
            if path=="/api/factory/production-profiles":
                from .production_profiles import catalog
                return self.json(catalog())
            if path=="/api/factory/workflow":
                from .workflow_spec import export_spec
                return self.json(export_spec())
            if path=="/api/factory/models":
                from .providers import public_inventory
                return self.json(public_inventory())
            if path=="/api/health":
                return self.json({"service":"drama-studio","root":str(ROOT),"status":"ready",
                                  "version":__version__,"factory":True})
            if path=="/api/factory/overview":
                return self.json({"projects":store.list_projects(),"capabilities":snapshot(),
                                  "queue":store.queue_summary(),"version":__version__})
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
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/flow",path)
            if match:
                from .team_flow import build_flow
                return self.json(build_flow(store.get(match.group(1))))
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})",path)
            if match:
                return self.json(store.get(match.group(1)))
            if path=="/":
                return self._static(ROOT/"ui/factory/index.html","text/html; charset=utf-8")
            if path.startswith("/factory/static/"):
                rel=path[len("/factory/static/"):]
                target=ROOT/"ui/factory"/rel
                if target.suffix not in {".css",".js",".svg",".png",".jpg",".woff2"}:
                    return self.json({"error":"Static asset not found"},404)
                return self.send_file(target,root=ROOT/"ui/factory")
            if path=="/legacy":
                previous=self.path
                self.path="/"
                try:
                    return super().do_GET()
                finally:
                    self.path=previous
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

    def _static(self,path,mime):
        body=path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        route=urlsplit(self.path).path
        if not route.startswith("/api/factory/"):
            return super().do_POST()
        try:
            payload=self.read_json_body()
            match=re.fullmatch(r"/api/factory/jobs/([a-f0-9]{32})/(pause|cancel)",route)
            if match:
                from .job_api import control
                result,status=control(*match.groups(),payload)
                return self.json(result,status)
            if route=="/api/factory/models/test":
                from .providers import probe_profile
                if (set(payload) not in ({"profile_id"}, {"profile_id","kind"}) or
                        not isinstance(payload["profile_id"],str) or
                        payload.get("kind","text") not in ("text","video")):
                    raise ValueError("请指定一个已保存的连接编号和连接类型")
                if payload.get("kind")=="video":
                    from .video_providers import probe_video_profile
                    return self.json(probe_video_profile(payload["profile_id"],load_config()))
                return self.json(probe_profile(payload["profile_id"],timeout=8))
            if route=="/api/factory/models":
                from .providers import save_registry
                required={"profiles","roles","expected_revision"}
                if set(payload) not in (required, required | {"video_profiles","video_roles"}):
                    raise ValueError("保存需要完整连接、角色分配和当前配置版本")
                video={}
                if "video_profiles" in payload:
                    if not isinstance(payload["video_profiles"],dict) or not isinstance(payload["video_roles"],dict):
                        raise ValueError("视频连接和分工应为对象")
                    video={"video_profiles":payload["video_profiles"],"video_roles":payload["video_roles"]}
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
                result = (authorize(pid,payload,resolve_video(load_config())) if operation == "cloud-authorize"
                          else confirm_cost(pid,payload))
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
