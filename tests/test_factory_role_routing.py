"""Offline routing contracts; these tests do not assess a model's story judgment."""
import copy
import json
import socket
import subprocess
from unittest.mock import patch

import pytest

from factory import config as factory_config, creative, llm, schemas


SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def routing_config():
    qwen = {"base_url": "http://127.0.0.1:8090/v1", "model": "fixture-qwen",
            "context_tokens": 32768, "vision": True,
            "response_format": "llama_json_schema",
            "chat_template_kwargs": {"enable_thinking": False}}
    critic = {"base_url": "http://127.0.0.1:8089/v1", "model": "fixture-critic",
              "context_tokens": 32768, "vision": False, "temperature_override": .6,
              "top_p": .95, "system_prompt_mode": "user_prefix",
              "chat_template_kwargs": {}, "api_key_env": "FACTORY_ROUTER_TEST_KEY"}
    return {"llm": copy.deepcopy(qwen), "llm_profiles": {"qwen": qwen, "critic": critic},
            "llm_roles": {"screenwriter": "qwen", "independent_critic": "critic",
                          "story_critic": "critic", "visual_critic": "qwen"}}


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Routing tests must not call a model, GPU or network")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    # Production review cache tests need constants, not binary discovery at import.
    monkeypatch.setattr("imageio_ffmpeg.get_ffmpeg_exe", lambda: "/fixture/ffmpeg")


