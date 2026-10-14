"""Real loopback HTTP regression tests; all projects, files and effects are isolated."""
from __future__ import annotations

import http.client
import json
import socket
import threading
from http.server import ThreadingHTTPServer

import pytest

from factory import web
from studio import web as legacy


@pytest.fixture(params=[web.Handler, legacy.Handler], ids=["factory", "legacy"])
def http_endpoint(request, tmp_path, monkeypatch):
    effects = []
    monkeypatch.setattr(legacy, "ROOT", tmp_path)
    monkeypatch.setattr(web, "ROOT", tmp_path)
    monkeypatch.setattr(legacy, "launch", lambda payload: effects.append(payload) or {"state": "running"})
    monkeypatch.setattr(legacy, "episode_state", lambda slug: {"slug": slug})
    (tmp_path / "ui/factory").mkdir(parents=True)
    (tmp_path / "ui/index.html").write_text("<html><script></script></html>")
    (tmp_path / "ui/factory/index.html").write_text("<html>workbench</html>")
    media = tmp_path / "production/example/clip.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"synthetic-media-for-http-ranges")

    class Quiet(request.param):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()

    is_factory = request.param is web.Handler

    class Client:
        port = server.server_port
        factory = is_factory

        def request(self, method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
            try:
                conn.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
                response = conn.getresponse()
                raw = response.read()
                return response.status, dict(response.getheaders()), raw
            finally:
                conn.close()

        def raw(self, headers, *, method="POST", path="/api/start", body=b"{}", host=True):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
            try:
                conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
                if host:
                    conn.putheader("Host", f"127.0.0.1:{self.port}")
                for key, value in headers:
                    conn.putheader(key, value)
                conn.endheaders(body)
                response = conn.getresponse()
                raw = response.read()
                return response.status, dict(response.getheaders()), raw
            finally:
                conn.close()

    try:
        yield Client(), effects, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("path", ["/api/health", "/api/episodes", "/api/jobs", "/api/episode/example", "/media/production/example/clip.mp4", "/"])
@pytest.mark.parametrize("headers", [{"Host": "rebind.example"}, {"Origin": "https://cross-site.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}])
def test_all_read_routes_share_boundary(http_endpoint, path, headers):
    client, effects, _ = http_endpoint
    code, _, body = client.request("GET", path, headers=headers)
    assert code == 403
    assert json.loads(body)["error"]
    assert effects == []


@pytest.mark.parametrize("headers", [{"Host": "rebind.example"}, {"Origin": "https://cross-site.example"}, {"Sec-Fetch-Site": "cross-site"}])
def test_legacy_launch_rejected_before_effect(http_endpoint, headers):
    client, effects, _ = http_endpoint
    code, _, _ = client.request("POST", "/api/start", b"{}", headers)
    assert code == 403
    assert effects == []


def test_cli_no_origin_and_browser_actual_port(http_endpoint):
    client, effects, _ = http_endpoint
    assert client.request("GET", "/api/health")[0] == 200
    origin = f"http://127.0.0.1:{client.port}"
    assert client.request("POST", "/api/start", b"{}", {"Origin": origin})[0] == 202
    assert effects == [{}]
    assert client.request("GET", "/")[0] == 200
    if client.factory:
        assert client.request("GET", "/legacy")[0] == 200


@pytest.mark.parametrize("headers,body,status", [
    ([("Content-Type", "application/json"), ("Content-Length", "2"), ("Host", "evil.example")], b"{}", 403),
    ([("Content-Type", "application/json"), ("Content-Length", "2"), ("Origin", "null"), ("Origin", "null")], b"{}", 403),
    ([("Content-Type", "application/json"), ("Content-Length", "2"), ("Content-Length", "2")], b"{}", 400),
    ([("Content-Type", "application/json"), ("Content-Length", "2"), ("Transfer-Encoding", "chunked")], b"{}", 400),
    ([("Content-Type", "application/json"), ("Content-Type", "text/plain"), ("Content-Length", "2")], b"{}", 400),
    ([("Content-Type", "application/json"), ("Content-Length", "+2")], b"{}", 400),
    ([("Content-Type", "application/json"), ("Content-Length", "99999999")], b"", 413),
    ([("Content-Type", "application/json"), ("Content-Length", "1")], b"{", 400),
    ([("Content-Type", "application/json"), ("Content-Length", "2")], b"[]", 400),
    ([("Content-Type", "application/json"), ("Content-Length", "9")], b'{"x":NaN}', 400),
    ([("Content-Type", "application/json"), ("Content-Length", "13")], b'{"x":1,"x":2}', 400),
])
def test_malformed_requests_are_bounded_and_never_launch(http_endpoint, headers, body, status):
    client, effects, _ = http_endpoint
    code, _, raw = client.raw(headers, body=body)
    assert code == status
    error = json.loads(raw)
    assert isinstance(error["error"], str) and error["code"] and error["status"] == code
    assert effects == []


@pytest.mark.parametrize("path", ["/media/production/../config/private.json", "/media/production/%2e%2e/config/private.json", "/media/production/%252e%252e/config/private.json", "/media/production/example/%00.mp4", "/media/production/example/%5cclip.mp4"])
def test_invalid_media_paths(http_endpoint, path):
    client, _, _ = http_endpoint
    assert client.request("GET", path)[0] == 400


def test_media_symlinks_rejected_and_ranges_preserved(http_endpoint):
    client, _, root = http_endpoint
    folder = root / "production/example"
    (folder / "linked.mp4").symlink_to(folder / "clip.mp4")
    (folder / "linked-dir").symlink_to(folder, target_is_directory=True)
    for path in ["linked.mp4", "linked-dir/clip.mp4"]:
        assert client.request("GET", "/media/production/example/" + path)[0] == 403
    code, headers, body = client.request("GET", "/media/production/example/clip.mp4", headers={"Range": "bytes=0-8"})
    assert code == 206 and body == b"synthetic"
    assert headers["Content-Length"] == "9" and headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("path", ["/api/episode/a/extra", "/api/factory/projects/not-a-project", "/api/factory/jobs/not-a-job"])
def test_invalid_identifier_never_reaches_backend(http_endpoint, path):
    client, effects, _ = http_endpoint
    expected = 404 if path.startswith("/api/factory/") and not client.factory else 400
    assert client.request("GET", path)[0] == expected
    assert effects == []


@pytest.mark.parametrize("headers", [[("X-Test", "bad\x01value")], [("X-Test", "folded\r\n value")], [("Range", "bytes=0-1"), ("Range", "bytes=2-3")], [("Expect", "100-continue")]])
def test_illegal_headers_rejected_before_dispatch(http_endpoint, headers):
    client, effects, _ = http_endpoint
    code, _, body = client.raw(headers, method="GET", path="/api/health", body=b"")
    assert code == 400 and json.loads(body)["status"] == 400
    assert effects == []


def test_missing_host_and_mismatched_local_origin_are_rejected(http_endpoint):
    client, effects, _ = http_endpoint
    assert client.raw([], method="GET", path="/api/health", body=b"", host=False)[0] == 403
    assert client.request("POST", "/api/start", b"{}", {"Origin": f"http://localhost:{client.port}"})[0] == 403
    assert effects == []


@pytest.mark.parametrize("target", ["//api/health", "http://127.0.0.1/api/health", "/api/%ZZhealth", "/api/%ffhealth"])
def test_noncanonical_request_targets_are_not_reinterpreted(http_endpoint, target):
    client, effects, _ = http_endpoint
    assert client.request("GET", target, headers={"Host": f"127.0.0.1:{client.port}"})[0] == 400
    assert effects == []


def test_unsupported_method_is_json_and_cannot_launch(http_endpoint):
    client, effects, _ = http_endpoint
    code, headers, body = client.request("DELETE", "/api/start")
    assert code == 405 and json.loads(body)["code"] == "method_not_allowed"
    assert headers["Cache-Control"] == "no-store" and effects == []


def test_body_timeout_is_bounded_without_side_effect(http_endpoint, monkeypatch):
    from studio.http_security import LocalHTTPBoundary
    monkeypatch.setattr(LocalHTTPBoundary, "request_timeout", .05)
    client, effects, _ = http_endpoint
    code, _, body = client.raw([("Content-Type", "application/json"), ("Content-Length", "3")], body=b"{")
    assert code == 408 and json.loads(body)["code"] == "request_timeout"
    assert effects == []


@pytest.mark.parametrize("body", [b'{"x":1e999}', b'{"x":"\\ud800"}', b'{"x":' + b'[' * 1100 + b']' * 1100 + b'}', b'{"x":"\xff"}'], ids=["nonfinite", "unpaired-surrogate", "deep-nesting", "invalid-utf8"])
def test_invalid_json_scalars_and_excessive_nesting_never_launch(http_endpoint, body):
    client, effects, _ = http_endpoint
    assert client.request("POST", "/api/start", body)[0] == 400
    assert effects == []


def test_job_endpoints_preserve_sqlite_and_pause_semantics(http_endpoint, monkeypatch):
    from factory import store
    client, effects, root = http_endpoint
    monkeypatch.setattr(store, "DATA", root / "db")
    monkeypatch.setattr(store, "DATABASE", root / "db/studio.sqlite3")
    store.initialize()
    project = store.create({"brief": "Synthetic HTTP job control contract only."})
    with store.connection() as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE project_id=?", (project["id"],)).fetchone())
    path = "/api/factory/jobs/" + job["id"]
    code, _, body = client.request("GET", path)
    if not client.factory:
        assert code == 404
        assert client.request("POST", path + "/pause", b"{}")[0] == 404
        assert store.get(project["id"], False) == project
        return
    assert code == 200 and json.loads(body)["queue_state"] == "queued"
    payload = json.dumps({"expected_revision": project["revision"], "expected_lease": job["lease"]}).encode()
    code, _, body = client.request("POST", path + "/cancel", payload)
    assert code == 409 and json.loads(body)["external_cancelled"] is False
    assert store.get(project["id"], False) == project
    assert client.request("POST", path + "/pause", b"{}")[0] == 400
    code, _, body = client.request("POST", path + "/pause", payload)
    assert code == 202 and json.loads(body)["job"]["state"] == "paused"
    assert client.request("GET", "/api/factory/jobs/" + "0" * 32)[0] == 404
    assert effects == []


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2", "public.example"])
def test_nonloopback_startup_fails_before_database_or_bind(monkeypatch, host):
    monkeypatch.setattr(web, "load_config", lambda: {"web": {"host": host, "port": 8787}})
    def forbidden():
        raise AssertionError("Unsafe bind must fail before database initialization")
    monkeypatch.setattr(web.store, "initialize", forbidden)
    with pytest.raises(ValueError, match="Non-loopback"):
        web.main()


def test_loopback_bind_and_port_validation():
    from studio.http_security import validate_bind
    assert validate_bind("localhost", "8787") == ("127.0.0.1", 8787)
    assert validate_bind("::1", 0) == ("::1", 0)
    for value in [True, -1, 65536, "8787junk"]:
        with pytest.raises(ValueError):
            validate_bind("127.0.0.1", value)


@pytest.mark.parametrize("method,path", [("GET", "/api/episode/example"), ("POST", "/api/start")])
def test_unexpected_legacy_errors_share_safe_json_contract(http_endpoint, monkeypatch, method, path):
    client, effects, _ = http_endpoint
    def fail(*_args):
        raise OSError("synthetic-private-provider-diagnostic")
    monkeypatch.setattr(legacy, "episode_state" if method == "GET" else "launch", fail)
    code, _, body = client.request(method, path, b"{}" if method == "POST" else None)
    assert code == 500
    result = json.loads(body)
    assert result["status"] == 500 and result["code"] == "internal_error"
    assert b"synthetic-private-provider-diagnostic" not in body
    assert effects == []
