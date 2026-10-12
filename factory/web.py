"""Producer console API, served at the original studio address."""
from __future__ import annotations

import json
import re
from http.server import ThreadingHTTPServer
from urllib.parse import unquote,urlsplit

from studio.core import ROOT, within
from studio.web import Handler as LegacyHandler
from . import __version__, store
from .capabilities import snapshot
from .config import load_config


class Handler(LegacyHandler):
    server_version="DramaFactory/"+__version__

    def _local_request(self):
        """Keep the configuration surface on this server's loopback origin."""
        hosts={f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"}
        host_values=self.headers.get_all("Host",[])
        origin_values=self.headers.get_all("Origin",[])
        if len(host_values)!=1 or host_values[0].lower() not in hosts:
            return "Host rejected"
        if len(origin_values)>1 or (origin_values and origin_values[0] not in {"http://"+h for h in hosts}):
            return "Origin rejected"
        if self.headers.get("Sec-Fetch-Site")=="cross-site":
            return "Cross-site request rejected"
        return None

    def do_GET(self):
        path=unquote(urlsplit(self.path).path)
        try:
            if path=="/" or path.startswith(("/api/factory/","/factory/static/")):
                rejected=self._local_request()
                if rejected:
                    return self.json({"error":rejected},403)
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
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})",path)
            if match:
                return self.json(store.get(match.group(1)))
            if path=="/":
                return self._static(ROOT/"ui/factory/index.html","text/html; charset=utf-8")
            if path.startswith("/factory/static/"):
                rel=path[len("/factory/static/"):]
                target=within(ROOT/"ui/factory",rel)
                if target.suffix not in {".css",".js",".svg",".png",".jpg",".woff2"}:
                    return self.json({"error":"Static asset not found"},404)
                return self.send_file(target)
            if path=="/legacy":
                previous=self.path
                self.path="/"
                try:
                    return super().do_GET()
                finally:
                    self.path=previous
            return super().do_GET()
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
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        route=urlsplit(self.path).path
        if not route.startswith("/api/factory/"):
            return super().do_POST()
        try:
            rejected=self._local_request()
            if rejected:
                return self.json({"error":rejected},403)
            if self.headers.get("Content-Type","").split(";")[0].strip().lower()!="application/json":
                return self.json({"error":"JSON required"},415)
            if len(self.headers.get_all("Content-Length",[]))!=1:
                return self.json({"error":"请求长度无效"},400)
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=65536:
                return self.json({"error":"请求应为不超过64KB的JSON"},413)
            payload=json.loads(self.rfile.read(size),
                               parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
            if not isinstance(payload,dict):
                raise ValueError("JSON请求应为对象")
            if route=="/api/factory/models/test":
                from .providers import probe_profile
                if set(payload)!={"profile_id"} or not isinstance(payload["profile_id"],str):
                    raise ValueError("请指定一个已保存的连接编号")
                return self.json(probe_profile(payload["profile_id"],timeout=8))
            if route=="/api/factory/models":
                from .providers import save_registry
                if set(payload)!={"profiles","roles","expected_revision"}:
                    raise ValueError("保存需要完整连接、角色分配和当前配置版本")
                return self.json(save_registry(payload["profiles"],payload["roles"],
                                               payload["expected_revision"]))
            if route=="/api/factory/projects":
                return self.json({"project":store.create(payload)},202)
            match=re.fullmatch(r"/api/factory/projects/([a-f0-9]{12})/(decision|action)",route)
            if match:
                pid,action=match.groups()
                result=store.decide(pid,payload) if action=="decision" else store.action(pid,payload)
                return self.json({"project":result},202)
            return self.json({"error":"Not found"},404)
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
    store.initialize()
    config=load_config()["web"]
    server=ThreadingHTTPServer((config["host"],int(config["port"])),Handler)
    server.daemon_threads=True
    print(f"DRAMA_FACTORY_READY http://{config['host']}:{config['port']}",flush=True)
    server.serve_forever()


if __name__=="__main__":
    main()
