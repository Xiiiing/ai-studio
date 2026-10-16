"""Real loopback text HTTP and canonical graph contracts; no model/GPU inference."""
import copy
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from factory import h3_prompting
from factory.config import llm_identity
from factory.gpu_service import validate_graph
from factory.scheduler import ProductionNeedsDecision
from studio.graphs import h3_video


def ready_plan():
    return {
        "status": "ready", "blocked_reason": "",
        "visual_description": "Live-action medium shot of an adult man at a cafe table.",
        "initial_state": "The man in a blue shirt rests his hands around a white cup.",
        "timeline": [
            {"start_seconds": 0, "end_seconds": 2,
             "action": "He blinks naturally while looking toward the window."},
            {"start_seconds": 2, "end_seconds": 5,
             "action": "He turns his head gently toward the camera and smiles."},
        ],
        "end_state": "He faces the camera with a slight warm smile.",
        "camera": "The camera holds a static medium shot throughout.",
        "continuity": "The same man, shirt, cup and table retain their positions.",
        "audio": "ambient_only",
    }


@pytest.fixture
def text_server(monkeypatch):
    state = {"requests": [], "model": "fixture-qwen38-director",
             "response_model": None, "plan": ready_plan()}

    def forbid_dns(*args, **kwargs):
        raise AssertionError("H3 prompt HTTP tests must use literal loopback only")

    monkeypatch.setattr(socket, "getaddrinfo", forbid_dns)
    monkeypatch.setattr(socket, "getfqdn", lambda *args: "localhost")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append({"method": "GET", "path": self.path})
            if self.path != "/v1/models":
                self.send_error(404)
                return
            self.reply({"data": [{"id": state["model"],
                                   "architecture": {"input_modalities": ["text"]}}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"method": "POST", "path": self.path, "body": body})
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            self.reply({
                "model": state["response_model"] or state["model"],
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps(state["plan"])}}],
                "usage": {"prompt_tokens": 180, "completion_tokens": 120, "total_tokens": 300},
            })

        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def configuration(base, model):
    return {
        "schema_version": 1,
        "llm_profiles": {
            "selected_director": {
                "provider": "openai_compatible", "base_url": base, "model": model,
                "network_mode": "loopback", "enabled": True, "vision": False,
                "context_tokens": 32768, "response_format": "openai_json_schema",
                "context_validation": "conservative_estimate", "model_discovery": "required",
                "max_schema_attempts": 2,
            },
            "other_role": {
                "provider": "openai_compatible", "base_url": base,
                "model": "different-screenwriter-model", "context_tokens": 32768,
            },
        },
        "llm_roles": {"storyboard_director": "selected_director", "screenwriter": "other_role"},
    }


def compile_case(tmp_path, config):
    project = {"id": "abc123def456", "slug": "factory-abc123def456",
               "format": "live_action", "brief": "A quiet cafe reaction shot."}
    shot = {
        "id": "S01", "seconds": 5, "characters": ["lead"], "camera_motion": "hold",
        "action": "He blinks, then turns from the window toward the camera and smiles.",
        "prompt": "An adult man in a blue shirt sits at a cafe table with a white cup.",
        "motion_prompt": "He turns gently and smiles.",
        "continuity": "His hands and cup stay on the table.", "lip_sync": False,
    }
    reference = {"sha256": "a" * 64, "bytes": 100, "width": 768, "height": 1344,
                 "qa": {"semantic": {"status": "pass", "summary": "A man with a cup."}}}
    return h3_prompting.compile_prompt(
        project, shot, config=config, references=[reference], style="Live-action natural daylight",
        seconds=5, output_dir=tmp_path / "prompts", attempt=1,
    )


def call_receipts(tmp_path):
    return [json.loads(path.read_text()) for path in (tmp_path / "prompts" / "llm").glob("calls/*/receipt.json")]


