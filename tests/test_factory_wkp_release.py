"""Release validation without touching services or production data."""
import hashlib
import io
from pathlib import Path
import sqlite3
import tarfile
from types import SimpleNamespace

import pytest

from scripts import wkp_deploy_release as release
from scripts import wkp_cami_gateway as gateway


def bundle(path, name="dist/index.html", *, symlink=False):
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        if symlink:
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
            archive.addfile(member)
        else:
            member.size = 5
            archive.addfile(member, io.BytesIO(b"hello"))
    return path


@pytest.mark.parametrize("name", ["/etc/passwd", "dist/../../outside", "config/factory.json"])
def test_ui_bundle_cannot_overwrite_private_or_external_files(tmp_path, name):
    with pytest.raises(ValueError, match="only dist"):
        release.unpack(bundle(tmp_path / "ui.tar.gz", name), tmp_path / "output")
    assert not (tmp_path / "outside").exists()


def test_ui_bundle_rejects_symbolic_links(tmp_path):
    with pytest.raises(ValueError, match="Links"):
        release.unpack(bundle(tmp_path / "ui.tar.gz", symlink=True), tmp_path / "output")


def test_busy_queue_prevents_any_service_shutdown(tmp_path, monkeypatch):
    data = tmp_path / "runtime/factory"
    data.mkdir(parents=True)
    with sqlite3.connect(data / "studio.sqlite3") as db:
        db.execute("CREATE TABLE jobs(id TEXT, project_id TEXT, status TEXT)")
        db.execute("INSERT INTO jobs VALUES('job','project','running')")
    artifact = bundle(tmp_path / "ui.tar.gz")
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="safe boundary"):
        release.deploy(tmp_path, "a" * 40, artifact, release.digest(artifact))
    assert not any(args[0] == "systemctl" for args in calls)


def test_task_arriving_during_preflight_restores_web_without_stopping_worker(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/factory.json").write_text("private configuration")
    artifact = bundle(tmp_path / "ui.tar.gz")
    calls, counts = [], iter([[], [("new", "project", "running")]])
    monkeypatch.setattr(release, "active_jobs", lambda _: next(counts))
    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="b" * 40 if args[:2] == ("git", "rev-parse") else "")
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(RuntimeError, match="worker left running"):
        release.deploy(tmp_path, "a" * 40, artifact, release.digest(artifact))
    service_calls = [args for args in calls if args[0] == "systemctl"]
    assert service_calls == [("systemctl", "stop", release.UNITS[0]), ("systemctl", "start", release.UNITS[0])]
    assert (tmp_path / "config/factory.json").read_text() == "private configuration"


def test_cami_gateway_refuses_private_key_input_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "AREA", tmp_path / "gateway")
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 0)
    private_key = "-----BEGIN PRIVATE KEY-----\nnot a public certificate\n"
    with pytest.raises(ValueError, match="public certificate"):
        gateway.install(private_key, hashlib.sha256(private_key.encode()).hexdigest())
    assert not (tmp_path / "gateway").exists()


@pytest.mark.parametrize("failure", ["queue_read", "worker_stop"])
def test_shutdown_failure_restores_web_before_leaving_preflight(tmp_path, monkeypatch, failure):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/factory.json").write_text("private configuration")
    artifact = bundle(tmp_path / "ui.tar.gz")
    calls, reads = [], []
    def jobs(_):
        reads.append(True)
        if len(reads) > 1 and failure == "queue_read":
            raise OSError("temporary database failure")
        return []
    monkeypatch.setattr(release, "active_jobs", jobs)
    def run(*args, **kwargs):
        calls.append(args)
        if failure == "worker_stop" and args == ("systemctl", "stop", release.UNITS[1]):
            raise OSError("stop timed out")
        return SimpleNamespace(stdout="b" * 40 if args[:2] == ("git", "rev-parse") else "")
    monkeypatch.setattr(release, "run", run)
    with pytest.raises(OSError):
        release.deploy(tmp_path, "a" * 40, artifact, release.digest(artifact))
    assert calls[-1] == ("systemctl", "start", release.UNITS[0])
    if failure == "queue_read":
        assert ("systemctl", "stop", release.UNITS[1]) not in calls
    else:
        assert ("systemctl", "start", release.UNITS[1]) in calls
    assert (tmp_path / "config/factory.json").read_text() == "private configuration"
