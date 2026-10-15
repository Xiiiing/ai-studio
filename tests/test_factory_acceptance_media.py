"""CPU synthetic media acceptance, explicitly not real TTS/GPU/cloud quality."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import numpy as np
import pytest

from factory import acceptance, evaluation
from factory.audio_editor import FFMPEG
from factory.production_profiles import normalize
from studio.core import sha256

PID = "012345abcdef"


def receipt(root, path):
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def tone(path, seconds, channels=1):
    rate = 24000
    signal = (np.sin(np.arange(round(rate * seconds)) * 2 * np.pi * 440 / rate) * 4000).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels); stream.setsampwidth(2); stream.setframerate(rate)
        stream.writeframes(np.repeat(signal[:, None], channels, axis=1).tobytes())


def video(path, seconds):
    subprocess.run([str(FFMPEG), "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-f", "lavfi", "-i",
                    f"testsrc=size=144x256:rate=24:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-ac", "2", "-c:a", "aac",
                    "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-t", str(seconds), str(path)],
                   check=True, capture_output=True, timeout=60)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    from PIL import Image
    base = tmp_path_factory.mktemp("acceptance-profiles")
    library = base / "clips"; library.mkdir()
    for seconds in (4, 5, 6):
        video(library / f"{seconds}.mp4", seconds)
    samples = {}
    for preset in ("legacy15", "compact6", "ensemble10"):
        root = base / preset
        folder = root / ("production/factory-" + PID)
        folder.mkdir(parents=True)
        profile = normalize(None if preset == "legacy15" else {"preset": preset})
        role = profile["characters"][0]["character_id"]
        Image.new("RGB", (144, 256), (50, 70, 90)).save(folder / "reference.png")
        tone(folder / "voice.wav", 1)
        tone(folder / "soundtrack.wav", profile["duration_seconds"], 2)
        video(folder / "episode.mp4", profile["duration_seconds"])
        shutil.copyfile(folder / "episode.mp4", folder / "episode_clean.mp4")
        image, voice = receipt(root, folder / "reference.png"), receipt(root, folder / "voice.wav")
        shots, voices, sources, edits, cues = [], [], [], [], []
        offset = 0.0
        for index, seconds in enumerate(profile["shot_seconds"], 1):
            sid = f"S{index:02d}"; lid = sid + "_D01"
            shutil.copyfile(library / f"{int(seconds)}.mp4", folder / (sid + ".mp4"))
            clip = receipt(root, folder / (sid + ".mp4"))
            line = {"id": lid, "role": role, "text": "Synthetic dialogue", "start": .2}
            shot = {"id": sid, "seconds": seconds, "prompt": "CPU test pattern, not inference", "characters": [role],
                    "video": clip, "image": image, "dialogue": [line],
                    "audio_timing": {"fits": True, "lines": [{"id": lid, "duration_seconds": 1, "sha256": voice["sha256"]}]}}
            shots.append(shot)
            voices.append({**voice, **line})
            sources.append({"shot": sid, "seconds": seconds, "video": clip, "image": image, "segment": clip})
            edits.append({"id": lid, "shot": sid, "source": voice, "processed": voice, "start": offset + .2, "seconds": 1, "tempo": 1})
            cues.append({"line": lid, "shot": sid, "text": line["text"], "start": offset + .2, "end": offset + 1.2})
            offset += seconds
        (folder / "subtitles.zh.srt").write_text("\n\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i, c in enumerate(cues, 1)), encoding="utf-8")
        write(folder / "timeline.json", {"fps": 24, "duration_seconds": offset, "shots": sources, "audio": receipt(root, folder / "soundtrack.wav"), "subtitles": cues})
        write(folder / "audio_edit.json", {"duration_seconds": offset, "dialogue": edits})
        artifacts = [receipt(root, folder / name) for name in ("episode.mp4", "episode_clean.mp4", "subtitles.zh.srt", "timeline.json", "audio_edit.json", "soundtrack.wav")]
        delivery = {"schema": "autonomous_factory.delivery.v1", "project_id": PID, "revision": 2, "path": artifacts[0]["path"],
                    "duration_seconds": offset, "fps": 24, "resolution": [144, 256], "sources": sources, "artifacts": artifacts}
        project = {"id": PID, "slug": "factory-" + PID, "production_revision": 2, "revision": 5, "duration_seconds": offset,
                   "format": "animation", "shots": shots, "script": {"cast": {role: "Synthetic adult"}, "shots": copy.deepcopy(shots)},
                   "assets": {"audio": {"lines": voices}}, "delivery": delivery}
        if preset != "legacy15":
            project["production_profile"] = profile
        write(folder / "manifest.json", delivery)
        write(folder / "factory_owner.json", {"project_id": PID})
        samples[preset] = (root, project)
    return samples


@pytest.fixture
def film(corpus, tmp_path):
    root, project = corpus["compact6"]
    shutil.copytree(root / "production", tmp_path / "production")
    return tmp_path, copy.deepcopy(project)


def run(root, project, **kwargs):
    source = root / "project.json"; write(source, project)
    return acceptance.validate(PID, project_json=source, root=root, output=root / "reports", **kwargs)


def check(report, name):
    return [v for v in report["checks"] if v["check"] == name]


def update_manifest(root, project):
    write(root / ("production/factory-" + PID + "/manifest.json"), project["delivery"])


def update_artifact(root, project, name, data):
    item = next(v for v in project["delivery"]["artifacts"] if v["path"].endswith("/" + name))
    path = root / item["path"]
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    item.update(receipt(root, path)); update_manifest(root, project)


@pytest.mark.parametrize("preset", ["legacy15", "compact6", "ensemble10"])
def test_all_existing_profiles_have_real_cpu_technical_evidence(corpus, tmp_path, preset):
    root, project = corpus[preset]
    shutil.copytree(root / "production", tmp_path / "production")
    before = {p.relative_to(tmp_path): sha256(p) for p in (tmp_path / "production").rglob("*") if p.is_file()}
    result = run(tmp_path, copy.deepcopy(project), production_revision=2, media_sha=project["delivery"]["artifacts"][0]["sha256"])
    assert result["technical_passed"] is True, [v for v in result["checks"] if v["status"] == "fail"]
    assert result["verdict"] == "human_review_required" and result["human_approved"] is False
    assert result["acceptance"]["model_execution"] == "not_run"
    assert all(v["status"] == "not_run" for v in result["checks"] if v["source"] in {"vlm", "asr", "vbench", "human"})
    assert before == {p.relative_to(tmp_path): sha256(p) for p in (tmp_path / "production").rglob("*") if p.is_file()}


@pytest.mark.parametrize("defect,failed", [("missing_shot", "shot_media"), ("wrong_sha", "media_sha_selector"),
    ("old_delivery", "delivery_project_revision"), ("wrong_manifest", "delivery_manifest_matches_snapshot"),
    ("duplicate_artifact", "unique_delivery_artifacts"), ("wrong_profile", "production_profile_contract"),
    ("missing_subtitles", "required_exports"), ("invalid_subtitles", "artifact_integrity"),
    ("wrong_voice_sha", "voice_edit_evidence"), ("wrong_measured_duration", "voice_edit_evidence"),
    ("wrong_dialogue_role", "screenplay_storyboard_dialogue"), ("primary_alias", "primary_delivery_receipt")])
def test_negative_production_contracts_are_evidence_not_pass(film, defect, failed):
    root, project = film
    options = {}
    if defect == "missing_shot": (root / project["shots"][0]["video"]["path"]).unlink()
    elif defect == "wrong_sha": options["media_sha"] = "0" * 64
    elif defect == "old_delivery": project["delivery"]["revision"] = 1
    elif defect == "wrong_manifest": write(root / ("production/factory-" + PID + "/manifest.json"), {})
    elif defect == "duplicate_artifact": project["delivery"]["artifacts"].append(copy.deepcopy(project["delivery"]["artifacts"][0]))
    elif defect == "wrong_profile": project["production_profile"]["shot_seconds"][0] = 5
    elif defect == "missing_subtitles": project["delivery"]["artifacts"] = [v for v in project["delivery"]["artifacts"] if not v["path"].endswith(".srt")]
    elif defect == "invalid_subtitles": update_artifact(root, project, "subtitles.zh.srt", "invalid")
    elif defect == "wrong_voice_sha": project["assets"]["audio"]["lines"][0]["sha256"] = "0" * 64
    elif defect == "wrong_measured_duration": project["shots"][0]["audio_timing"]["lines"][0]["duration_seconds"] = 10
    elif defect == "wrong_dialogue_role": project["shots"][0]["dialogue"][0]["role"] = "unknown-role"
    elif defect == "primary_alias": project["delivery"]["path"] = project["delivery"]["artifacts"][1]["path"]
    result = run(root, project, **options)
    assert result["verdict"] == "needs_repair" and result["technical_passed"] is False
    assert any(v["status"] == "fail" for v in check(result, failed))
    evidence = json.loads(Path(result["report_dir"], "failures.json").read_text())
    assert evidence and all(v["project_id"] == PID and v["media_signature"] for v in evidence)


def test_split_subtitle_cues_follow_measured_speech_not_one_cue_assumption(film):
    root, project = film
    timeline_path = next(root / v["path"] for v in project["delivery"]["artifacts"] if v["path"].endswith("/timeline.json"))
    timeline = json.loads(timeline_path.read_text())
    first = timeline["subtitles"][0]
    timeline["subtitles"][:1] = [{**first, "text": "Synthetic ", "end": .7}, {**first, "text": "dialogue", "start": .7}]
    update_artifact(root, project, "timeline.json", timeline)
    update_artifact(root, project, "subtitles.zh.srt", "\n\n".join(f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i, c in enumerate(timeline["subtitles"], 1)))
    result = run(root, project)
    assert result["technical_passed"] is True, [v for v in result["checks"] if v["status"] == "fail"]


def test_human_approval_is_bound_to_version_and_all_media(film):
    root, project = film
    first = run(root, project)
    human = json.loads(Path(first["report_dir"], "human-review-template.json").read_text())
    human.update(reviewer="Synthetic test reviewer", reviewed_at="2026-10-14T00:00:00Z", decision="approve")
    for value in human["dimensions"].values():
        value.update(status="pass", evidence=[{"fixture": "simulated manual review, not artistic evidence"}])
    approved = run(root, project, human=human)
    assert approved["human_approved"] is True and approved["verdict"] == "human_approved"
    human["production_revision"] = 1
    stale = run(root, project, human=human)
    assert stale["verdict"] == "needs_repair" and stale["human_approved"] is False


def test_hash_manifest_covers_summary_failures_and_all_exports(film):
    root, project = film
    result = run(root, project)
    folder = Path(result["report_dir"])
    manifest = json.loads((folder / "export-manifest.json").read_text())
    assert {v["path"] for v in manifest["files"]} == {p.name for p in folder.iterdir() if p.name != "export-manifest.json"}
    assert all(sha256(folder / item["path"]) == item["sha256"] for item in manifest["files"])


def test_forbidden_links_or_cross_project_delivery_fail_before_decode(film, monkeypatch):
    root, project = film
    def forbidden(*args, **kwargs):
        raise AssertionError("Unsafe paths reached the media decoder")
    monkeypatch.setattr(evaluation, "evaluate", forbidden)
    project["delivery"]["path"] = "production/another-project/episode.mp4"
    with pytest.raises(ValueError, match="another project"):
        run(root, project)
    project["delivery"]["path"] = project["delivery"]["artifacts"][0]["path"]
    path = root / project["shots"][0]["video"]["path"]
    path.unlink(); path.symlink_to(root / project["shots"][1]["video"]["path"])
    with pytest.raises(ValueError, match="Symbolic"):
        run(root, project)


@pytest.mark.parametrize("name", ["timeline.json", "audio_edit.json"])
def test_duration_metadata_cannot_contradict_measured_delivery(film, name):
    root, project = film
    path = next(root / v["path"] for v in project["delivery"]["artifacts"] if v["path"].endswith("/" + name))
    value = read_json_fixture(path); value["duration_seconds"] += 1
    update_artifact(root, project, name, value)
    result = run(root, project)
    assert any(v["status"] == "fail" for v in check(result, "timeline_audio_duration_metadata"))


def read_json_fixture(path):
    return json.loads(path.read_text())


def test_delivery_sources_must_match_verified_timeline(film):
    root, project = film
    project["delivery"]["sources"][0]["video"]["sha256"] = "0" * 64
    update_manifest(root, project)
    result = run(root, project)
    assert any(v["status"] == "fail" for v in check(result, "manifest_timeline_sources"))


def test_director_visual_changes_do_not_mask_locked_story_changes(film):
    root, project = film
    project["episode"] = copy.deepcopy(project["script"])
    project["episode"]["shots"][0]["prompt"] = "A changed visual composition"
    good = run(root, project)
    assert all(v["status"] == "pass" for v in check(good, "screenplay_episode_locked_fields"))
    project["episode"]["shots"][0]["dialogue"][0]["text"] = "A changed story outcome"
    bad = run(root, project)
    assert any(v["status"] == "fail" for v in check(bad, "screenplay_episode_locked_fields"))


def test_actual_soundtrack_duration_mismatch_is_not_hidden_by_matching_metadata(film):
    root, project = film
    soundtrack = root / ("production/factory-" + PID + "/soundtrack.wav")
    tone(soundtrack, 1, 2)
    replacement = receipt(root, soundtrack)
    artifact = next(v for v in project["delivery"]["artifacts"] if v["path"] == replacement["path"])
    artifact.update(replacement)
    timeline = read_json_fixture(root / ("production/factory-" + PID + "/timeline.json"))
    timeline["audio"] = replacement
    update_artifact(root, project, "timeline.json", timeline)
    result = run(root, project)
    assert result["technical_passed"] is False
    assert any(v["status"] == "fail" for v in check(result, "soundtrack_duration"))


def test_human_binding_covers_receipts_even_when_master_hash_does_not_change(film):
    root, project = film
    original_sha = project["delivery"]["artifacts"][0]["sha256"]
    first = run(root, project)
    human = read_json_fixture(Path(first["report_dir"], "human-review-template.json"))
    human.update(reviewer="Synthetic test only", reviewed_at="2026-10-14T00:00:00Z", decision="approve")
    for value in human["dimensions"].values():
        value.update(status="pass", evidence=[{"fixture": True}])
    edit_path = root / ("production/factory-" + PID + "/audio_edit.json")
    edit = read_json_fixture(edit_path); edit["technical_note"] = "A new, independently recorded edit receipt"
    update_artifact(root, project, "audio_edit.json", edit)
    result = run(root, project, human=human)
    assert project["delivery"]["artifacts"][0]["sha256"] == original_sha
    assert result["human_approved"] is False and result["verdict"] == "needs_repair"
    assert any(v["status"] == "fail" for v in check(result, "human_review_binding"))


def test_actual_existing_audio_editor_output_is_accepted(film, monkeypatch):
    """Exercise the unchanged CPU mixer, not an invented audio_edit schema."""
    from types import SimpleNamespace
    from factory import audio_editor
    from factory.speech import SpeechMixin
    from studio import core
    root, project = film
    monkeypatch.setattr(core, "ROOT", root)
    monkeypatch.setattr(audio_editor, "ROOT", root)
    class OfflineEditor(audio_editor.AudioEditorMixin, SpeechMixin):
        def boundary(self):
            pass
        def emit(self, *_args):
            pass
    editor = OfflineEditor()
    editor.project, editor.shots, editor.config, editor.mode = project, project["shots"], {}, "motion_comic"
    editor.budget = SimpleNamespace(timeout=lambda limit: limit)
    folder = root / ("production/factory-" + PID + "/actual-mix")
    folder.mkdir()
    mapping = {line["id"]: root / line["path"] for line in project["assets"]["audio"]["lines"]}
    soundtrack, cues = editor._mix_audio(folder, mapping)
    actual = read_json_fixture(folder / "audio_edit.json")
    assert "duration_seconds" not in actual  # Actual supported producer schema.
    update_artifact(root, project, "audio_edit.json", actual)
    timeline = read_json_fixture(root / ("production/factory-" + PID + "/timeline.json"))
    timeline.update(audio=receipt(root, soundtrack), subtitles=cues)
    update_artifact(root, project, "timeline.json", timeline)
    update_artifact(root, project, "subtitles.zh.srt", "\n\n".join(
        f"{i}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i, c in enumerate(cues, 1)))
    result = run(root, project)
    assert result["technical_passed"], [v for v in result["checks"] if v["status"] == "fail"]
    assert result["human_approved"] is False


def test_clean_export_change_during_evaluation_is_detected(film, monkeypatch):
    root, project = film
    original = evaluation.evaluate
    clean = next(root / v["path"] for v in project["delivery"]["artifacts"] if v["path"].endswith("episode_clean.mp4"))
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        with clean.open("ab") as stream:
            stream.write(b"changed-after-decode")
        return result
    monkeypatch.setattr(evaluation, "evaluate", change)
    result = run(root, project)
    assert result["technical_passed"] is False
    assert any(v["status"] == "fail" for v in check(result, "evidence_unchanged"))
