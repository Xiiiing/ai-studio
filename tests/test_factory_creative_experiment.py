"""Synthetic, offline A/B evidence tests; no artistic or provider validation."""
import copy
import json
from pathlib import Path

import pytest

from factory import schemas, creative
from tests.test_factory_creative import fixture as shaped
from tests.test_factory_role_routing import routing_config


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Offline A/B must never execute models, network, GPU or subprocesses")
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("factory.llm.json_call", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)


@pytest.fixture
def experiment(tmp_path):
    from factory.creative_experiment import freeze
    from factory.prompt_registry import load_prompt
    corpus = json.loads((Path(__file__).parents[1] / "evaluation/creative/corpus-v1.json").read_text())
    corpus_path = write(tmp_path / "corpus.json", corpus)
    def build(name="run", role="screenwriter", split="development", **kwargs):
        return freeze(corpus_path, load_prompt(role), load_prompt(role), routing_config(),
                      tmp_path / name, tmp_path / (name + ".seal.json"),
                      role=role, samples=kwargs.pop("samples", 2), seed=kwargs.pop("seed", 731),
                      split=split, judge_id="fixture-judge", **kwargs)
    plan = build()
    return tmp_path, plan, build


def imported_values(tmp_path, plan, run_name="run"):
    from factory.creative_comparison import ingest_generation
    from factory.creative_evidence_io import digest
    for task in plan["tasks"]:
        request = json.loads((tmp_path / run_name / "requests" / (task["id"] + ".json")).read_text())
        value = shaped(request["output_schema"])
        # Keep each replicate distinct and every evidence quote verifiable.
        if plan["role"] == "screenwriter":
            value["synopsis"] = ("合成测试故事证据，不是模型作品。" * 10) + task["id"]
        output = write(tmp_path / "output.json", value)
        receipt = write(tmp_path / "receipt.json", {"status": "simulated", "output_sha256": digest(value)})
        ingest_generation(tmp_path / run_name, tmp_path / (run_name + ".seal.json"), task["id"], output, receipt,
                          evidence_mode="simulation")


def import_reviews(tmp_path, plan, *, baseline_score=6, candidate_score=7, omit_last=False, run_name="run"):
    from factory.creative_comparison import blind, ingest_review
    from factory.creative_experiment import pair_specs
    directory = tmp_path / ("blind" if run_name == "run" else run_name + "-blind")
    blind(tmp_path / run_name, tmp_path / (run_name + ".seal.json"), directory)
    pairs = pair_specs(plan)
    for pair in pairs[:-1] if omit_last else pairs:
        pack = json.loads((directory / (pair["id"] + ".json")).read_text())
        scores = {}
        for label in ("A", "B"):
            value = pack[label]
            quote = value.get("synopsis", "")[:24]
            score = baseline_score if pair["arms"][label] == "baseline" else candidate_score
            scores[label] = {key: {"score": score, "evidence": [{"pointer": "/synopsis", "quote": quote}]}
                             for key in schemas.SCORE_KEYS}
        review = {"pair_id": pair["id"], "pair_sha256": pack["pair_sha256"],
                  "judge_id": "fixture-judge", "evidence_mode": "simulation", "scores": scores}
        path = write(tmp_path / "review.json", review)
        ingest_review(tmp_path / run_name, tmp_path / (run_name + ".seal.json"), path)


@pytest.mark.parametrize("role", ["screenwriter", "storyboard_director"])
def test_frozen_tasks_cover_all_replicates_without_leaking_split_or_arm(experiment, role):
    tmp_path, initial, build = experiment
    plan = initial if role == "screenwriter" else build("director", role=role)
    directory = tmp_path / ("run" if role == "screenwriter" else "director")
    assert len(plan["tasks"]) == len(plan["cases"]) * 2 * 2
    for case in plan["cases"]:
        tasks = [t for t in plan["tasks"] if t["case_id"] == case["id"]]
        assert {t["replicate"] for t in tasks} == {1, 2}
        requests = [json.loads((directory / "requests" / (t["id"] + ".json")).read_text()) for t in tasks]
        assert all(r["payload"] == requests[0]["payload"] for r in requests)
        assert all(r["model_identity"] == requests[0]["model_identity"] for r in requests)
        assert all(not {"arm", "split", "negative_control", "rubric"}.intersection(r) for r in requests)
    assert not any(c["split"] == "holdout" for c in plan["cases"])


