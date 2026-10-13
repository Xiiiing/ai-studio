"""Video adapters use only loopback HTTP fixtures; no real models, GPUs or paid APIs."""
import copy
import json
import os
import socket
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from factory import config, minimax_video as video, providers, video_providers, video_transport


AUTH_VALUE = "VIDEO_FIXTURE_SECRET_DO_NOT_PERSIST"
AUTH_ENV = "DRAMA_VIDEO_FIXTURE_KEY"
ASSET = b"\x00\x00\x00\x18ftypmp42" + b"offline-video-fixture" * 12


def remote_profile(**changes):
    return {"provider": "minimax", "enabled": True, "model": "MiniMax-Hailuo-2.3",
            "base_url": "http://127.0.0.1:8765/v1", "network_mode": "loopback",
            "api_key_env": AUTH_ENV, "request_timeout_seconds": 2, "max_wait_seconds": 10,
            "poll_interval_seconds": 1, **changes}


def config_fixture():
    text = {"provider": "openai_compatible", "base_url": "http://127.0.0.1:8090/v1",
            "model": "fixture-text", "context_tokens": 32768, "vision": True}
    return {"schema_version": 1, "llm": copy.deepcopy(text), "llm_profiles": {"text": text},
            "llm_roles": {role: "text" for role in config.ROLE_LABELS},
            "production": {"sentinel": "preserve-existing-settings"}}


@pytest.fixture(autouse=True)
def credentials_and_network_guard(monkeypatch):
    monkeypatch.setenv(AUTH_ENV, AUTH_VALUE)
    def no_dns(*args, **kwargs):
        raise AssertionError("Tests may contact only their own literal loopback server")
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    monkeypatch.setattr(socket, "getfqdn", lambda *args: "localhost")


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "frame.png"
    Image.new("RGB", (384, 512), (24, 40, 64)).save(path)
    return path


