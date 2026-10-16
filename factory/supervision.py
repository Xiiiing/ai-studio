"""Optional, bounded producer delegation; never a fee or final-release authority."""
from __future__ import annotations

import copy
import hashlib
from pathlib import PurePosixPath
import re
import uuid

from jsonschema import validate
from studio.core import ROOT
from . import store
from .config import load_config, resolve_llm
from .creative_evidence_io import read_bytes
from .prompt_registry import load_prompt
from .providers import redact_secrets
from .schemas import cached_json_call, fingerprint, object_schema, text_schema, array_schema
from .attention_actions import generation, validate_gate

ROLE = "executive_producer"
KINDS = ("story", "style", "final")
BASIS = "text_and_record_review_not_film_review"
DECISION_SCHEMA = object_schema({
    "action": {"type": "string", "enum": ["select", "escalate"]},
    "choice": text_schema(0, 80), "rationale": text_schema(1, 1500),
    "risks": array_schema(text_schema(1, 500), 0, 6),
})
PUBLIC_KEYS = frozenset({"gate_id", "kind", "run_id", "status", "started_at", "finished_at", "input_signature",
    "profile_id", "requested_model", "model_source", "basis", "reason_code", "error_type", "proposal"})


def _object(value):
    return value if isinstance(value, dict) else {}


def _current(project, complete, generation):
    version = project.get(generation)
    return type(version) is int and version > 0 and type(project.get(complete)) is int and project[complete] == version


def _preview_valid(project, style):
    from .media_contract import preview_contract_valid
    if not preview_contract_valid(project, style):
        return False
    preview = _object(style.get("preview"))
    raw, expected = preview.get("path"), preview.get("sha256")
    if (not isinstance(raw, str) or "\\" in raw or not isinstance(expected, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected)):
        return False
    path = PurePosixPath(raw)
    ident = project.get("id", "")
    if (not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{12}", ident)
            or project.get("slug") != "factory-" + ident or path.is_absolute() or ".." in path.parts
            or path.parts[:4] != ("production", "factory-" + ident, "style_previews", style.get("id"))):
        return False
    try:
        return hashlib.sha256(read_bytes(ROOT / str(path), maximum=16 * 1024 * 1024)).hexdigest() == expected
    except (ValueError, OSError):
        return False


