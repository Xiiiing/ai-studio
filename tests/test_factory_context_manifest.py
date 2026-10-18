"""Input provenance and reuse evidence, with isolated transport only."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest

from factory import context_manifest as cm, llm, schemas, team_flow
from factory.creative_evidence_io import digest
from tests.test_factory_role_routing import routing_config, mock_transport, SCHEMA


def project():
    return {"id": "face1234abcd", "slug": "factory-face1234abcd", "brief": "合成测试要求",
            "script_generation": 2, "revision": 9, "script_feedback": "结尾保留悬念",
            "series_bible": {"version": 3, "world": "虚构世界"}}


def test_frozen_inputs_keep_exact_feedback_and_do_not_assign_current_version_to_old_draft():
    p = project()
    older = {"version": 1, "world": "旧设定"}
    payload = {"project": {"brief": p["brief"]}, "series_bible": older,
               "producer_feedback": {"instructions": [{"source": "script_feedback", "content": p["script_feedback"]}]}}
    with cm.capture(p, "screenplay"):
        manifest = cm.build("screenwriter", payload, {})
    p["script_feedback"] = "修改后的反馈不能覆盖历史"
    rows = {m["field"]: m for m in manifest["materials"]}
    assert rows["series_bible"]["version"]["version"] == 1
    feedback = rows["producer_feedback.instructions[0].content"]
    assert feedback["summary"] == "结尾保留悬念"
    assert feedback["source_fields"] == ["script_feedback"]
    checks = {m["field"]: m for m in manifest["source_checks"]}
    assert checks["series_bible"]["state"] == "not_confirmed"
    assert checks["script_feedback"]["state"] == "exact_match"
    assert manifest["execution"] == {"project_id": p["id"], "stage": "screenplay"}
    assert cm.build("screenwriter", {}, {})["execution"] == {}


@pytest.mark.parametrize("role", ["story_editor", "screenwriter", "storyboard_director", "independent_critic"])
def test_actual_request_has_per_attempt_input_evidence(tmp_path, monkeypatch, role):
    calls, _ = mock_transport(monkeypatch)
    payload = {"producer_feedback": "结尾保留悬念", "episode": {"version": 4, "title": "合成剧本"}}
    with cm.capture(project(), "test-stage"):
        assert llm.json_call(routing_config(), role, "PRIVATE_ROLE_RULES", payload, SCHEMA, tmp_path) == {"ok": True}
    folder = next(tmp_path.glob("calls/*"))
    receipt = json.loads((folder / "receipt.json").read_text())
    manifest = receipt["context_manifest"]
    frozen = json.loads((folder / "context-01.json").read_text())
    assert frozen["request_sha256"] == digest(calls[0]["request"])
    assert manifest["input_sha256"] == digest(payload)
    assert manifest["project_version"]["script_generation"] == 2
    assert manifest["delivery"] == "response_received"
    assert manifest["budget"]["fits"] is True
    assert "PRIVATE_ROLE_RULES" not in json.dumps(cm.public(manifest))
    assert len(calls) == 1


def test_overflow_retains_required_inputs_and_blocks_generation(tmp_path, monkeypatch):
    calls, _ = mock_transport(monkeypatch)
    monkeypatch.setattr(llm, "context_budget", lambda *a: {"fits": False, "prompt_tokens": 35000, "context_tokens": 32768})
    payload = {"producer_feedback": "不能删除这一条", "episode": {"title": "必须保留的当前稿"}}
    with pytest.raises(ValueError, match="未裁剪"):
        llm.json_call(routing_config(), "screenwriter", "rules", payload, SCHEMA, tmp_path)
    assert calls == []
    receipt = json.loads(next(tmp_path.glob("calls/*/receipt.json")).read_text())
    assert receipt["status"] == "failed"
    assert receipt["context_manifest"]["delivery"] == "not_submitted"
    assert receipt["context_manifest"]["input_sha256"] == digest(payload)
    assert not list(tmp_path.glob("calls/*/request-01.json"))
    assert list(tmp_path.glob("calls/*/context-01.json"))


def test_multimodal_manifest_uses_actual_text_and_image_hashes_without_image_body():
    content = [{"type": "text", "text": json.dumps({"action": "拿起杯子", "approved_style": "画风"})},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,PRIVATE_IMAGE_BODY"}}]
    value = cm.build("visual_critic", None, {}, content=content)
    assert value["content_sha256"] == digest(content)
    assert value["materials"][0]["summary"] == "拿起杯子"
    assert "PRIVATE_IMAGE_BODY" not in json.dumps(value)


def test_cache_reuse_links_original_call_and_changed_feedback_invalidates_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    calls, _ = mock_transport(monkeypatch)
    p, cfg = project(), routing_config()
    def run():
        return schemas.cached_json_call(p, cfg, "screenwriter", "test", "rules",
                                       {"producer_feedback": p["script_feedback"]}, SCHEMA)
    run(); run()
    assert len(calls) == 1
    base = schemas.planning_dir(p)
    reuse = json.loads(next((base / "context-reuse").glob("*.json")).read_text())
    assert reuse["model_call_started"] is False
    assert reuse["origin_state"] == "verified"
    assert reuse["origin"]["call_id"] == next((base / "llm/calls").iterdir()).name
    assert reuse["context_manifest"]["materials"][0]["summary"] == "结尾保留悬念"
    p["script_feedback"] = "新的明确要求"
    run()
    assert len(calls) == 2


@pytest.mark.parametrize("origin_kind", ["missing", "wrong_output", "traversal", "symlink"])
def test_unverifiable_cache_origin_stays_unknown_without_reexecution(tmp_path, origin_kind):
    base = tmp_path / "planning"
    base.mkdir()
    receipt = {"status": "succeeded", "role": "screenwriter", "call_id": "old", "output_sha256": digest({"ok": True})}
    source = base / "receipt.json"
    source.write_text(json.dumps(receipt))
    origin = {"receipt": "receipt.json", "call_id": "old", "output_sha256": receipt["output_sha256"]}
    if origin_kind == "missing":
        origin = None
    elif origin_kind == "wrong_output":
        source.write_text(json.dumps({**receipt, "output_sha256": digest({"ok": False})}))
    elif origin_kind == "traversal":
        origin["receipt"] = "../receipt.json"
    else:
        linked = base / "linked.json"
        linked.symlink_to(source)
        origin["receipt"] = "linked.json"
    cm.record_reuse(base, project(), "screenwriter", "screenplay", {"origin": origin, "signature": "frozen", "value": {"ok": True}})
    row = json.loads(next((base / "context-reuse").glob("*.json")).read_text())
    assert row["origin_state"] == "unknown" and row["context_manifest"] is None
    assert row["origin"] is None


def test_cache_evidence_write_failure_cannot_trigger_new_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    calls, _ = mock_transport(monkeypatch)
    def run():
        return schemas.cached_json_call(project(), routing_config(), "screenwriter", "test", "rules", {}, SCHEMA)
    run()
    monkeypatch.setattr(cm, "record_reuse", lambda *a: (_ for _ in ()).throw(OSError("fixture read-only disk")))
    with pytest.raises(OSError):
        run()
    assert len(calls) == 1


def test_local_http_request_and_readonly_flow_projection(tmp_path, monkeypatch):
    """Real loopback HTTP transport and file reading, no real model or GPU."""
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, data):
            raw = json.dumps(data).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_GET(self): self.reply({"data": [{"id": "fixture-qwen"}]})
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(request)
            self.reply({"model": "fixture-qwen", "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        cfg = routing_config()
        cfg["llm_profiles"]["qwen"].update(base_url=f"http://127.0.0.1:{server.server_port}/v1",
            provider="openai_compatible", context_validation="conservative_estimate")
        monkeypatch.setattr(schemas, "ROOT", tmp_path)
        monkeypatch.setattr(team_flow, "ROOT", tmp_path)
        p = project()
        def run():
            schemas.cached_json_call(p, cfg, "screenwriter", "screenplay", "rules", {"producer_feedback": p["script_feedback"]}, SCHEMA, max_tokens=700)
        run(); run()
        assert len(seen) == 1
        before = {str(f): f.read_bytes() for f in tmp_path.rglob("*.json")}
        flow = team_flow.build_flow(p, cfg)
        executions = next(r for r in flow["nodes"] if r["role"] == "screenwriter")["executions"]
        assert len(executions) == 2
        assert sum(bool(r["reused"]) for r in executions) == 1
        assert all(r["context_manifest"]["materials"][0]["summary"] == "结尾保留悬念" for r in executions)
        assert {str(f): f.read_bytes() for f in tmp_path.rglob("*.json")} == before
    finally:
        server.shutdown(); server.server_close(); thread.join()
