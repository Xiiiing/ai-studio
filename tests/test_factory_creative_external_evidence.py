"""Synthetic external-receipt contract tests. No provider authenticity is claimed."""
import copy
import hashlib
import json

import pytest

from tests.test_factory_creative_experiment import experiment, no_external_work, imported_values, write
from tests.test_factory_creative import fixture as shaped
from tests.test_factory_role_routing import routing_config


def parameters(request, identity):
    override = identity.get("temperature_override")
    return {"max_tokens": request["max_tokens"], "temperature": request["temperature"] if override is None else override,
            "top_p": identity["top_p"], "response_format": identity["response_format"],
            "system_prompt_mode": identity["system_prompt_mode"]}


def synthetic_receipt(plan, task, request, value):
    from factory.creative_comparison import _wire_hash
    from factory.creative_evidence_io import digest
    return {"fixture_only": True, "status": "succeeded", "call_id": "synthetic_" + task["id"],
            "model_identity": plan["model_identity"], "config_revision": plan["config_revision"],
            "parameters": parameters(request, plan["model_identity"]),
            "prompt": {**task["prompt"], "wire_system_sha256": _wire_hash(request["system"], request["output_schema"], plan["model_identity"])},
            "output_sha256": digest(value), "output_digest_format": "canonical-json-utf8.v1"}


def inputs(tmp_path, plan, index=0):
    task = plan["tasks"][index]
    request = json.loads((tmp_path / "run/requests" / (task["id"] + ".json")).read_text())
    value = shaped(request["output_schema"])
    return task, request, value, synthetic_receipt(plan, task, request, value)


def test_externally_reported_call_is_content_bound_not_a_real_validation(experiment):
    from factory.creative_comparison import ingest_generation, compare
    tmp_path, plan, _ = experiment
    task, request, value, receipt = inputs(tmp_path, plan)
    result = ingest_generation(tmp_path / "run", tmp_path / "run.seal.json", task["id"],
                               write(tmp_path / "value.json", value), write(tmp_path / "receipt.json", receipt),
                               evidence_mode="external")
    assert result["status"] == "recorded"
    report = compare(tmp_path / "run", tmp_path / "run.seal.json")
    assert report["status"] == "incomplete" and report["conclusion"] == "not_established"
    assert report["summary"] is None


@pytest.mark.parametrize("mutation", ["model", "config", "system", "version", "sampling", "output"])
def test_changed_model_prompt_sampling_or_output_invalidates_external_evidence(experiment, mutation):
    from factory.creative_comparison import ingest_generation
    tmp_path, plan, _ = experiment
    task, request, value, receipt = inputs(tmp_path, plan)
    if mutation == "model": receipt["model_identity"] = {"model": "another-model"}
    elif mutation == "config": receipt["config_revision"] = "another-config"
    elif mutation == "system": receipt["prompt"]["wire_system_sha256"] = "0" * 64
    elif mutation == "version": receipt["prompt"]["version"] = "2.0.0"
    elif mutation == "sampling": receipt["parameters"]["temperature"] = 1.99
    else: receipt["output_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        ingest_generation(tmp_path / "run", tmp_path / "run.seal.json", task["id"],
                          write(tmp_path / "value.json", value), write(tmp_path / "receipt.json", receipt),
                          evidence_mode="external")
    assert not list((tmp_path / "run/results").iterdir())


def test_same_call_cannot_be_counted_twice_as_independent_samples(experiment):
    from factory.creative_comparison import ingest_generation
    tmp_path, plan, _ = experiment
    for index in (0, 1):
        task, request, value, receipt = inputs(tmp_path, plan, index)
        receipt["call_id"] = "synthetic_shared_call"
        def put():
            return ingest_generation(tmp_path / "run", tmp_path / "run.seal.json", task["id"],
                                     write(tmp_path / "value.json", value), write(tmp_path / "receipt.json", receipt),
                                     evidence_mode="external")
        if index == 0: put()
        else:
            with pytest.raises(ValueError, match="independent replicate"):
                put()


def test_content_bound_old_version_is_kept_unknown_and_failure_is_retained(experiment):
    from factory.creative_comparison import ingest_generation, compare
    tmp_path, plan, _ = experiment
    task, request, value, receipt = inputs(tmp_path, plan)
    receipt["prompt"].update(status="unregistered", version=None, template_sha256=None, contract_sha256=None, transforms=[])
    receipt["status"] = "failed"
    receipt["error"] = "PRIVATE_FIXTURE_ERROR_TEXT"
    ingest_generation(tmp_path / "run", tmp_path / "run.seal.json", task["id"], None,
                      write(tmp_path / "receipt.json", receipt), evidence_mode="external")
    record = json.loads((tmp_path / "run/results" / (task["id"] + ".json")).read_text())
    assert record["receipt"]["prompt"]["version"] is None
    report = compare(tmp_path / "run", tmp_path / "run.seal.json")
    assert len(report["failed_generations"]) == 1 and report["summary"] is None
    assert "PRIVATE_FIXTURE_ERROR_TEXT" not in json.dumps(report)


def test_model_review_receipt_binds_exact_pair_schema_and_judge_config(experiment):
    from factory.creative_comparison import blind, ingest_review, review_schema, JUDGE_SYSTEM, _wire_hash
    from factory.creative_evidence_io import digest
    from factory.schemas import SCORE_KEYS
    tmp_path, _, build = experiment
    plan = build("model-judge", judge_config=routing_config())
    imported_values(tmp_path, plan, "model-judge")
    blind(tmp_path / "model-judge", tmp_path / "model-judge.seal.json", tmp_path / "anonymous")
    pair_path = next(p for p in (tmp_path / "anonymous").glob("*.json") if p.name != "index.json")
    pack = json.loads(pair_path.read_text())
    review = {"pair_id": pack["pair_id"], "pair_sha256": pack["pair_sha256"], "judge_id": plan["judge_id"],
              "evidence_mode": "external", "scores": {label: {key: {"score": 6,
                  "evidence": [{"pointer": "/synopsis", "quote": pack[label]["synopsis"][:30]}]}
                  for key in SCORE_KEYS} for label in ("A", "B")}}
    judge = plan["judge_identity"]; schema = review_schema(pack, plan["judge_id"])
    receipt = {"fixture_only": True, "status": "succeeded", "call_id": "synthetic_judge_call",
               "model_identity": judge["model_identity"], "config_revision": judge["config_revision"],
               "parameters": parameters(judge["parameters"], judge["model_identity"]),
               "output_sha256": digest(review), "prompt": {"role": "independent_critic",
                   "input_sha256": digest(pack), "rendered_sha256": hashlib.sha256(JUDGE_SYSTEM.encode()).hexdigest(),
                   "output_schema_sha256": digest(schema), "wire_system_sha256": _wire_hash(JUDGE_SYSTEM, schema, judge["model_identity"])}}
    result = ingest_review(tmp_path / "model-judge", tmp_path / "model-judge.seal.json",
                           write(tmp_path / "review.json", review), receipt_path=write(tmp_path / "judge-receipt.json", receipt))
    assert result["status"] == "recorded" and result["production_approval"] is False
    receipt["parameters"]["temperature"] = 1.99
    with pytest.raises(ValueError, match="sampling"):
        ingest_review(tmp_path / "model-judge", tmp_path / "model-judge.seal.json",
                      tmp_path / "review.json", receipt_path=write(tmp_path / "judge-receipt.json", receipt))
