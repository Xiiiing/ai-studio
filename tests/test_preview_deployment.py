"""Preview deployment guardrails; never install units or connect to a model."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import deploy_preview as preview


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "config").mkdir(parents=True)
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").touch()
    (root / "ui/factory/dist").mkdir(parents=True)
    (root / "ui/factory/dist/index.html").write_text("preview")
    example = {"llm": {"enabled": True}, "llm_profiles": {"writer": {"enabled": True}},
               "video_profiles": {"cloud": {"enabled": True}},
               "production": {"gpu_cards": [0]}, "web": {"port": 8787}}
    (root / "config/factory.example.json").write_text(json.dumps(example))
    area = root / "runtime/cami-preview"
    area.mkdir(parents=True)
    home = tmp_path / "home"
    monkeypatch.setattr(preview, "ROOT", root)
    monkeypatch.setattr(preview, "WORKSPACE", area)
    monkeypatch.setattr(Path, "home", lambda: home)
    return root, area, home


def test_first_configuration_disables_model_and_gpu_execution(workspace):
    _, area, _ = workspace
    path = preview.configuration(8788)
    config = json.loads(path.read_text())
    assert config["web"] == {"host": "127.0.0.1", "port": 8788}
    assert not config["llm"]["enabled"]
    assert not config["llm_profiles"]["writer"]["enabled"]
    assert not config["video_profiles"]["cloud"]["enabled"]
    assert config["production"]["gpu_cards"] == []
    assert path.stat().st_mode & 0o777 == 0o600
    config["operator_note"] = "Keep edited preview configuration"
    path.write_text(json.dumps(config))
    assert json.loads(preview.configuration(8789).read_text())["operator_note"] == config["operator_note"]
    assert path.parent == area


def test_foreign_unit_is_rejected_without_writing_configuration(workspace, monkeypatch):
    _, area, home = workspace
    unit = home / ".config/systemd/user" / preview.UNIT
    unit.parent.mkdir(parents=True)
    unit.write_text("Description=Another application")
    monkeypatch.setattr(preview, "run", lambda *a, **kw: SimpleNamespace(stdout="a" * 40))
    with pytest.raises(RuntimeError, match="unrelated service"):
        preview.deploy(8787)
    assert unit.read_text() == "Description=Another application"
    assert not (area / "config.json").exists()


@pytest.mark.parametrize("path", ["/home/a user/preview", "/home/a%user/preview", '/home/a"user/preview'])
def test_systemd_path_escaping_is_rejected(path):
    with pytest.raises(ValueError, match="systemd"):
        preview.checked_path(Path(path))
