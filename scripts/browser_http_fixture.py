#!/usr/bin/env python3
"""Actual Python HTTP boundary with disposable data; no worker or model processes."""
from __future__ import annotations

import copy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import signal
import socket
import sys
from tempfile import TemporaryDirectory

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))


def main():
    from factory import config, store, web
    from studio import core, web as legacy

    # Never discover the operator's private configuration or use live data.
    public_config = json.loads((SOURCE / "config/factory.example.json").read_text())
    with TemporaryDirectory(prefix="drama-http-browser-") as temporary:
        root = Path(temporary)
        shutil.copytree(SOURCE / "ui", root / "ui")
        media = root / "production/http-fixture"
        media.mkdir(parents=True)
        shutil.copyfile(SOURCE / "runtime/browser-fixtures/clip.mp4", media / "clip.mp4")
        core.ROOT = config.ROOT = web.ROOT = legacy.ROOT = root
        config.DATA = store.DATA = root / "runtime"
        config.DATABASE = store.DATABASE = store.DATA / "studio.sqlite3"
        config.CHECKPOINTS = store.DATA / "checkpoints.sqlite3"
        config.load_config = lambda: copy.deepcopy(public_config)
        web.snapshot = lambda: {"fixture": True, "models_run": False}
        legacy.episode_state = lambda slug: {"slug": slug, "fixture": True}
        legacy.jobs_state = lambda: []

        def forbid_launch(_payload):
            raise RuntimeError("Browser fixture cannot start production")

        legacy.launch = forbid_launch
        def forbid_outbound(*_args, **_kwargs):
            raise RuntimeError("Browser HTTP fixture forbids outbound connections")

        socket.create_connection = forbid_outbound
        socket.socket.connect = forbid_outbound
        store.initialize()
        project = store.create({"title": "HTTP 边界隔离测试", "brief": "Synthetic HTTP browser fixture; no generation is permitted."})
        store.action(project["id"], {"action": "pause"})
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        server.daemon_threads = True
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        print("HTTP_BROWSER_FIXTURE_READY " + json.dumps({"port": server.server_port, "project_id": project["id"]}), flush=True)
        try:
            server.serve_forever(poll_interval=.05)
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