@pytest.fixture
def video_server():
    state = {"requests": [], "task_status": "Success", "query_task_id": "task_fixture",
             "query_file_id": "file_fixture", "retrieve_file_id": "file_fixture",
             "overrides": {}, "disconnect_submission": False, "asset": ASSET,
             "submit_entered": None, "submit_release": None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.handle_request()
        def do_POST(self):
            self.handle_request()
        def handle_request(self):
            parsed = urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append({"method": self.command, "path": parsed.path,
                                      "query": parse_qs(parsed.query),
                                      "auth": self.headers.get("Authorization"),
                                      "body": json.loads(body) if body else None})
            if parsed.path == "/v1/video_generation":
                if state["submit_entered"]:
                    state["submit_entered"].set()
                    state["submit_release"].wait(3)
                if state["disconnect_submission"]:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                data = {"task_id": "task_fixture", "base_resp": {"status_code": 0}}
            elif parsed.path == "/v1/query/video_generation":
                data = {"task_id": state["query_task_id"], "status": state["task_status"],
                        "file_id": state["query_file_id"], "video_width": 768, "video_height": 1024,
                        "base_resp": {"status_code": 0}}
            elif parsed.path == "/v1/files/retrieve":
                data = {"file": {"file_id": state["retrieve_file_id"],
                                "download_url": state["base"] + "/assets/source.mp4?signature=SIGNED_FIXTURE"},
                        "base_resp": {"status_code": 0}}
            elif parsed.path == "/assets/source.mp4":
                data = state["asset"]
            else:
                data = {"error": "Unexpected fixture route"}
            status, data, headers = state["overrides"].get(parsed.path, (200, data, {}))
            raw = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
            self.send_response(status)
            if parsed.path != "/assets/source.mp4" or not state.get("omit_asset_length"):
                self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Type", "video/mp4" if isinstance(data, bytes) else "application/json")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            try:
                if parsed.path == "/assets/source.mp4" and state.get("drip_delay"):
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(state.get("drip_delay", 0))
                else:
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    state["base"] = "http://127.0.0.1:" + str(server.server_port)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield state, remote_profile(base_url=state["base"] + "/v1")
    finally:
        if state["submit_release"]:
            state["submit_release"].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def generate(profile, frame, folder, **kwargs):
    # Transport tests explicitly provide a test authorizer. Real ledger coverage
    # is in test_factory_cloud_budget.py; the adapter has no implicit bypass.
    kwargs.setdefault("before_submit", lambda request: {"test_authorization": True})
    return video.generate_video(profile, "A quiet shot with a slowly opening door.", frame, folder,
                                source_signature="approved-shot-v1", **kwargs)


def count(state, path):
    return sum(request["path"] == path for request in state["requests"])


def journal(folder):
    return json.loads((folder / "remote_task.json").read_text())


def test_submit_query_download_and_cached_resume_use_one_task(video_server, frame, tmp_path):
    state, profile = video_server
    folder = tmp_path / "take"
    events = []
    raw, receipt = generate(profile, frame, folder, event=events.append)
    assert raw.read_bytes() == ASSET and receipt["status"] == "downloaded"
    assert receipt["task_id"] == "task_fixture" and receipt["file_id"] == "file_fixture"
    assert [item["status"] for item in events] == ["submitting", "submitted", "generated", "downloaded"]
    assert [item["path"] for item in state["requests"]] == [
        "/v1/video_generation", "/v1/query/video_generation", "/v1/files/retrieve", "/assets/source.mp4"]
    assert all(item["auth"] == "Bearer " + AUTH_VALUE for item in state["requests"][:3])
    assert state["requests"][-1]["auth"] is None
    assert state["requests"][-1]["query"] == {"signature": ["SIGNED_FIXTURE"]}
    assert state["requests"][1]["query"] == {"task_id": ["task_fixture"]}
    assert state["requests"][2]["query"] == {"file_id": ["file_fixture"]}
    body = state["requests"][0]["body"]
    assert body["model"] == profile["model"] and body["duration"] == 6 and body["resolution"] == "768P"
    assert body["first_frame_image"].startswith("data:image/png;base64,")
    assert body["prompt_optimizer"] is False
    before = len(state["requests"])
    assert generate(profile, frame, folder)[0] == raw
    assert len(state["requests"]) == before
    for artifact in folder.glob("*.json"):
        text = artifact.read_text()
        assert AUTH_VALUE not in text and "SIGNED_FIXTURE" not in text and "data:image" not in text
    assert AUTH_ENV in (folder / "remote_task.json").read_text()


def test_query_identifiers_are_literal_encoded_data(video_server):
    state, profile = video_server
    profile = video_providers.normalize_video_profile(profile)
    video_transport.video_request(profile, "/query/video_generation",
                                  params={"task_id": "task&file_id=extra"})
    assert state["requests"][0]["query"] == {"task_id": ["task&file_id=extra"]}


@pytest.mark.parametrize("status,code", [(301, "http_301"), (401, "http_401"),
                                        (429, "http_429"), (500, "http_500")])
def test_api_http_errors_do_not_follow_redirects_retry_or_echo_secrets(video_server, status, code):
    state, profile = video_server
    state["overrides"]["/v1/video_generation"] = (
        status, (AUTH_VALUE + " upstream details").encode(), {"Location": "http://169.254.169.254/metadata"})
    with pytest.raises(providers.ProviderError) as error:
        video_transport.video_request(video_providers.normalize_video_profile(profile),
                                      "/video_generation", payload={"prompt": "fixture"})
    assert error.value.code == code and AUTH_VALUE not in str(error.value)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("body,code", [
    ({"base_resp": {"status_code": 1002, "status_msg": AUTH_VALUE}}, "api_1002"),
    ({"base_resp": {"status_code": "0"}}, "invalid_response"),
    ({"task_id": "unconfirmed"}, "invalid_response"),
    (b'{"base_resp":{"status_code":NaN}}', "invalid_json"),
    (b"not-json", "invalid_json"),
])
def test_business_and_malformed_responses_are_safe(video_server, body, code):
    state, profile = video_server
    state["overrides"]["/v1/video_generation"] = (200, body, {})
    with pytest.raises(providers.ProviderError) as error:
        video_transport.video_request(video_providers.normalize_video_profile(profile),
                                      "/video_generation", payload={})
    assert error.value.code == code and AUTH_VALUE not in str(error.value)
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("omit_length", [False, True])
def test_download_size_and_redirect_failure_preserve_destination(video_server, tmp_path, omit_length):
    state, profile = video_server
    state["omit_asset_length"] = omit_length
    profile = video_providers.normalize_video_profile({**profile, "max_download_bytes": 1024})
    destination = tmp_path / "existing.mp4"
    destination.write_bytes(b"keep-existing-video")
    state["asset"] = b"x" * 1025
    with pytest.raises(providers.ProviderError) as error:
        video_transport.download_video(profile, state["base"] + "/assets/source.mp4", destination)
    assert error.value.code == "response_too_large"
    assert destination.read_bytes() == b"keep-existing-video" and not Path(str(destination) + ".part").exists()
    state["overrides"]["/assets/source.mp4"] = (302, b"", {"Location": "http://169.254.169.254/metadata"})
    with pytest.raises(providers.ProviderError) as error:
        video_transport.download_video(profile, state["base"] + "/assets/source.mp4", destination)
    assert error.value.code == "http_302"
    assert all(request["auth"] is None for request in state["requests"])
    assert len(state["requests"]) == 2 and destination.read_bytes() == b"keep-existing-video"


@pytest.mark.parametrize("url", ["http://example.com/video", "https://127.0.0.1/video",
                                 "https://169.254.169.254/video", "https://user:pass@example.com/video",
                                 "file:///private/video.mp4"])
def test_remote_artifact_url_cannot_escape_public_https_scope(tmp_path, url):
    profile = video_providers.normalize_video_profile(remote_profile(
        base_url="https://api.example.com/v1", network_mode="remote",
        allowed_hosts=["api.example.com"]))
    with pytest.raises(providers.ProviderError):
        video_transport.download_video(profile, url, tmp_path / "source.mp4")
    assert not (tmp_path / "source.mp4").exists()


def test_explicit_download_host_restriction_precedes_dns(tmp_path):
    profile = video_providers.normalize_video_profile(remote_profile(download_hosts=["cdn.allowed.example"]))
    with pytest.raises(providers.ProviderError) as error:
        video_transport.download_video(profile, "http://cdn.other.example/video", tmp_path / "source.mp4")
    assert error.value.code == "download_host"


@pytest.mark.parametrize("route,kwargs", [
    ("/other", {}), ("/video_generation", {"params": {"task_id": "t"}}),
    ("/query/video_generation", {"params": {"file_id": "f"}}),
    ("/files/retrieve", {"params": {"file_id": "f"}, "payload": {}}),
])
def test_video_routes_and_query_shapes_fail_before_network(route, kwargs):
    profile = video_providers.normalize_video_profile(remote_profile())
    with pytest.raises(ValueError):
        video_transport.video_request(profile, route, **kwargs)


def test_missing_auth_or_disabled_profile_does_not_contact_server(video_server, monkeypatch):
    state, profile = video_server
    monkeypatch.delenv(AUTH_ENV)
    for value, code in ((profile, "missing_auth"), ({**profile, "enabled": False}, "disabled")):
        with pytest.raises(providers.ProviderError) as error:
            video_transport.video_request(video_providers.normalize_video_profile(value),
                                          "/video_generation", payload={})
        assert error.value.code == code
    assert state["requests"] == []


def test_uncertain_submission_is_never_automatically_repeated(video_server, frame, tmp_path):
    state, profile = video_server
    state["disconnect_submission"] = True
    folder = tmp_path / "unknown"
    for _ in range(2):
        with pytest.raises(video.VideoPending) as error:
            generate(profile, frame, folder)
        assert error.value.code == "video_submission_unknown"
    assert count(state, "/v1/video_generation") == 1
    assert journal(folder)["status"] == "submission_unknown" and not journal(folder).get("task_id")


def test_business_rejection_is_retained_without_resubmission(video_server, frame, tmp_path):
    state, profile = video_server
    state["overrides"]["/v1/video_generation"] = (
        200, {"base_resp": {"status_code": 1002, "status_msg": AUTH_VALUE}}, {})
    folder = tmp_path / "rejected"
    for _ in range(2):
        with pytest.raises(video.VideoPending) as error:
            generate(profile, frame, folder)
        assert error.value.code == "video_submission_rejected"
    assert count(state, "/v1/video_generation") == 1 and journal(folder)["status"] == "rejected"
    assert AUTH_VALUE not in (folder / "remote_task.json").read_text()


def test_poll_timeout_resumes_the_same_task(video_server, frame, tmp_path):
    state, profile = video_server
    state["task_status"] = "Processing"
    folder = tmp_path / "poll-timeout"
    with pytest.raises(video.VideoPending) as error:
        generate(profile, frame, folder, timeout=.2)
    assert error.value.code == "video_wait_timeout" and journal(folder)["task_id"] == "task_fixture"
    state["task_status"] = "Success"
    assert generate(profile, frame, folder)[1]["status"] == "downloaded"
    assert count(state, "/v1/video_generation") == 1


def test_download_failure_resumes_file_retrieval_without_new_generation(video_server, frame, tmp_path):
    state, profile = video_server
    state["overrides"]["/assets/source.mp4"] = (503, b"unavailable", {})
    folder = tmp_path / "download-retry"
    with pytest.raises(video.VideoPending) as error:
        generate(profile, frame, folder)
    assert error.value.code == "video_download_pending" and journal(folder)["file_id"] == "file_fixture"
    state["overrides"].clear()
    assert generate(profile, frame, folder)[0].read_bytes() == ASSET
    assert count(state, "/v1/video_generation") == count(state, "/v1/query/video_generation") == 1
    assert count(state, "/v1/files/retrieve") == count(state, "/assets/source.mp4") == 2


def test_slow_download_timeout_removes_partial_data_and_resumes_without_submission(video_server, frame, tmp_path):
    state, profile = video_server
    state["drip_delay"] = .03
    folder = tmp_path / "slow-download"
    with pytest.raises(video.VideoPending) as error:
        generate(profile, frame, folder, timeout=.2)
    assert error.value.code == "video_download_pending"
    assert journal(folder)["file_id"] == "file_fixture"
    assert not (folder / "source.mp4").exists() and not (folder / "source.mp4.part").exists()
    state["drip_delay"] = 0
    assert generate(profile, frame, folder)[0].read_bytes() == ASSET
    assert count(state, "/v1/video_generation") == count(state, "/v1/query/video_generation") == 1
    assert count(state, "/v1/files/retrieve") == 2


@pytest.mark.parametrize("field,value,code", [
    ("query_task_id", "another_task", "video_status_unavailable"),
    ("retrieve_file_id", "another_file", "video_download_pending"),
    ("task_status", "Unexpected", "video_status_unavailable"),
])
def test_remote_task_and_file_identity_must_match(video_server, frame, tmp_path, field, value, code):
    state, profile = video_server
    state[field] = value
    folder = tmp_path / "wrong-identity"
    with pytest.raises(video.VideoPending) as error:
        generate(profile, frame, folder)
    assert error.value.code == code and count(state, "/assets/source.mp4") == 0
    assert count(state, "/v1/video_generation") == 1


def test_terminal_failure_never_resubmits_the_same_take(video_server, frame, tmp_path):
    state, profile = video_server
    state["task_status"] = "Fail"
    folder = tmp_path / "failed"
    for _ in range(2):
        with pytest.raises(video.VideoFailed):
            generate(profile, frame, folder)
    assert count(state, "/v1/video_generation") == count(state, "/v1/query/video_generation") == 1
    assert journal(folder)["status"] == "failed"


def test_changed_inputs_and_tampered_cache_are_blocked_before_requests(video_server, frame, tmp_path):
    state, profile = video_server
    folder = tmp_path / "cached"
    raw, _ = generate(profile, frame, folder)
    before = len(state["requests"])
    with pytest.raises(video.VideoPending) as error:
        video.generate_video(profile, "Different prompt", frame, folder, source_signature="approved-shot-v1")
    assert error.value.code == "video_request_changed"
    raw.write_bytes(b"tampered-video")
    with pytest.raises(video.VideoPending) as error:
        generate(profile, frame, folder)
    assert error.value.code == "video_cache_integrity" and len(state["requests"]) == before


def test_two_callers_for_one_take_do_not_duplicate_submission(video_server, frame, tmp_path):
    state, profile = video_server
    state["submit_entered"], state["submit_release"] = threading.Event(), threading.Event()
    folder = tmp_path / "concurrent"
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(generate, profile, frame, folder)
        assert state["submit_entered"].wait(2)
        try:
            with pytest.raises(video.VideoPending) as error:
                generate(profile, frame, folder)
            assert error.value.code == "video_task_busy"
        finally:
            state["submit_release"].set()
        assert first.result(timeout=3)[1]["status"] == "downloaded"
    assert count(state, "/v1/video_generation") == 1
    assert generate(profile, frame, folder)[1]["task_id"] == "task_fixture"


def test_submission_intent_is_written_and_directory_synced_before_the_post(
        video_server, frame, tmp_path, monkeypatch):
    state, profile = video_server
    folder = tmp_path / "durable"
    synced_directories = set()
    original_sync = os.fsync
    def sync(descriptor):
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            synced_directories.add(info.st_ino)
        return original_sync(descriptor)
    original_request = video.video_request
    def request(*args, **kwargs):
        if args[1] == "/video_generation":
            assert journal(folder)["status"] == "submitting"
            assert folder.stat().st_ino in synced_directories
            assert folder.parent.stat().st_ino in synced_directories
        return original_request(*args, **kwargs)
    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(video, "video_request", request)
    generate(profile, frame, folder)
    assert count(state, "/v1/video_generation") == 1


@pytest.mark.parametrize("change", [
    {"api_key": AUTH_VALUE}, {"duration": 8}, {"resolution": "512P"},
    {"duration": 10, "resolution": "1080P"}, {"api_key_env": "KEY=value"},
    {"max_download_bytes": 512 * 1024 * 1024 + 1},
    {"download_hosts": ["*.example.com"]}, {"request_timeout_seconds": float("inf")},
    {"base_url": "http://169.254.169.254/v1", "network_mode": "remote",
     "allowed_hosts": ["169.254.169.254"]},
])
def test_invalid_video_profiles_fail_without_echoing_credentials(change):
    with pytest.raises(ValueError) as error:
        video_providers.normalize_video_profile(remote_profile(**change))
    assert AUTH_VALUE not in str(error.value)


def test_legacy_video_default_and_configuration_only_probe():
    cfg = config_fixture()
    assert video_providers.resolve_video(cfg)["provider"] == "comfyui_wan"
    cfg["video_profiles"] = {"cloud": remote_profile()}
    cfg["video_roles"] = {"video_generator": "cloud"}
    result = video_providers.probe_video_profile("cloud", cfg)
    assert result["status"] == "configured" and result["auth_configured"] is True
    assert result["network_checked"] is False and result["generation"] is False and result["model_match"] is False
    assert AUTH_VALUE not in json.dumps(video_providers.public_video_inventory(cfg))


def test_text_and_video_registry_save_is_atomic_and_uses_one_revision(tmp_path):
    cfg = config_fixture()
    path = tmp_path / "factory.json"
    path.write_text(json.dumps(cfg))
    inventory = providers.public_inventory(cfg)
    text_profiles = copy.deepcopy(inventory["editable_profiles"])
    text_profiles["text"]["model"] = "updated-text"
    saved = providers.save_registry(text_profiles, inventory["role_assignments"], inventory["revision"],
                                    config_path=path, video_profiles={"cloud": remote_profile()},
                                    video_roles={"video_generator": "cloud"})
    content = json.loads(path.read_text())
    assert content["llm_profiles"]["text"]["model"] == "updated-text"
    assert content["video_profiles"]["cloud"]["provider"] == "minimax"
    assert content["production"] == cfg["production"] and AUTH_VALUE not in path.read_text()
    assert saved["video_roles"] == {"video_generator": "cloud"}
    with pytest.raises(providers.ConfigConflict):
        providers.save_registry(text_profiles, inventory["role_assignments"], inventory["revision"],
                                config_path=path)
    before = path.read_bytes()
    bad = copy.deepcopy(text_profiles)
    bad["text"]["model"] = "must-not-be-written"
    with pytest.raises(ValueError):
        providers.save_registry(bad, saved["role_assignments"], saved["revision"], config_path=path,
                                video_profiles={"cloud": remote_profile(enabled=False)},
                                video_roles={"video_generator": "cloud"})
    assert path.read_bytes() == before
    updated = providers.save_registry(bad, saved["role_assignments"], saved["revision"], config_path=path)
    assert json.loads(path.read_text())["video_profiles"] == content["video_profiles"]
    assert updated["editable_profiles"]["text"]["model"] == "must-not-be-written"
