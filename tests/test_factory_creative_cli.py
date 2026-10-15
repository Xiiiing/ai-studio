"""Offline CLI reports and malformed prompt-contract regression tests."""
import json
import shutil

import pytest

from tests.test_factory_creative_experiment import experiment, no_external_work


@pytest.mark.parametrize("schema", [True, {"type": "invented"}, {"type": "object", "additionalProperties": True}])
def test_malformed_local_prompt_contract_fails_as_validation_error(tmp_path, schema):
    from factory.prompt_registry import load_prompt, PROMPT_ROOT
    root = tmp_path / "prompts"
    shutil.copytree(PROMPT_ROOT, root)
    path = root / "catalog.json"
    catalog = json.loads(path.read_text())
    catalog["prompts"][0]["input_schema"] = schema
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match="schema"):
        load_prompt(catalog["prompts"][0]["role"], root=root)


def test_cli_not_run_report_is_independent_and_never_overwritten(experiment, capsys):
    from factory.creative_experiment_cli import main
    tmp_path, plan, _ = experiment
    common = [str(tmp_path / "run"), "--seal", str(tmp_path / "run.seal.json")]
    assert main(["verify", *common]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"
    args = ["compare", *common, "--output", str(tmp_path / "report")]
    assert main(args) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "not_run" and report["conclusion"] == "not_established"
    before = (tmp_path / "report/report.json").read_bytes()
    assert "not_run" in (tmp_path / "report/summary.md").read_text()
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    assert (tmp_path / "report/report.json").read_bytes() == before