def test_no_model_results_is_not_run_and_never_a_quality_pass(experiment):
    from factory.creative_comparison import compare
    tmp_path, _, _ = experiment
    report = compare(tmp_path / "run", tmp_path / "run.seal.json")
    assert report["status"] == "not_run" and report["model_validation"] == "not_run"
    assert report["conclusion"] == "not_established" and report["summary"] is None
    assert report["missing_generations"] and not report["production_approval"]


def test_complete_blinded_simulation_remains_explicitly_not_real(experiment):
    from factory.creative_comparison import compare
    tmp_path, plan, _ = experiment
    imported_values(tmp_path, plan)
    import_reviews(tmp_path, plan)
    first = compare(tmp_path / "run", tmp_path / "run.seal.json")
    assert first == compare(tmp_path / "run", tmp_path / "run.seal.json")
    assert first["status"] == "simulated" and first["model_validation"] == "not_run"
    assert first["conclusion"] == "not_established"
    assert first["summary"]["observed_delta"] == 1
    assert first["replicates_per_arm"] == 2
    assert not first["production_approval"]
    for path in (tmp_path / "blind").glob("*.json"):
        value = json.loads(path.read_text())
        assert not {"baseline", "candidate", "prompts", "tasks", "model_identity"}.intersection(value)


def test_negative_results_are_preserved_and_missing_review_not_best_of(experiment):
    from factory.creative_comparison import compare
    tmp_path, plan, _ = experiment
    imported_values(tmp_path, plan)
    import_reviews(tmp_path, plan, baseline_score=8, candidate_score=4, omit_last=True)
    report = compare(tmp_path / "run", tmp_path / "run.seal.json")
    assert report["status"] == "incomplete" and report["summary"] is None
    assert len(report["missing_reviews"]) == 1


@pytest.mark.parametrize("target", ["plan", "request"])
def test_tampered_frozen_input_is_rejected(experiment, target):
    from factory.creative_experiment import verify
    tmp_path, plan, _ = experiment
    path = (tmp_path / "run/plan.json" if target == "plan" else
            tmp_path / "run/requests" / (plan["tasks"][0]["id"] + ".json"))
    value = json.loads(path.read_text()); value["tampered"] = True; write(path, value)
    with pytest.raises(ValueError, match="checksum|seal"):
        verify(tmp_path / "run", tmp_path / "run.seal.json")


def test_holdout_requires_explicit_frozen_release(experiment):
    tmp_path, _, build = experiment
    with pytest.raises(ValueError, match="holdout"):
        build("unreleased", split="holdout")
    plan = build("released", split="holdout", allow_holdout=True)
    assert all(case["split"] == "holdout" for case in plan["cases"])
    assert plan["holdout_policy"] == "one-sealed-comparison; retire-after-results-informed-tuning"


def test_output_and_seal_cannot_overwrite_or_self_authorize(experiment):
    from factory.creative_experiment import freeze
    from factory.prompt_registry import load_prompt
    tmp_path, _, build = experiment
    with pytest.raises((FileExistsError, ValueError)):
        build()
    prompt = load_prompt("screenwriter")
    with pytest.raises(ValueError, match="outside"):
        freeze(tmp_path / "corpus.json", prompt, prompt, routing_config(), tmp_path / "unsafe",
               tmp_path / "unsafe/seal.json", role="screenwriter", judge_id="fixture-judge")


def test_bad_output_schema_and_duplicate_import_do_not_replace_evidence(experiment):
    from factory.creative_comparison import ingest_generation
    from factory.creative_evidence_io import digest
    tmp_path, plan, _ = experiment
    task = plan["tasks"][0]
    output = write(tmp_path / "bad.json", {"artistically_good": True})
    receipt = write(tmp_path / "receipt.json", {"status": "simulated", "output_sha256": digest({"artistically_good": True})})
    with pytest.raises(ValueError, match="schema"):
        ingest_generation(tmp_path / "run", tmp_path / "run.seal.json", task["id"], output, receipt,
                          evidence_mode="simulation")
    imported_values(tmp_path, plan)
    before = (tmp_path / "run/results" / (task["id"] + ".json")).read_bytes()
    with pytest.raises((ValueError, FileExistsError)):
        ingest_generation(tmp_path / "run", tmp_path / "run.seal.json", task["id"], output, receipt,
                          evidence_mode="simulation")
    assert (tmp_path / "run/results" / (task["id"] + ".json")).read_bytes() == before