def mock_transport(monkeypatch, responder=None):
    calls, checks = [], []
    def check(configuration):
        checks.append((configuration["base_url"], configuration["model"]))
        return {"data": [{"id": configuration["model"]}]}
    def send(endpoint, route, request, **kwargs):
        calls.append({"endpoint": endpoint, "route": route, "request": copy.deepcopy(request),
                      "options": kwargs})
        if responder:
            return responder(request)
        return {"model": request["model"], "choices": [{
            "finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 7}}
    monkeypatch.setattr(llm, "check_model", check)
    monkeypatch.setattr(llm, "context_budget", lambda *args: {"fits": True, "prompt_tokens": 31})
    monkeypatch.setattr(llm, "request_json", send)
    return calls, checks


def test_writer_and_critic_use_distinct_endpoints_and_record_real_policy(tmp_path, monkeypatch):
    cfg = routing_config()
    monkeypatch.setenv("FACTORY_ROUTER_TEST_KEY", "PRIVATE_VALUE_MUST_NOT_BE_SAVED")
    calls, checks = mock_transport(monkeypatch)
    for role, tokens, temperature in (("screenwriter", 700, .65), ("independent_critic", 900, .25)):
        assert llm.json_call(cfg, role, "ROLE_RULE_SENTINEL", {"source": "TASK_DATA_SENTINEL"},
                             SCHEMA, tmp_path, max_tokens=tokens, temperature=temperature) == {"ok": True}
    assert checks == [("http://127.0.0.1:8090/v1", "fixture-qwen"),
                      ("http://127.0.0.1:8089/v1", "fixture-critic")]
    assert [call["endpoint"] for call in calls] == [value[0] for value in checks]
    assert calls[0]["request"]["temperature"] == .65
    assert [m["role"] for m in calls[0]["request"]["messages"]] == ["system", "user"]
    second = calls[1]["request"]
    assert second["model"] == "fixture-critic"
    assert second["temperature"] == .6 and second["top_p"] == .95 and second["max_tokens"] == 900
    assert [m["role"] for m in second["messages"]] == ["user"]
    text = second["messages"][0]["content"]
    assert text.index("ROLE_RULE_SENTINEL") < text.index("任务资料开始") < text.index("TASK_DATA_SENTINEL")
    assert "任务资料结束" in text and llm.BASE_SYSTEM in text
    receipts = {record["role"]: record for record in
                (json.loads(path.read_text()) for path in tmp_path.glob("calls/*/receipt.json"))}
    receipt = receipts["independent_critic"]
    assert receipt["status"] == "succeeded" and receipt["profile"] == "critic"
    assert receipt["configured_model"] == receipt["response_model"] == "fixture-critic"
    assert receipt["endpoint"] == "http://127.0.0.1:8089/v1"
    assert receipt["attempts"][0]["response_model"] == "fixture-critic"
    assert receipt["attempts"][0]["usage"]["completion_tokens"] == 7
    assert receipt["parameters"]["system_prompt_mode"] == "user_prefix"
    assert receipt["parameters"]["temperature"] == .6 and receipt["parameters"]["max_tokens"] == 900
    assert "PRIVATE_VALUE_MUST_NOT_BE_SAVED" not in json.dumps(receipts)


@pytest.mark.parametrize("finish,returned,match", [
    ("length", "fixture-critic", "length"),
    ("stop", "unexpected-other-model", "does not match"),
])
def test_early_failure_retains_usage_and_actual_model_without_fallback(tmp_path, monkeypatch, finish, returned, match):
    def response(request):
        return {"model": returned, "usage": {"completion_tokens": 1800},
                "choices": [{"finish_reason": finish, "message": {"content": '{"ok":true}'}}]}
    calls, _ = mock_transport(monkeypatch, response)
    with pytest.raises(ValueError, match=match):
        llm.json_call(routing_config(), "independent_critic", "Review", {},
                      SCHEMA, tmp_path, max_tokens=1800)
    assert len(calls) == 1 and calls[0]["endpoint"] == "http://127.0.0.1:8089/v1"
    receipt = json.loads(next(tmp_path.glob("calls/*/receipt.json")).read_text())
    assert receipt["status"] == "failed"
    assert receipt["response_model"] == returned
    assert receipt["attempts"][0]["usage"]["completion_tokens"] == 1800
    assert receipt["attempts"][0]["finish_reason"] == finish
    assert receipt["attempts"][0]["response_model"] == returned


def test_unknown_profile_and_text_or_vision_misrouting_fail_before_transport(tmp_path, monkeypatch):
    calls, checks = mock_transport(monkeypatch)
    cfg = routing_config()
    cfg["llm_roles"]["screenwriter"] = "missing-profile"
    with pytest.raises(ValueError, match="Unknown LLM profile"):
        llm.json_call(cfg, "screenwriter", "Write", {}, SCHEMA, tmp_path)
    cfg = routing_config()
    cfg["llm_roles"]["visual_critic"] = "critic"
    with pytest.raises(ValueError, match="vision-capable"):
        factory_config.resolve_llm(cfg, "visual_critic")
    images = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="}}]
    for role, profile in (("independent_critic", "qwen"), ("story_critic", "critic")):
        cfg = routing_config()
        cfg["llm_roles"][role] = profile
        with pytest.raises(ValueError, match="Text critic roles cannot receive images"):
            llm._request(cfg, role, "Review", None, SCHEMA, tmp_path, content=images)
    cfg = routing_config()
    cfg["llm_profiles"]["critic"].pop("model")
    with pytest.raises(ValueError, match="requires model"):
        factory_config.resolve_llm(cfg, "independent_critic")
    assert calls == [] and checks == []


def test_cache_uses_resolved_profile_and_behavior_parameters_but_not_secret_values(tmp_path, monkeypatch):
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    count = []
    def produce(**kwargs):
        count.append(kwargs["role"])
        return {"ok": True}
    monkeypatch.setattr(llm, "json_call", produce)
    project = {"id": "face1234abcd", "slug": "factory-face1234abcd", "brief": "A bounded fixture"}
    cfg = routing_config()
    cfg["llm_roles"]["independent_critic"] = "qwen"
    def call(value):
        return schemas.cached_json_call(project, value, "independent_critic", "review",
                                        "Check evidence", {"text": "Fixture"}, SCHEMA)
    call(cfg); call(cfg)
    assert len(count) == 1
    cfg["llm_roles"]["independent_critic"] = "critic"
    call(cfg)
    assert len(count) == 2
    for field, value in (("temperature_override", .7), ("top_p", .8),
                         ("system_prompt_mode", "system"),
                         ("chat_template_kwargs", {"enable_thinking": True}),
                         ("revision", "new-model-revision")):
        changed = copy.deepcopy(cfg)
        changed["llm_profiles"]["critic"][field] = value
        call(changed)
    assert len(count) == 7
    monkeypatch.setenv("FACTORY_ROUTER_TEST_KEY", "SECRET_A")
    call(cfg)
    monkeypatch.setenv("FACTORY_ROUTER_TEST_KEY", "SECRET_B")
    call(cfg)
    assert len(count) == 7
    identity = json.dumps(factory_config.llm_identity(cfg, "independent_critic"))
    assert "SECRET_A" not in identity and "SECRET_B" not in identity
    assert "FACTORY_ROUTER_TEST_KEY" in identity


def test_role_inventory_and_unconfigured_legacy_request_are_compatible(tmp_path, monkeypatch):
    cfg = {"llm": routing_config()["llm"]}
    calls, checks = mock_transport(monkeypatch)
    rows = factory_config.list_roles(cfg)
    assert len(rows) == len(factory_config.ROLE_LABELS)
    assert {row["role"] for row in rows} == set(factory_config.ROLE_LABELS)
    assert "storyboard_director" in {row["role"] for row in rows}
    required = [row for row in rows if row["role"] not in factory_config.OPTIONAL_ROLES]
    assert all(row["profile"] == "default" and row["configured"] is False for row in required)
    optional = [row for row in rows if row["role"] in factory_config.OPTIONAL_ROLES]
    assert all(row["profile"] is None and row["enabled"] is False for row in optional)
    assert [row["role"] for row in rows if row["requires_vision"]] == ["visual_critic"]
    assert calls == checks == []
    assert llm.json_call(cfg, "creator", "Create", {}, SCHEMA, tmp_path) == {"ok": True}
    assert calls[0]["endpoint"] == "http://127.0.0.1:8090/v1"
    assert [m["role"] for m in calls[0]["request"]["messages"]] == ["system", "user"]


def test_changed_story_critic_reassesses_cached_target_without_rewriting_it():
    from tests import test_factory_creative as fixtures
    case = fixtures.CreativeContractTests(
        "test_approved_compact_target_reused_only_with_same_sources_policy_and_body")
    case.setUp()
    try:
        project = {**case.approved_project(), "script_generation": 20,
                   "story_feedback": "删除豆豆项圈，仅保留当晚归还"}
        first = creative.resolve_story_brief(project, case.config)
        before = len(case.calls)
        cfg = copy.deepcopy(case.config)
        cfg["llm_profiles"] = routing_config()["llm_profiles"]
        cfg["llm_roles"] = {"story_critic": "critic"}
        second = creative.resolve_story_brief(project, cfg)
        fresh_roles = [call["role"] for call in case.calls[before:]]
        assert fresh_roles == ["story_critic"]
        assert first["source_signature"] == second["source_signature"]
        assert first["reviewed_target_signature"] == second["reviewed_target_signature"]
        assert first["review_policy_signature"] != second["review_policy_signature"]
        before = len(case.calls)
        assert creative.resolve_story_brief(project, cfg) == second
        assert len(case.calls) == before
    finally:
        case.tearDown()
        case.doCleanups()


def test_visual_review_and_production_metadata_use_resolved_visual_model(tmp_path, monkeypatch):
    cfg = routing_config()
    cfg["llm"]["model"] = "unrouted-default"
    sample = tmp_path / "image.png"
    sample.write_bytes(b"mocked-image")
    monkeypatch.setattr(llm, "_image_url", lambda value: "data:image/png;base64,eA==")
    monkeypatch.setattr(llm, "_request", lambda *a, **k: {
        "status": "pass", "score": 88, "summary": "Fixture", "issues": []})
    result = llm.review_media({"slug": "factory-routing"}, {"id": "S01"}, sample, [], cfg)
    assert result["model"] == "fixture-qwen" and result["profile"] == "qwen"
    assert result["endpoint"] == "http://127.0.0.1:8090/v1"
    production = factory_config.production_config(
        {"budget": {"gpu_minutes": 10, "max_attempts": 3}}, cfg)
    assert production["semantic_model"] == result["model"]
    assert production["semantic_model_identity"] == factory_config.llm_identity(cfg, "visual_critic")


@pytest.fixture
def production_review_runner(tmp_path, monkeypatch):
    from factory import production
    monkeypatch.setattr(production, "ROOT", tmp_path)
    project = {"id": "a111b222c333", "slug": "factory-a111b222c333",
               "format": "motion_comic",
               "budget": {"gpu_minutes": 10, "wall_minutes": 20, "max_attempts": 1},
               "episode": {
                   "cast": {"female": "Fictional adult woman", "male": "Fictional adult man"},
                   "shots": [{"id": "S01", "seconds": 4,
                              "prompt": "An adult woman holds one key beside a closed door."}]}}
    def build(config, *, legacy=False):
        settings = config if legacy else factory_config.production_config(project, config)
        run = production._Run(project, settings, None, None)
        target = run._target("S01", run.folder / "shots/S01", run.shots[0]["prompt"],
                             shot=run.shots[0], review=True)
        return run, target
    return build


@pytest.mark.parametrize("field,value", [
    ("base_url", "http://127.0.0.1:8093/v1"),
    ("revision", "new-vision-weights"),
    ("temperature_override", .1),
])
def test_visual_profile_identity_invalidates_only_review_cache(
        production_review_runner, field, value):
    cfg = routing_config()
    original, target = production_review_runner(cfg)
    receipt = {"qa": {"semantic": {"status": "pass"}},
               "review_signature": target["review_signature"]}
    assert original._review_current(target, receipt)
    unchanged, same_target = production_review_runner(copy.deepcopy(cfg))
    assert unchanged._review_current(same_target, receipt)

    changed = copy.deepcopy(cfg)
    changed["llm_profiles"]["qwen"][field] = value
    revised, revised_target = production_review_runner(changed)
    assert revised.config["semantic_model"] == original.config["semantic_model"]
    # Existing generated pixels stay reusable; only the stale semantic verdict expires.
    assert revised_target["signature"] == target["signature"]
    assert revised_target["review_signature"] != target["review_signature"]
    assert revised._review_current(revised_target, receipt) is False
    fresh_receipt = {**receipt, "review_signature": revised_target["review_signature"]}
    assert revised._review_current(revised_target, fresh_receipt)


def test_visual_profile_secret_rotation_preserves_review_cache(production_review_runner, monkeypatch):
    cfg = routing_config()
    cfg["llm_profiles"]["qwen"]["api_key_env"] = "FACTORY_VLM_REVIEW_TEST_KEY"
    monkeypatch.setenv("FACTORY_VLM_REVIEW_TEST_KEY", "first-secret")
    first, target = production_review_runner(cfg)
    receipt = {"qa": {"semantic": {"status": "pass"}},
               "review_signature": target["review_signature"]}
    monkeypatch.setenv("FACTORY_VLM_REVIEW_TEST_KEY", "rotated-secret")
    second, same_target = production_review_runner(cfg)
    assert second._review_current(same_target, receipt)
    encoded = json.dumps(first.config["semantic_model_identity"])
    assert "first-secret" not in encoded and "rotated-secret" not in encoded


def test_manual_production_config_preserves_preidentity_review_cache(production_review_runner):
    run, target = production_review_runner({
        "semantic_enabled": True, "semantic_model": "legacy-vision",
        "semantic_review_revision": "legacy-v1"}, legacy=True)
    # Captured from the actual pre-fix constructor, rather than recomputed from new code.
    receipt = {"qa": {"semantic": {"status": "pass"}},
               "review_signature": "9b56a55373708e61f6e026b1efdcf81cc633d0160ad48adec6b29ee40f545148"}
    assert "semantic_model_identity" not in run.config
    assert run._review_current(target, receipt)