@pytest.mark.parametrize("model", ["fixture-qwen38-director", "user-selected-local-director"])
def test_compiler_uses_configured_http_director_then_reuses_frozen_prompt(text_server, tmp_path, model):
    state, base = text_server
    state["model"] = model
    config = configuration(base, model)
    original = copy.deepcopy(config)
    result = compile_case(tmp_path, config)

    assert config == original
    assert [(call["method"], call["path"]) for call in state["requests"]] == [
        ("GET", "/v1/models"), ("POST", "/v1/chat/completions")]
    request = state["requests"][1]["body"]
    assert request["model"] == model
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["response_format"]["json_schema"]["schema"] == h3_prompting.PROMPT_SCHEMA
    payload = json.loads(request["messages"][1]["content"])
    assert payload["approved_shot"]["action"].startswith("He blinks, then turns")
    assert payload["first_frame_references"][0]["sha256"] == "a" * 64
    assert payload["seconds"] == 5

    receipt = result["receipt"]
    assert receipt["compiler"]["model_identity"] == llm_identity(config, "storyboard_director")
    assert receipt["compiler"]["version"] == "3.0.0"
    assert receipt["first_frame_sha256"] == "a" * 64
    assert receipt["quality_status"] == "pending_actual_media_review"
    receipts = call_receipts(tmp_path)
    assert len(receipts) == 1
    assert receipts[0]["status"] == "succeeded"
    assert receipts[0]["role"] == "storyboard_director"
    assert receipts[0]["profile"] == "selected_director"
    assert receipts[0]["response_model"] == model
    assert receipts[0]["prompt"]["status"] == "registered"
    assert receipts[0]["prompt"]["version"] == "3.0.0"

    prompt = result["prompt"]
    assert prompt.startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced.\n\n")
    assert (prompt.index("integrated_multimodal_description: [Shot 1]")
            < prompt.index("overall_soundscape:") < prompt.index("non_diegetic_music:"))

    graph = h3_video(result["prompt"], 42, "fixture-first-frame.png", seconds=5,
                     profile={"quality": "production", "steps": 20})
    validate_graph(graph, {"fixture-first-frame.png"}, "video")
    assert graph["6"]["inputs"]["prompt"] == result["prompt"]
    assert graph["6"]["inputs"]["first_frame"] == ["18", 0]
    assert compile_case(tmp_path, config) == result
    assert len(state["requests"]) == 2
    assert len(call_receipts(tmp_path)) == 1


def test_compiler_refuses_wrong_actual_http_response_model(text_server, tmp_path):
    state, base = text_server
    state["response_model"] = "unexpected-model"
    with pytest.raises(ProductionNeedsDecision) as error:
        compile_case(tmp_path, configuration(base, state["model"]))
    assert error.value.reason == "h3_prompt_unavailable"
    assert len([call for call in state["requests"] if call["method"] == "POST"]) == 1
    assert not list((tmp_path / "prompts").glob("h3-prompt-*.json"))
    receipts = call_receipts(tmp_path)
    assert len(receipts) == 1 and receipts[0]["status"] == "failed"
    assert receipts[0]["response_model"] == "unexpected-model"


def test_compiler_retains_http_director_feasibility_block_without_generation_prompt(text_server, tmp_path):
    state, base = text_server
    state["plan"] = {**ready_plan(), "status": "blocked",
                     "blocked_reason": "The approved action needs a cup missing from the approved starting state."}
    with pytest.raises(ProductionNeedsDecision) as error:
        compile_case(tmp_path, configuration(base, state["model"]))
    assert error.value.reason == "h3_prompt_infeasible"
    assert error.value.details["reason"] == state["plan"]["blocked_reason"]
    assert len([call for call in state["requests"] if call["method"] == "POST"]) == 1
    assert not list((tmp_path / "prompts").glob("h3-prompt-*.json"))
    receipts = call_receipts(tmp_path)
    assert len(receipts) == 1 and receipts[0]["status"] == "succeeded"


def test_compiler_stops_after_bounded_http_schema_repairs(text_server, tmp_path):
    state, base = text_server
    state["plan"] = {"status": "ready"}
    with pytest.raises(ProductionNeedsDecision) as error:
        compile_case(tmp_path, configuration(base, state["model"]))
    assert error.value.reason == "h3_prompt_unavailable"
    assert len([call for call in state["requests"] if call["method"] == "POST"]) == 2
    assert not list((tmp_path / "prompts").glob("h3-prompt-*.json"))
    receipts = call_receipts(tmp_path)
    assert len(receipts) == 1 and receipts[0]["status"] == "failed"
    assert len(receipts[0]["attempts"]) == 2
    assert all(attempt["validation_errors"] for attempt in receipts[0]["attempts"])
