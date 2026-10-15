"""Adversarial synthetic evidence; successful imports are not real-model runs."""
import copy
import json
from pathlib import Path

import pytest

from tests.test_factory_creative_experiment import (
    experiment, no_external_work, imported_values, import_reviews, write,
)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"n":NaN}', b'{"n":1e999}', b'[]'])
def test_strict_evidence_json_rejects_ambiguous_or_nonfinite_values(tmp_path, raw):
    from factory.creative_evidence_io import read_json
    path = tmp_path / "input.json"; path.write_bytes(raw)
    with pytest.raises(ValueError):
        read_json(path)


def test_all_evidence_io_refuses_symlink_parents_and_traversal(tmp_path):
    from factory.creative_evidence_io import read_json, write_new, new_directory
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    write(tmp_path / "real/input.json", {"data": True})
    with pytest.raises(ValueError, match="symlink"):
        read_json(tmp_path / "link/input.json")
    with pytest.raises(ValueError, match="symlink"):
        write_new(tmp_path / "link/output.json", {"data": True})
    with pytest.raises(ValueError, match="traversal"):
        new_directory(tmp_path / "real/../escaped")
    assert not (tmp_path / "escaped").exists()


def test_oversized_evidence_and_production_output_are_rejected(tmp_path):
    from factory.creative_evidence_io import read_json, new_directory
    path = write(tmp_path / "large.json", {"data": "X" * 5000})
    with pytest.raises(ValueError, match="size"):
        read_json(path, maximum=100)
    (tmp_path / "production").mkdir()
    with pytest.raises(ValueError, match="production"):
        new_directory(tmp_path / "production/experiment")


def test_quote_score_and_judge_identity_tampering_is_rejected(experiment):
    from factory.creative_comparison import ingest_review
    tmp_path, plan, _ = experiment
    imported_values(tmp_path, plan); import_reviews(tmp_path, plan, omit_last=True)
    template = json.loads((tmp_path / "review.json").read_text())
    for key, changed in (("quote", "invented evidence"), ("score", True), ("judge_id", "another-judge")):
        value = copy.deepcopy(template)
        if key == "quote": value["scores"]["A"]["hook"]["evidence"][0]["quote"] = changed
        elif key == "score": value["scores"]["A"]["hook"]["score"] = changed
        else: value[key] = changed
        with pytest.raises(ValueError):
            ingest_review(tmp_path / "run", tmp_path / "run.seal.json", write(tmp_path / "bad-review.json", value))


def test_output_hash_and_frozen_source_changes_are_not_silently_accepted(experiment, monkeypatch):
    from factory.creative_comparison import compare
    from factory import creative_experiment as module
    tmp_path, plan, _ = experiment
    imported_values(tmp_path, plan)
    path = tmp_path / "run/results" / (plan["tasks"][0]["id"] + ".json")
    value = json.loads(path.read_text()); value["value"]["synopsis"] += "tampered"; write(path, value)
    with pytest.raises(ValueError, match="checksum"):
        compare(tmp_path / "run", tmp_path / "run.seal.json")
    monkeypatch.setattr(module, "source_identity", lambda: {"sha256": "0" * 64})
    with pytest.raises(ValueError, match="source changed"):
        module.verify(tmp_path / "run", tmp_path / "run.seal.json")


def test_model_judge_cannot_be_substituted_without_a_receipt(experiment):
    from factory.creative_comparison import ingest_review
    from tests.test_factory_role_routing import routing_config
    tmp_path, _, build = experiment
    plan = build("model", judge_config=routing_config())
    imported_values(tmp_path, plan, "model")
    import_reviews(tmp_path, plan, run_name="model", omit_last=True)
    value = json.loads((tmp_path / "review.json").read_text())
    for mode in ("human", "external"):
        value["evidence_mode"] = mode
        with pytest.raises(ValueError, match="model|receipt"):
            ingest_review(tmp_path / "model", tmp_path / "model.seal.json", write(tmp_path / "bad.json", value))


def test_holdout_cannot_be_reused_after_unrelated_development_edits(experiment):
    from factory.creative_comparison import compare
    tmp_path, _, build = experiment
    first = build("holdout-one", split="holdout", allow_holdout=True)
    imported_values(tmp_path, first, "holdout-one")
    import_reviews(tmp_path, first, run_name="holdout-one")
    report = compare(tmp_path / "holdout-one", tmp_path / "holdout-one.seal.json")
    assert report == compare(tmp_path / "holdout-one", tmp_path / "holdout-one.seal.json")
    corpus = json.loads((tmp_path / "corpus.json").read_text())
    corpus["cases"][0]["genre"] = "changed-development-label"
    write(tmp_path / "corpus.json", corpus)
    second = build("holdout-two", split="holdout", allow_holdout=True, seed=99)
    imported_values(tmp_path, second, "holdout-two")
    import_reviews(tmp_path, second, run_name="holdout-two")
    with pytest.raises(ValueError, match="holdout already consumed"):
        compare(tmp_path / "holdout-two", tmp_path / "holdout-two.seal.json")


def test_director_fixture_cannot_expand_editable_fields_or_invent_shot_ids(experiment):
    tmp_path, _, build = experiment
    corpus = json.loads((tmp_path / "corpus.json").read_text())
    corpus["cases"][0]["inputs"]["storyboard_director"]["editable_fields"].append("action")
    write(tmp_path / "corpus.json", corpus)
    with pytest.raises(ValueError, match="editable fields"):
        build("bad-director", role="storyboard_director")


def test_scores_retain_negative_delta_and_dimension_evidence(experiment):
    from factory.creative_comparison import compare
    tmp_path, plan, _ = experiment
    imported_values(tmp_path, plan)
    import_reviews(tmp_path, plan, baseline_score=8, candidate_score=4)
    report = compare(tmp_path / "run", tmp_path / "run.seal.json")
    assert report["summary"]["observed_delta"] == -4
    assert report["summary"]["regressed_cases"] == [case["id"] for case in plan["cases"]]
    assert set(report["summary"]["dimensions"]) == {"hook", "causality", "motivation", "originality", "closure", "feasibility"}
    assert all(row["observed_delta"] == -4 for row in report["summary"]["dimensions"].values())
    assert report["conclusion"] == "not_established"
