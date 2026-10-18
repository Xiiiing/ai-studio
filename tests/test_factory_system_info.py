"""System identity never starts a model or reads the private configuration."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from factory import system_info


def test_revision_command_is_bounded_and_returns_only_a_commit(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="b" * 40 + "\n")
    monkeypatch.setattr(system_info.subprocess, "run", run)
    assert system_info._source_commit(Path("/fixture")) == "b" * 40
    assert calls == [(["git", "rev-parse", "--verify", "HEAD"], {
        "cwd": Path("/fixture"), "capture_output": True, "text": True, "check": True, "timeout": 2,
    })]


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("git", 2)])
def test_source_identity_is_optional_when_packaged_without_git(monkeypatch, tmp_path, failure):
    def run(*args, **kwargs):
        raise failure
    monkeypatch.setattr(system_info.subprocess, "run", run)
    assert system_info._source_commit(tmp_path) is None


def test_unexpected_git_output_is_not_exposed(monkeypatch, tmp_path):
    monkeypatch.setattr(system_info.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="/private/config\n"))
    assert system_info._source_commit(tmp_path) is None
