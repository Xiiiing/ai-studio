"""Publication review regressions, using temporary files and synthetic receipts."""
import json
import shutil

import pytest

from tests.test_factory_creative_experiment import experiment, no_external_work
from tests.test_factory_creative_external_evidence import inputs


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
def test_offline_catalog_rejects_schema_references_before_validation(tmp_path, keyword):
    from factory.prompt_registry import load_prompt, PROMPT_ROOT
    root = tmp_path / "prompts"
    shutil.copytree(PROMPT_ROOT, root)
    path = root / "catalog.json"
    catalog = json.loads(path.read_text())
    catalog["prompts"][0]["input_schema"]["properties"]["project"] = {
        keyword: "https://invalid.example/private-schema.json"}
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match="self-contained"):
        load_prompt("creator", root=root)


def test_failed_receipt_cannot_substantiate_a_recorded_output(experiment):
    from factory.creative_comparison import _generation_check
    from factory.creative_experiment import PROTOCOL
    from factory.creative_evidence_io import digest
    tmp_path, plan, _ = experiment
    task, request, value, receipt = inputs(tmp_path, plan)
    receipt["status"] = "failed"
    record = {"protocol": PROTOCOL, "task_id": task["id"],
              "request_sha256": task["request_sha256"], "status": "recorded",
              "evidence_mode": "external", "value": value,
              "output_sha256": digest(value), "receipt": receipt}
    with pytest.raises(ValueError, match="status"):
        _generation_check(record, task, request, plan)


def test_oversized_evidence_is_rejected_before_creating_a_file(tmp_path):
    from factory.creative_evidence_io import write_new
    path = tmp_path / "oversized.json"
    with pytest.raises(ValueError, match="size bound"):
        write_new(path, {"value": "x" * (2 * 1024 * 1024)})
    assert not path.exists()


def test_holdout_cannot_be_released_to_another_plan_before_final_scores(experiment):
    tmp_path, _, build = experiment
    first = build("release-one", split="holdout", allow_holdout=True)
    assert first["split"] == "holdout"
    # No generation, review or compare is needed to expose frozen holdout inputs.
    with pytest.raises(ValueError, match="holdout already"):
        build("release-two", split="holdout", allow_holdout=True, seed=9)
    assert not (tmp_path / "release-two").exists()


def test_same_holdout_plan_cannot_be_released_under_a_new_seal(experiment):
    tmp_path, _, build = experiment
    build("release-one", split="holdout", allow_holdout=True)
    with pytest.raises(ValueError, match="holdout already"):
        build("release-again", split="holdout", allow_holdout=True)
    assert not (tmp_path / "release-again").exists()