def context_for(project, gate):
    kind = gate["type"]
    if kind not in KINDS:
        raise ValueError("unsupported_gate")
    version = generation(project, kind)
    if type(version) is not int or version < 1 or gate["id"] != f"{project['id']}:{kind}:{version}":
        raise ValueError("stale_gate")
    validate_gate(project, gate)
    options = gate.get("options")
    if not isinstance(options, list) or len(options) > 12:
        raise ValueError("invalid_options")
    allowed = [v.get("id") for v in options if isinstance(v, dict)]
    if len(allowed) != len(options) or any(not isinstance(v, str) for v in allowed) or len(set(allowed)) != len(allowed):
        raise ValueError("invalid_options")
    candidates = []
    if kind == "story":
        if (not _current(project, "proposals_complete_generation", "proposal_generation")
                or _object(project.get("creation_review")).get("passed") is not True):
            raise ValueError("independent_review_not_current")
        source = project.get("proposals", [])
        fields = ("id", "title", "logline", "synopsis", "hook", "ending")
    elif kind == "style":
        if (not _current(project, "script_complete_generation", "script_generation")
                or project.get("style_previews_generation") != generation(project, "style")
                or _object(project.get("script_review")).get("passed") is not True):
            raise ValueError("independent_review_not_current")
        source, fields = project.get("styles", []), ("id", "name", "description", "prompt")
    else:
        if not _current(project, "delivery_complete_revision", "production_revision") or not project.get("delivery"):
            raise ValueError("delivery_not_current")
        source, fields = [], ()
    if not isinstance(source, list) or len(source) > 12:
        raise ValueError("invalid_candidates")
    identifiers = [item.get("id") for item in source if isinstance(item, dict)]
    if (len(identifiers) != len(source) or any(not isinstance(value, str) for value in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError("duplicate_or_invalid_candidate")
    for item in source:
        if not isinstance(item, dict) or item.get("id") not in allowed:
            continue
        if kind == "story" and item.get("eligible") is not True:
            continue
        if kind == "style" and not _preview_valid(project, item):
            continue
        validate_gate(project, gate, item["id"])
        candidate = {key: item[key][:1800] for key in fields if isinstance(item.get(key), str)}
        if kind == "style":
            candidate["preview_sha256"] = item["preview"]["sha256"]
            if project.get("creative_contract"):
                semantic = item["preview"]["qa"]["semantic"]
                candidate["visual_review"] = {key: copy.deepcopy(semantic[key]) for key in
                    ("status", "summary", "style_alignment", "issues") if key in semantic}
        candidates.append(candidate)
    if kind != "final" and not candidates:
        raise ValueError("no_verified_candidate")
    if len({v["id"] for v in candidates}) != len(candidates):
        raise ValueError("duplicate_candidate")
    shots = project.get("shots", [])
    shots = shots if isinstance(shots, list) else []
    context = {
        "project": {key: copy.deepcopy(project.get(key)) for key in (
            "id", "brief", "format", "duration_seconds", "creation_mode", "production_profile")},
        "gate": {"id": gate["id"], "type": kind}, "candidates": candidates,
        "review_summary": {"story_passed": _object(project.get("creation_review")).get("passed") is True,
            "script_passed": _object(project.get("script_review")).get("passed") is True,
            "shot_count": len(shots), "delivery_current": _current(project, "delivery_complete_revision", "production_revision"),
            "semantic_pass_count": sum(_object(_object(v.get("qa")).get("semantic")).get("status") == "pass"
                                       for v in shots[:120] if isinstance(v, dict))},
        "constraints": {"basis": BASIS, "allowed_actions": ["escalate"] if kind == "final" else ["select", "escalate"],
            "forbidden": ["approve_final", "authorize_cost", "change_budget", "override_reviews", "publish", "unbounded_retry"]},
    }
    if "creative_contract" in project:
        from .creative_contract import resolve as resolve_contract
        context["project"]["creative_contract"] = resolve_contract(project)
    return context


def _save(db, project, record):
    project.setdefault("supervision", {})[record["kind"]] = record
    store._write(db, project)
    store._event(db, project["id"], "总制片记录 · " + record["status"], "supervision",
                 {k: v for k, v in record.items() if k in PUBLIC_KEYS})


def _claim(pid, gate, profile, configuration_problem):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        current = next((v for v in project["decisions"] if v["id"] == gate["id"]), None)
        if (project.get("team_mode", "guided") != "delegated" or current != gate
                or current.get("status") != "pending" or project.get("pause_requested")
                or project.get("status") != "awaiting_" + gate["type"]):
            return None
        previous = _object(project.get("supervision")).get(gate["type"])
        if previous and previous.get("gate_id") == gate["id"]:
            return None  # At most one request per gate, including an uncertain interrupted request.
        problem, payload = configuration_problem, None
        try:
            payload = context_for(project, gate)
        except (ValueError, KeyError, TypeError) as exc:
            problem = str(exc) if type(exc) is ValueError else "invalid_gate_context"
        record = {"gate_id": gate["id"], "kind": gate["type"], "run_id": uuid.uuid4().hex,
                  "status": "manual" if problem else "requested", "started_at": store.now(),
                  "finished_at": store.now() if problem else None, "reason_code": problem,
                  "input_signature": fingerprint(payload) if payload else None,
                  "profile_id": profile.get("profile"), "requested_model": profile.get("model"),
                  "model_source": "request", "basis": BASIS}
        _save(db, project, record)
        return None if problem else (project, payload, copy.deepcopy(record), project["revision"])


def _settle(pid, gate, claim, proposal, error_type):
    _, payload, token, expected_revision = claim
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        project = store._read(db, pid)
        record = _object(project.get("supervision")).get(gate["type"])
        if not record or record.get("run_id") != token["run_id"] or record.get("status") != "requested":
            return None
        current = next((v for v in project["decisions"] if v["id"] == gate["id"]), None)
        stable = (project["revision"] == expected_revision and current == gate
                  and project.get("team_mode") == "delegated" and not project.get("pause_requested")
                  and project.get("status") == "awaiting_" + gate["type"])
        if stable:
            try:
                stable = fingerprint(context_for(project, current)) == token["input_signature"]
            except (ValueError, KeyError, TypeError):
                stable = False
        record.update(finished_at=store.now(), error_type=error_type)
        if not stable:
            record.update(status="superseded", reason_code="project_or_evidence_changed")
            _save(db, project, record)
            return None  # A human's answer/pause and all project fields are preserved.
        allowed = {v["id"] for v in payload["candidates"]}
        select = bool(proposal and proposal["action"] == "select" and gate["type"] in {"story", "style"}
                      and proposal["choice"] in allowed)
        escalate = bool(proposal and proposal["action"] == "escalate" and proposal["choice"] == "")
        if not select and not escalate:
            record.update(status="manual", reason_code="model_failure" if error_type else "invalid_selection")
            _save(db, project, record)
            return None
        if select:
            try:
                validate_gate(project, current, proposal["choice"])
            except ValueError:
                record.update(status="manual", reason_code="invalid_selection")
                _save(db, project, record)
                return None
        record["proposal"] = proposal
        if escalate:
            record.update(status="advisory" if gate["type"] == "final" else "manual", reason_code="human_review_required")
            _save(db, project, record)
            return None
        feedback = project.get(gate["type"] + "_feedback", "")
        feedback = feedback[:4000] if isinstance(feedback, str) else ""
        answer = {"gate_id": gate["id"], "type": gate["type"], "choice": proposal["choice"],
                  "feedback": feedback, "actor": ROLE, "at": store.now(), "supervisor_receipt": token["run_id"]}
        project["answers"][gate["id"]] = answer
        current.update(status="answered", answer=answer)
        project.update(status="queued", error=None)
        record.update(status="selected", reason_code=None)
        _save(db, project, record)
        store._event(db, pid, "已记录总制片的授权范围内选择", "decision", answer)
        return copy.deepcopy(answer)  # The current graph invocation applies the normal decision receipt.


def supervise_gate(pid, gate, config=None):
    if gate.get("type") not in KINDS or store.get(pid, False).get("team_mode", "guided") != "delegated":
        return None
    config = load_config() if config is None else config
    profile, problem = {}, None
    try:
        if ROLE not in config.get("llm_roles", {}):
            raise ValueError("missing_explicit_binding")
        profile = resolve_llm(config, ROLE)
        if not profile.get("configured") or not profile.get("enabled", True):
            raise ValueError("disabled_binding")
    except (ValueError, KeyError, TypeError):
        profile, problem = {}, "supervisor_not_configured"
    claim = _claim(pid, gate, profile, problem)
    if claim is None:
        return None
    project, payload, _, _ = claim
    proposal, error_type = None, None
    from .execution_context import current_execution
    context_token = current_execution.set({"project_id": pid, "stage": gate["type"] + "_decision",
                                            "run_id": claim[2]["run_id"]})
    try:
        proposal = cached_json_call(project, config, ROLE, "supervisor_" + gate["type"], load_prompt(ROLE),
                                    payload, DECISION_SCHEMA, max_tokens=2048, temperature=0.2)
        validate(proposal, DECISION_SCHEMA)
        proposal = redact_secrets(proposal, profile)
        validate(proposal, DECISION_SCHEMA)
    except Exception as exc:
        proposal, error_type = None, type(exc).__name__[:100]
    finally:
        current_execution.reset(context_token)
    return _settle(pid, gate, claim, proposal, error_type)


def snapshot(project):
    records = []
    for kind in KINDS:
        record = _object(_object(project.get("supervision")).get(kind))
        if record:
            records.append({**{k: copy.deepcopy(v) for k, v in record.items() if k in PUBLIC_KEYS},
                "current_gate_version": record.get("gate_id") == f"{project.get('id')}:{kind}:{generation(project, kind)}"})
    return {"mode": project.get("team_mode", "guided"), "role": ROLE, "basis": BASIS,
            "automatic_choices": ["story", "style"], "human_required": ["budget", "final", "platform_release"],
            "records": records}
