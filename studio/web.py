"""Shared local HTTP/media transport. The former dashboard and launcher are retired.

There is deliberately no standalone server or subprocess production endpoint here.
The only supported application entrypoint is factory.web.
"""
from __future__ import annotations

import mimetypes
import os
import re
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import unquote, urlsplit

from .core import ROOT
from .http_security import HTTPError, LocalHTTPBoundary
from .http_media import open_media


class Handler(LocalHTTPBoundary, BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(time.strftime("%Y-%m-%d %H:%M:%S"), fmt % args, flush=True)

    def do_GET(self):
        try:
            route = unquote(urlsplit(self.path).path)
            if route == "/api/health":
                return self.json({"service": "drama-studio", "status": "ready"})
            if route in {"/", "/legacy", "/api/episodes", "/api/jobs"} or route.startswith("/api/episode/"):
                return self.json({"error": "旧版界面与工具已移除，请使用制片控制台。", "code": "legacy_retired"}, 410)
            if route.startswith("/media/"):
                path = ROOT / route[len("/media/"):]
                if not path.is_relative_to(ROOT / "production") or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".mp4", ".wav", ".srt", ".ass", ".json", ".md", ".csv", ".zip"}:
                    raise ValueError("File is outside the production media set")
                return self.send_file(path)
            return self.json({"error": "Not found"}, 404)
        except HTTPError as exc:
            return self.json({"error": str(exc), "code": exc.code}, exc.status)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            return self.json({"error": str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.log_error("HTTP failure: %s", type(exc).__name__)
            return self.json({"error": "Internal request failure"}, 500)

    def do_POST(self):
        try:
            self.read_json_body()
            if urlsplit(self.path).path == "/api/start":
                return self.json({"error": "旧版启动接口已删除，请从制片项目发起任务。", "code": "legacy_retired"}, 410)
            return self.json({"error": "Not found"}, 404)
        except HTTPError as exc:
            return self.json({"error": str(exc), "code": exc.code}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.log_error("HTTP failure: %s", type(exc).__name__)
            return self.json({"error": "Internal request failure"}, 500)

    def send_file(self, path, *, root=None):
        try:
            with open_media(ROOT if root is None else root, path) as source:
                size = os.fstat(source.fileno()).st_size
                start, end, partial = 0, size - 1, False
                header = self.headers.get("Range")
                if header:
                    match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", header)
                    if not match:
                        raise HTTPError(416, "invalid_range", "Unsupported range")
                    a, b = match.groups()
                    if a:
                        start = int(a); end = min(end, int(b)) if b else end
                    elif b and int(b) > 0:
                        start = max(0, size - int(b))
                    else:
                        raise HTTPError(416, "invalid_range", "Empty range")
                    if start > end or start >= size:
                        raise HTTPError(416, "invalid_range", "Range outside file")
                    partial = True
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(max(0, end - start + 1)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                source.seek(start); remaining = end - start + 1
                while remaining > 0:
                    block = source.read(min(256 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block); remaining -= len(block)
        except HTTPError as exc:
            return self.json({"error": str(exc), "code": exc.code}, exc.status)
