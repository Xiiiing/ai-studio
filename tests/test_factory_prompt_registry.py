"""Offline prompt identity/cache contracts, never a creative-quality claim."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from factory import creative, llm, schemas, storyboard
from factory.config import llm_identity
from factory.profile_creative import adapt_system
from tests.test_factory_role_routing import routing_config, mock_transport, SCHEMA


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Prompt tests cannot call a provider or GPU")
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)


@pytest.mark.parametrize("role,module,name", [
    ("creator", creative, "CREATOR_SYSTEM"),
    ("independent_critic", creative, "CRITIC_SYSTEM"),
    ("screenwriter", creative, "SCRIPT_SYSTEM"),
    ("visual_designer", creative, "STYLE_SYSTEM"),
    ("storyboard_director", storyboard, "DIRECTOR_SYSTEM"),
])
def test_extracted_v1_is_byte_equal_to_frozen_main(role, module, name):
    from factory.prompt_registry import load_prompt, PromptText
    frozen = json.loads((Path(__file__).parent / "fixtures/prompt-baseline-v1.json").read_text())
    value = load_prompt(role)
    assert isinstance(value, PromptText)
    assert str(value) == str(getattr(module, name))
    assert hashlib.sha256(value.encode()).hexdigest() == frozen["prompts"][role]
    assert value.spec.version == "1.0.0"
    assert value.spec.role == role and value.spec.output_schema_refs
    assert not hasattr(value.spec, "model")
    assert copy.deepcopy(value).spec == value.spec
    assert json.dumps(value, ensure_ascii=False) == json.dumps(str(value), ensure_ascii=False)


def test_profile_and_writer_derivation_keep_actual_identity():
    from factory.prompt_registry import load_prompt, derive_prompt, prompt_receipt
    from factory.production_profiles import normalize
    project = {"production_profile": normalize({"preset": "compact6"})}
    value = load_prompt("screenwriter")
    derived = derive_prompt(value, str(value).replace("编剧兼分镜导演", "编剧", 1), "separate_storyboard")
    rendered = adapt_system(derived, project)
    assert str(rendered) == adapt_system(str(derived), project)
    assert str(adapt_system(rendered, project)) == str(rendered)
    assert adapt_system(rendered, project).transforms == rendered.transforms
    meta = prompt_receipt(rendered, "screenwriter", {}, SCHEMA)
    assert meta["version"] == "1.0.0" and meta["status"] == "registered"
    assert meta["rendered_sha256"] != meta["template_sha256"]
    assert meta["transforms"] == ["separate_storyboard", "production_profile"]


def test_unknown_plain_prompt_is_not_relabelled_using_current_role():
    from factory.prompt_registry import prompt_receipt
    meta = prompt_receipt("Historical private rules", "screenwriter", {}, SCHEMA)
    assert meta["status"] == "unregistered"
    assert meta["version"] is None and meta["template_sha256"] is None
    assert "Historical private rules" not in json.dumps(meta)


@pytest.mark.parametrize("role,version", [("../creator", None), ("creator", "../1.0.0"),
                                          ("creator", "9.0.0"), ("unknown", None)])
def test_registry_refuses_unknown_or_unsafe_selectors(role, version):
    from factory.prompt_registry import load_prompt
    with pytest.raises(ValueError):
        load_prompt(role, version)


def test_tampered_template_and_symlink_are_rejected(tmp_path):
    from factory.prompt_registry import load_prompt, PROMPT_ROOT
    import shutil
    root = tmp_path / "prompts"
    shutil.copytree(PROMPT_ROOT, root)
    file = root / "creator/1.0.0.txt"
    file.write_text(file.read_text() + "\nChanged without a version")
    with pytest.raises(ValueError, match="checksum"):
        load_prompt("creator", root=root)
    file.unlink()
    file.symlink_to(PROMPT_ROOT / "creator/1.0.0.txt")
    with pytest.raises(ValueError, match="symlink"):
        load_prompt("creator", root=root)


def test_role_or_unapproved_context_rejected_before_any_request(tmp_path, monkeypatch):
    from factory.prompt_registry import load_prompt
    calls, checks = mock_transport(monkeypatch)
    with pytest.raises(ValueError, match="role"):
        llm.json_call(routing_config(), "independent_critic", load_prompt("creator"), {}, SCHEMA, tmp_path)
    with pytest.raises(ValueError, match="context"):
        llm.json_call(routing_config(), "creator", load_prompt("creator"),
                      {"private_credential_dump": "never send"}, SCHEMA, tmp_path)
    assert not calls and not checks and not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", [False, True])
def test_actual_call_receipt_has_prompt_and_wire_hash(tmp_path, monkeypatch, failure):
    from factory.prompt_registry import load_prompt
    calls, _ = mock_transport(monkeypatch)
    if failure:
        monkeypatch.setattr(llm, "check_model", lambda *a: (_ for _ in ()).throw(ValueError("fixture failure")))
    prompt = load_prompt("creator")
    if failure:
        with pytest.raises(ValueError, match="fixture failure"):
            llm.json_call(routing_config(), "creator", prompt, {}, SCHEMA, tmp_path)
    else:
        assert llm.json_call(routing_config(), "creator", prompt, {}, SCHEMA, tmp_path) == {"ok": True}
    receipt = json.loads(next(tmp_path.glob("calls/*/receipt.json")).read_text())
    assert receipt["prompt"]["version"] == "1.0.0"
    assert receipt["prompt"]["template_sha256"] == prompt.spec.template_sha256
    assert receipt["prompt"]["wire_system_sha256"] == hashlib.sha256((str(prompt) + llm.BASE_SYSTEM).encode()).hexdigest()
    assert receipt["status"] == ("failed" if failure else "succeeded")
    assert str(prompt) not in json.dumps(receipt, ensure_ascii=False)


def test_legacy_cache_is_reused_unchanged_without_fabricating_version(tmp_path, monkeypatch):
    from factory.prompt_registry import load_prompt
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    project = {"id": "face1234abcd", "slug": "factory-face1234abcd", "brief": "Synthetic cache"}
    config, payload, stage = routing_config(), {}, "proposals-1"
    prompt = load_prompt("creator")
    signature = schemas.fingerprint({"schema_version": schemas.SCHEMA_VERSION,
        "context": schemas.project_context(project), "model": llm_identity(config, "creator"),
        "role": "creator", "stage": stage, "system": str(prompt), "payload": payload,
        "schema": SCHEMA, "max_tokens": 4096, "temperature": .65})
    cache = schemas.planning_dir(project) / "cache" / (stage + "-" + signature[:24] + ".json")
    cache.parent.mkdir(parents=True)
    original = json.dumps({"signature": signature, "generated_at": "historical", "value": {"ok": True}}).encode()
    cache.write_bytes(original)
    monkeypatch.setattr(llm, "json_call", lambda **k: pytest.fail("must reuse the exact legacy key"))
    assert schemas.cached_json_call(project, config, "creator", stage, prompt, payload, SCHEMA) == {"ok": True}
    assert cache.read_bytes() == original


def test_prompt_content_changes_only_its_cached_step(tmp_path, monkeypatch):
    from factory.prompt_registry import load_prompt, derive_prompt
    monkeypatch.setattr(schemas, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(llm, "json_call", lambda **k: (calls.append(k), {"ok": True})[1])
    project = {"id": "face1234abcd", "slug": "factory-face1234abcd", "brief": "Synthetic cache"}
    original = copy.deepcopy(project)
    cfg = routing_config()
    def call(role, prompt):
        return schemas.cached_json_call(project, cfg, role, role + "-1", prompt, {}, SCHEMA)
    creator, critic = load_prompt("creator"), load_prompt("independent_critic")
    call("creator", creator); call("independent_critic", critic)
    call("creator", derive_prompt(creator, str(creator) + "\nFixture change", "test_change"))
    call("independent_critic", critic)
    assert len(calls) == 3 and project == original
    caches = list(schemas.planning_dir(project).glob("cache/*.json"))
    assert len(caches) == 3
    assert all(json.loads(path.read_text())["prompt"]["version"] == "1.0.0" for path in caches)


def test_prompt_catalog_does_not_override_producer_priority():
    from factory.prompt_registry import load_prompt
    value = creative._producer_feedback({"story_feedback": "old", "script_feedback": "new"}, "newest")
    assert value["priority_order"] == ["revision_feedback", "script_feedback", "story_feedback", "selected_proposal"]
    assert "priority_order" in load_prompt("screenwriter")
    assert "不能扩大镜号或字段修改权限" in load_prompt("storyboard_director")
    assert "不改变人物身份和剧情" in load_prompt("visual_designer")
