"""CPU-only safety/resume/media integration tests. No model service is launched."""
from __future__ import annotations

import contextlib
import copy
import tempfile
import subprocess
import wave
import numpy as np
import unittest
import uuid
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from studio.core import ROOT, fingerprint, media_receipt, read_json, sha256, write_json
from factory.production import _Run, generate_project, generate_style_previews
from factory.quality import combine, inspect_image, inspect_video, repair_plan, semantic_review
from factory.scheduler import Budget, ProductionBudgetExceeded, ProductionNeedsDecision, ProductionPaused, Scheduler


class FixtureWorker:
    """Clearly marked deterministic image fixture; never substitutes for a model in the application."""
    calls = []
    active = False

    def copy_input(self, path):
        return str(path)

    def execute(self, graph, destination, kind, timeout=1):
        if kind != "image":
            raise AssertionError("The CPU fixture cannot generate video")
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        width, height = graph["5"]["inputs"]["width"], graph["5"]["inputs"]["height"]
        image = Image.new("RGB", (width, height), (55, 92, 135))
        draw = ImageDraw.Draw(image)
        for x in range(0, width, 20):
            draw.rectangle((x, 0, x + 8, height), fill=(110, 150, 180))
        draw.ellipse((width//4, height//5, width*3//4, height*3//5), fill=(213, 177, 142))
        path = destination / "image.png"
        image.save(path)
        record = media_receipt(path, origin="unit_test_fixture_not_model_inference")
        write_json(destination / "result.json", record)
        self.calls.append(str(destination))
        return path


@contextlib.contextmanager
def fake_worker(self, tag, control=None, min_free=None):
    self.budget.check(control)
    FixtureWorker.active = True
    try:
        yield FixtureWorker()
    finally:
        FixtureWorker.active = False



_REAL_SUBPROCESS_RUN = subprocess.run


class FixtureSpeech:
    """CPU WAV fixture exercising the real cache/timing code; never loads a TTS model."""
    active = False
    calls = []
    batches = []
    failures = []
    durations = {}

    @classmethod
    def invoke(cls, argv, **kwargs):
        if len(argv) < 2 or str(argv[1]) != str(ROOT / "audio/tts.py"):
            return _REAL_SUBPROCESS_RUN(argv, **kwargs)
        if not cls.active:
            raise AssertionError("TTS invocation escaped its GPU lease")
        folder = Path(argv[argv.index("--output-dir") + 1])
        lines = read_json(folder / "input.json")
        expected = read_json(folder / "expected_sources.json")
        cls.batches.append([line["id"] for line in lines])
        failure = cls.failures.pop(0) if cls.failures else None
        if failure in ("transient", "oom"):
            kwargs["stdout"].write("CUDA out of memory\n" if failure == "oom" else "temporary TTS backend error\n")
            return subprocess.CompletedProcess(argv, 1)
        for line in lines:
            ident = line["id"]
            path = folder / (ident + ".wav")
            source = expected[ident]
            if path.exists() and path.with_suffix(".json").exists():
                old = read_json(path.with_suffix(".json"))
                if old.get("source_sha256") == source["source_sha256"] and old["quality"]["wav_sha256"] == sha256(path):
                    continue
            duration = cls.durations.get(line["text"], 1.2)
            samples = (np.sin(np.arange(round(duration * 24000)) * (2 * np.pi * 220 / 24000)) * 5000).astype("<i2")
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(samples.tobytes())
            quality = {"sample_rate": 24000, "channels": 1, "frames": len(samples),
                       "duration_seconds": duration, "wav_sha256": sha256(path)}
            write_json(path.with_suffix(".json"), {"id": ident, "role": line["role"], "text": line["text"].strip(),
                                                   "source_sha256": source["source_sha256"], "source": source["source"],
                                                   "quality": quality, "fixture": "not_model_inference"})
            cls.calls.append(ident)
            if failure == "partial":
                kwargs["stdout"].write("temporary failure after a completed line\n")
                return subprocess.CompletedProcess(argv, 1)
        return subprocess.CompletedProcess(argv, 0)


@contextlib.contextmanager
def fake_audio_lease(self, control=None):
    self.budget.check(control, require_gpu=True)
    FixtureSpeech.active = True
    try:
        with self.budget.gpu_operation():
            yield 0
    finally:
        FixtureSpeech.active = False


class FactorySafetyTests(unittest.TestCase):
    def setUp(self):
        # All receipt and asset paths belong to this test. Never depend on an
        # installed model, a private download receipt, or the operator's project.
        self.sandbox = tempfile.TemporaryDirectory(prefix="factory-production-test-")
        self.addCleanup(self.sandbox.cleanup)
        sandbox_root = Path(self.sandbox.name)
        for target in (__name__ + ".ROOT", "studio.core.ROOT", "studio.media.ROOT",
                       "factory.production.ROOT", "factory.speech.ROOT",
                       "factory.audio_editor.ROOT", "factory.scheduler.ROOT"):
            patcher = mock.patch(target, sandbox_root)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("factory.scheduler.GPU_LOCK_DIR", sandbox_root / "runtime/gpu_locks")
        patcher.start()
        self.addCleanup(patcher.stop)

        # The real SpeechMixin still hashes this driver, checks each listed
        # file's metadata, and verifies every WAV source/receipt. Only the
        # driver's pure input helpers are synthetic; it cannot run inference.
        audio = sandbox_root / "audio"
        audio.mkdir()
        (audio / "tts.py").write_text(
            '"""CPU test source fixture; contains no model inference."""\n'
            'import json\n'
            'from pathlib import Path\n'
            'def canonical(value):\n'
            '    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))\n'
            'def parse_input(path, voices):\n'
            '    result = []\n'
            '    for line in json.loads(Path(path).read_text(encoding="utf-8")):\n'
            '        voice = voices[line["role"]]\n'
            '        result.append({"id": line["id"], "role": line["role"], "text": line["text"].strip(),\n'
            '                       "speaker": voice["speaker"], "language": voice.get("language", "Chinese"),\n'
            '                       "emotion": line.get("emotion", "") or "", "instruct": voice.get("instruct", "")})\n'
            '    return result\n'
            'if __name__ == "__main__":\n'
            '    raise RuntimeError("CPU fixture must never execute a model process")\n',
            encoding="utf-8")
        write_json(audio / "voices.json", {
            "female": {"speaker": "Fixture_Female", "language": "Chinese"},
            "male": {"speaker": "Fixture_Male", "language": "Chinese"}})
        model = sandbox_root / "models/tts/Qwen3-TTS-12Hz-1.7B-CustomVoice"
        model.mkdir(parents=True)
        model_file = model / "fixture.weights"
        model_file.write_bytes(b"Synthetic CPU fixture, not model weights.\n")
        write_json(model / "download_receipt.json", {
            "repo_id": "fixture/cpu-speech", "revision": "fixture-v1", "fixture": True,
            "files": {"fixture.weights": {"sha256": sha256(model_file), "size": model_file.stat().st_size}}})
        (sandbox_root / "assets/fonts").mkdir(parents=True)

        self.ident = "unit-" + uuid.uuid4().hex[:14]
        self.folder = ROOT / "production" / ("factory-" + self.ident)
        self.project = {
            "id": self.ident, "slug": "factory-" + self.ident, "format": "motion_comic",
            "styles": [{"id": "a", "name": "A", "prompt": "Warm graphic novel"},
                       {"id": "b", "name": "B", "prompt": "Cool illustrated film"},
                       {"id": "c", "name": "C", "prompt": "Ink illustration"}],
            "episode": {"title": "CPU 验证样片", "cast": {"female": "Adult woman with a black bob",
                                                       "male": "Adult man in a dark jacket"},
                        "shots": [{"id": "S01", "seconds": .75, "prompt": "A person waits by a window",
                                   "reference_group": "female", "dialogue": []},
                                  {"id": "S02", "seconds": 1.25, "prompt": "A second person approaches the window",
                                   "reference_group": "male", "dialogue": []}]}}
        self.config = {"gpu_cards": [0, 1], "image_width": 288, "image_height": 512,
                       "output_width": 288, "output_height": 512, "max_gpu_seconds": 120,
                       "max_wall_seconds": 180, "max_attempts": 2}
        FixtureWorker.calls = []
        FixtureSpeech.calls, FixtureSpeech.batches = [], []
        FixtureSpeech.failures, FixtureSpeech.durations = [], {}
        FixtureSpeech.active = False

    def tearDown(self):
        import shutil
        if self.folder.exists():
            owner = read_json(self.folder / "factory_owner.json")
            self.assertEqual(owner["project_id"], self.ident)
            shutil.rmtree(self.folder)

    def save(self, patch):
        self.project.update(copy.deepcopy(patch))


    def test_dialogue_repair_preserves_other_voices_and_resumes_full_export(self):
        first, second = self.project["episode"]["shots"]
        original, shortened = "这条录音里面藏着关键的线索。", "录音里有线索。"
        first.update(seconds=4, dialogue=[{"id": "S01_L1", "role": "female", "text": original, "start": .2}])
        second.update(seconds=4, dialogue=[{"id": "S02_L1", "role": "male", "text": "快看。", "start": .2}])
        FixtureSpeech.durations = {original: 5.2, shortened: 2.4}
        callback_measurements = []
        def rewrite(project, shot, measurement):
            self.assertFalse(FixtureSpeech.active)
            self.assertEqual(project["production"]["active_gpu_leases"], 0)
            self.assertEqual(shot["prompt"], first["prompt"])
            self.assertEqual(shot["seconds"], 4)
            callback_measurements.append(measurement)
            return {"line_updates": [{"id": "S01_L1", "text": shortened}], "reason": "保留录音中有线索的原意"}
        with mock.patch.object(Scheduler, "audio_lease", fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=FixtureSpeech.invoke), \
                mock.patch.object(Scheduler, "worker", fake_worker):
            run = _Run(self.project, self.config, self.save, None, dialogue_rewriter=rewrite)
            mapping = run._voice()
            unchanged = (mapping["S02_L1"], sha256(mapping["S02_L1"]))
            mapping = run._fit_dialogue(mapping)
            self.assertEqual(FixtureSpeech.calls, ["S01_L1", "S02_L1", "S01_L1"])
            self.assertEqual((mapping["S02_L1"], sha256(mapping["S02_L1"])), unchanged)
            self.assertEqual(len(callback_measurements), 1)
            self.assertAlmostEqual(callback_measurements[0]["max_total_speech_seconds"], 4.4375)
            self.assertFalse(callback_measurements[0]["fits"])
            self.assertEqual(self.project["episode"]["shots"][0]["dialogue"][0]["text"], shortened)
            self.assertEqual(self.project["script"]["shots"][0]["dialogue"][0]["text"], shortened)
            self.assertEqual(read_json(self.folder / "episode.json")["shots"][0]["dialogue"][0]["text"], shortened)
            delivery = generate_project(self.project, self.config, self.save, dialogue_rewriter=rewrite)
            self.assertEqual(delivery["duration_seconds"], 8)
            self.assertEqual(FixtureSpeech.calls, ["S01_L1", "S02_L1", "S01_L1"])
            self.assertEqual(len(callback_measurements), 1)
            mix = read_json(Path(ROOT / delivery["path"]).parent / "audio_edit.json")
            self.assertEqual(mix["dialogue"][1]["source"]["sha256"], unchanged[1])
            self.assertEqual(delivery["technical_qa"]["subtitled"]["status"], "pass")

    def test_dialogue_repairs_stop_after_two_and_do_not_repeat_on_resume(self):
        original, one, two = "我已经在这里等了你很久很久。", "我在这里等了你很久。", "我等了你很久。"
        self.project["episode"]["shots"][0].update(seconds=4, dialogue=[
            {"id": "S01_L1", "role": "female", "text": original}])
        FixtureSpeech.durations = {original: 5.8, one: 5.2, two: 4.8}
        responses = iter((one, two))
        def rewrite(project, shot, measurement):
            self.assertFalse(FixtureSpeech.active)
            return {"line_updates": [{"id": "S01_L1", "text": next(responses)}], "reason": "保留等候含义"}
        with mock.patch.object(Scheduler, "audio_lease", fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=FixtureSpeech.invoke):
            run = _Run(self.project, self.config, self.save, None, dialogue_rewriter=rewrite)
            with self.assertRaises(ProductionNeedsDecision) as caught:
                run._fit_dialogue(run._voice())
            self.assertEqual(caught.exception.reason, "dialogue_timing")
            self.assertEqual(len(self.project["shots"][0]["dialogue_repairs"]), 2)
            self.assertEqual(FixtureSpeech.calls, ["S01_L1", "S01_L1", "S01_L1"])
            resumed = _Run(self.project, self.config, self.save, None, dialogue_rewriter=rewrite)
            with self.assertRaises(ProductionNeedsDecision):
                resumed._fit_dialogue(resumed._voice())
            self.assertEqual(FixtureSpeech.calls, ["S01_L1", "S01_L1", "S01_L1"])

    def test_rewriter_cannot_change_roles_or_remove_numeric_facts(self):
        original = "这是 3 年前发生的那件事。"
        self.project["episode"]["shots"][0].update(seconds=4, dialogue=[
            {"id": "S01_L1", "role": "female", "text": original}])
        FixtureSpeech.durations = {original: 5.5}
        responses = iter(([{"id": "S01_L1", "text": "这是3年前的事。", "role": "male"}],
                          [{"id": "S01_L1", "text": "这是往事。"}]))
        def rewrite(project, shot, measurement):
            return {"line_updates": next(responses), "reason": "invalid fixture"}
        with mock.patch.object(Scheduler, "audio_lease", fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=FixtureSpeech.invoke):
            run = _Run(self.project, self.config, self.save, None, dialogue_rewriter=rewrite)
            with self.assertRaises(ProductionNeedsDecision) as caught:
                run._fit_dialogue(run._voice())
            self.assertEqual(caught.exception.reason, "dialogue_rewrite_failed")
            self.assertEqual(run.shots[0]["dialogue"][0]["text"], original)
            self.assertEqual(run.shots[0]["dialogue"][0]["role"], "female")
            self.assertEqual(FixtureSpeech.calls, ["S01_L1"])

    def test_transient_voice_failure_retries_once_and_oom_does_not_retry(self):
        self.project["episode"]["shots"][0].update(seconds=4, dialogue=[
            {"id": "S01_L1", "role": "female", "text": "别害怕。"}])
        FixtureSpeech.failures = ["transient"]
        with mock.patch.object(Scheduler, "audio_lease", fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=FixtureSpeech.invoke):
            run = _Run(self.project, self.config, self.save, None)
            run._voice()
            self.assertEqual(len(FixtureSpeech.batches), 2)
            attempts = [read_json(path) for path in (self.folder / "audio/tts/batches").glob("*/attempts.json")][0]
            self.assertEqual([a["state"] for a in attempts], ["failed", "completed"])
            self.assertIn("temporary", attempts[0]["error_tail"])
            self.assertEqual(run.budget.snapshot()["active_gpu_leases"], 0)
            self.project["episode"]["shots"][0]["dialogue"][0]["text"] = "看着我。"
            FixtureSpeech.failures = ["oom"]
            run = _Run(self.project, self.config, self.save, None)
            with self.assertRaises(ProductionNeedsDecision) as caught:
                run._voice()
            self.assertEqual(caught.exception.reason, "voice_failed")
            self.assertTrue(caught.exception.details["oom"])
            self.assertEqual(len(FixtureSpeech.batches), 3)
            with self.assertRaises(ProductionNeedsDecision):
                _Run(self.project, self.config, self.save, None)._voice()
            self.assertEqual(len(FixtureSpeech.batches), 3)
            self.assertFalse(FixtureSpeech.active)

    def test_tts_driver_revision_invalidates_voice_cache(self):
        self.project["episode"]["shots"][0].update(seconds=4, dialogue=[
            {"id": "S01_L1", "role": "female", "text": "原来是你。"}])
        with mock.patch.object(Scheduler, "audio_lease", fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=FixtureSpeech.invoke):
            first = _Run(self.project, self.config, self.save, None)._voice()["S01_L1"]
            original_hash = sha256(first)
            def changed_driver(path):
                return "1" * 64 if Path(path) == ROOT / "audio/tts.py" else sha256(path)
            with mock.patch("factory.speech.sha256", side_effect=changed_driver):
                second = _Run(self.project, self.config, self.save, None)._voice()["S01_L1"]
            self.assertNotEqual(first, second)
            self.assertEqual(FixtureSpeech.calls, ["S01_L1", "S01_L1"])
            self.assertEqual(sha256(first), original_hash)


    def test_partial_tts_batch_does_not_reset_line_retry_allowance_on_resume(self):
        self.project["episode"]["shots"][0].update(seconds=4, dialogue=[
            {"id": "S01_L1", "role": "female", "text": "别回头。"}])
        self.project["episode"]["shots"][1].update(seconds=4, dialogue=[
            {"id": "S02_L1", "role": "male", "text": "他在后面。"}])
        FixtureSpeech.failures = ["partial", "transient"]
        with mock.patch.object(Scheduler, "audio_lease", fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=FixtureSpeech.invoke):
            run = _Run(self.project, self.config, self.save, None)
            with self.assertRaises(ProductionNeedsDecision):
                run._voice()
            self.assertEqual(FixtureSpeech.calls, ["S01_L1"])
            self.assertEqual(len(FixtureSpeech.batches), 2)
            with self.assertRaises(ProductionNeedsDecision) as caught:
                _Run(self.project, self.config, self.save, None)._voice()
            self.assertEqual(caught.exception.details["blocked_lines"][0]["id"], "S02_L1")
            self.assertEqual(caught.exception.details["blocked_lines"][0]["attempt_count"], 2)
            self.assertEqual(len(FixtureSpeech.batches), 2)
            # A genuinely changed line may run; the prior completed voice remains byte-identical.
            saved = next((self.folder / "audio/tts/lines").glob("*/selected.json"))
            before = read_json(saved)
            self.project["episode"]["shots"][1]["dialogue"][0]["text"] = "他在门外。"
            mapping = _Run(self.project, self.config, self.save, None)._voice()
            self.assertEqual(sha256(mapping["S01_L1"]), before["sha256"])
            self.assertEqual(FixtureSpeech.calls, ["S01_L1", "S02_L1"])
            self.assertEqual(FixtureSpeech.batches[-1], ["S02_L1"])


    def test_cached_lipsync_semantic_outage_recovers_without_a_gpu_gate(self):
        self.project["format"] = "live_action"
        self.project["episode"]["shots"][0]["lip_sync"] = True
        self.config.update(semantic_enabled=True, semantic_max_attempts=1,
                           semantic_review_revision="unit-lipsync-policy", semantic_model="unit-model")
        online, calls = [False], []
        def reviewer(project, shot, path, references):
            self.assertEqual(project["production"]["active_gpu_leases"], 0)
            calls.append(path)
            return {"status": "pass" if online[0] else "unavailable", "score": 95 if online[0] else None,
                    "issues": []}
        run = _Run(self.project, self.config, self.save, None, reviewer)
        shot = run.shots[0]
        image_path = self.folder / "cached-lipsync-test.png"
        im = Image.new("RGB", (288, 512), (60, 110, 160))
        ImageDraw.Draw(im).ellipse((45, 50, 240, 410), fill=(200, 155, 100))
        im.save(image_path)
        shot["image"] = media_receipt(image_path)
        shot["video"] = run._camera_clip(shot)
        audio_path = self.folder / "cached-lipsync-test.wav"
        with wave.open(str(audio_path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(24000)
            out.writeframes((np.sin(np.arange(7200) * .05) * 3000).astype("<i2").tobytes())
        shot["lipsync_audio"] = media_receipt(audio_path)
        source = run.receipt_path(shot["video"])
        signature = fingerprint({"video": sha256(source), "audio": sha256(audio_path),
                                 "seconds": shot["seconds"], "backend": "LatentSync-1.6"})
        folder = self.folder / "renders" / shot["id"] / ("lipsync_" + signature[:16])
        folder.mkdir(parents=True)
        cached = folder / "video.mp4"
        cached.write_bytes(source.read_bytes())
        record = media_receipt(cached, source_signature=signature, origin="unit_cached_lipsync_fixture",
                               qa=combine(inspect_video(cached, shot["seconds"], run.output_size)))
        write_json(folder / "result.json", record)
        original_sha = sha256(cached)
        run.emit()
        with mock.patch("studio.comfy.gpu_snapshot", side_effect=AssertionError("cached review must not acquire a GPU")):
            with self.assertRaises(ProductionNeedsDecision) as caught:
                run._lip_sync(self.folder)
            self.assertEqual(caught.exception.reason, "semantic_unavailable")
            self.assertEqual(read_json(folder / "result.json")["state"], "semantic_pending")
            self.assertEqual(sha256(cached), original_sha)
            online[0] = True
            resumed = _Run(self.project, self.config, self.save, None, reviewer)
            resumed._lip_sync(self.folder)
            self.assertEqual(len(calls), 2)
            self.assertEqual(resumed.shots[0]["qa"]["semantic"]["status"], "pass")
            self.assertEqual(resumed.shots[0]["video"]["sha256"], original_sha)

    def test_late_dialogue_onset_is_included_in_timing_measurement(self):
        self.project["episode"]["shots"][0].update(seconds=4, dialogue=[
            {"id": "S01_L1", "role": "female", "text": "谁在那里？", "start": .2},
            {"id": "S01_L2", "role": "male", "text": "是我。", "start": 3.0}])
        run = _Run(self.project, self.config, self.save, None)
        measured = run._speech_timing(run.shots[0], [.8, 1.2])
        self.assertFalse(measured["fits"])
        self.assertAlmostEqual(measured["required_tempo"], 1.6)
        self.assertGreater(measured["playback_end_seconds"], 3.75)

    def test_budget_is_cumulative_and_pause_is_not_success(self):
        budget = Budget({"max_gpu_seconds": 60, "max_wall_seconds": 300},
                        {"gpu_seconds": 59.8, "elapsed_seconds": 30})
        self.assertGreaterEqual(budget.snapshot()["elapsed_seconds"], 30)
        with self.assertRaises(ProductionPaused):
            budget.check(lambda: {"pause": True})
        budget.base_gpu = 60
        with self.assertRaises(ProductionBudgetExceeded):
            budget.check(require_gpu=True)

    def test_scheduler_does_not_use_busy_or_under_memory_cards(self):
        scheduler = Scheduler({**self.config, "resource_wait_seconds": 0}, Budget(self.config))
        with mock.patch("factory.scheduler.gpu_snapshot",
                        return_value={0: {"free_mib": 18000, "utilization": 80},
                                      1: {"free_mib": 14000, "utilization": 0}}):
            with self.assertRaises(ProductionPaused):
                scheduler.choose_card()
        with mock.patch("factory.scheduler.gpu_snapshot",
                        return_value={0: {"free_mib": 18000, "utilization": 80},
                                      1: {"free_mib": 16000, "utilization": 0}}):
            self.assertEqual(scheduler.choose_card(), 1)

    def test_semantic_unknown_is_not_a_pass_and_identity_has_targeted_repair(self):
        qa = combine({"status": "pass", "issues": []},
                     semantic_review(None, {}, {}, "unused", []))
        self.assertFalse(qa["semantic_passed"])
        self.assertEqual(qa["status"], "technical_pass_semantic_pending")
        plan = repair_plan({"issues": [{"code": "identity", "detail": "Face differs"}]}, "video", 2)
        self.assertTrue(plan["regenerate_keyframe"])
        self.assertIn("identity", plan["action"])

    def test_black_image_is_rejected(self):
        root = ROOT / "runtime"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="factory-quality-", dir=root) as folder:
            path = Path(folder) / "black.png"
            Image.new("RGB", (288, 512), (0, 0, 0)).save(path)
            result = inspect_image(path, (288, 512))
            self.assertEqual(result["status"], "fail")
            self.assertIn("black_frame", [x["code"] for x in result["issues"]])

    def test_style_previews_are_cached_per_input_and_changed_prompt_gets_a_new_take(self):
        with mock.patch.object(Scheduler, "worker", fake_worker):
            result = generate_style_previews(self.project, self.config, self.save)
            self.assertEqual(len(FixtureWorker.calls), 3)
            self.assertTrue(all(x["preview_status"] == "generated" for x in result))
            again = generate_style_previews(self.project, self.config, self.save)
            self.assertEqual(len(FixtureWorker.calls), 3)
            self.assertEqual(result[0]["preview"]["sha256"], again[0]["preview"]["sha256"])
            self.project["styles"][0]["prompt"] = "Different charcoal style"
            generate_style_previews(self.project, self.config, self.save)
            self.assertEqual(len(FixtureWorker.calls), 4)
            self.assertTrue((self.folder / "style_previews/a/take_002/attempt.json").exists())

    def test_full_cpu_delivery_resumes_and_revisions_preserve_original_film(self):
        self.project["episode"]["shots"][0]["camera_motion"] = "hold"
        self.project["episode"]["shots"][1]["camera_motion"] = "pull_out"
        with mock.patch.object(Scheduler, "worker", fake_worker):
            delivery = generate_project(self.project, self.config, self.save)
            self.assertEqual(len(FixtureWorker.calls), 4)  # Two references and two story frames.
            final = ROOT / delivery["path"]
            original_hash = sha256(final)
            qa = inspect_video(final, 2, (288, 512), require_audio=True)
            self.assertEqual(qa["status"], "pass", qa)
            self.assertEqual(qa["decoded_frames"], 48)
            self.assertEqual(delivery["semantic_status"], "review_required")
            self.assertEqual(self.project["shots"][0]["generation_kind"], "procedural_camera_motion")
            self.assertEqual(self.project["shots"][0]["video"]["motion"], "static")
            self.assertEqual(self.project["shots"][1]["video"]["motion"], "slow_pull")
            reused = generate_project(self.project, self.config, self.save)
            self.assertEqual(reused["path"], delivery["path"])
            self.assertEqual(len(FixtureWorker.calls), 4)
            self.project["production_revision"] = 2
            self.project["edit_plan"] = {"music_gain": .03, "cut_style": "hard"}
            self.project["reedit_requested"] = True
            self.project["edit_feedback"] = "Reduce background music"
            revised = generate_project(self.project, self.config, self.save)
            self.assertNotEqual(revised["path"], delivery["path"])
            self.assertEqual(sha256(final), original_hash)
            self.assertEqual(len(FixtureWorker.calls), 4)

    def test_vlm_runs_after_gpu_release_and_only_failed_shot_is_repaired(self):
        evaluations = {}
        def reviewer(project, shot, path, references):
            self.assertFalse(FixtureWorker.active, "VLM overlapped an active image worker")
            evaluations[shot["id"]] = evaluations.get(shot["id"], 0) + 1
            if shot["id"] == "S01" and evaluations["S01"] == 1:
                return {"status": "fail", "score": 40,
                        "issues": [{"code": "identity", "detail": "Reference face differs"}]}
            return {"status": "pass", "score": 85, "issues": []}
        with mock.patch.object(Scheduler, "worker", fake_worker):
            run = _Run(self.project, self.config, self.save, None, reviewer)
            run._characters_and_images()
        self.assertEqual(len(FixtureWorker.calls), 5)
        self.assertEqual(evaluations, {"S01": 2, "S02": 1})
        first = run.shots[0]["attempts"]
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0]["qa"]["status"], "fail")
        self.assertIn("exact face", first[1]["prompt"])
        self.assertEqual(first[1]["qa"]["status"], "pass")

    def test_semantic_outage_preserves_media_and_resume_only_reviews(self):
        self.config.update(semantic_enabled=True, semantic_max_attempts=2,
                           semantic_review_revision="unit-policy-v1")
        online = {"value": False}
        calls = []
        def reviewer(project, shot, path, references):
            self.assertFalse(FixtureWorker.active)
            calls.append(shot["id"])
            if not online["value"]:
                raise ConnectionError("Temporary local reviewer outage")
            return {"status": "pass", "score": 95, "issues": []}
        with mock.patch.object(Scheduler, "worker", fake_worker):
            first = _Run(self.project, self.config, self.save, None, reviewer)
            with self.assertRaises(ProductionNeedsDecision) as caught:
                first._characters_and_images()
            self.assertEqual(caught.exception.reason, "semantic_unavailable")
            self.assertEqual(len(FixtureWorker.calls), 4)
            self.assertEqual(calls, ["S01", "S01", "S02", "S02"])
            hashes = {}
            for ident in ("S01", "S02"):
                folder = self.folder / "assets/shots" / ident
                self.assertFalse((folder / "selected.json").exists())
                pending = read_json(folder / "pending_review.json")
                hashes[ident] = pending["sha256"]
                self.assertFalse(pending["qa"]["eligible_for_selection"])
                self.assertEqual(read_json(folder / "take_001/attempt.json")["state"], "semantic_pending")
            online["value"] = True
            resumed = _Run(self.project, self.config, self.save, None, reviewer)
            resumed._characters_and_images()
            self.assertEqual(len(FixtureWorker.calls), 4)
            self.assertEqual(calls[-2:], ["S01", "S02"])
            for shot in resumed.shots:
                self.assertEqual(shot["image"]["sha256"], hashes[shot["id"]])
                self.assertTrue(shot["image"]["qa"]["semantic_passed"])
                self.assertEqual(len(shot["attempts"]), 1)
            delivery = generate_project(self.project, self.config, self.save, semantic_evaluator=reviewer)
            self.assertEqual(len(FixtureWorker.calls), 4)
            self.assertEqual(delivery["semantic_status"], "pass")

    def test_legacy_semantic_pending_selection_and_changed_context_are_reassessed(self):
        self.config.update(semantic_enabled=False, semantic_review_revision="unit-policy-v1")
        reviewed = []
        def reviewer(project, shot, path, references):
            reviewed.append(shot["id"])
            return {"status": "pass", "score": 95, "issues": []}
        with mock.patch.object(Scheduler, "worker", fake_worker):
            _Run(self.project, self.config, self.save, None)._characters_and_images()
            self.assertEqual(len(FixtureWorker.calls), 4)
            self.config["semantic_enabled"] = True
            _Run(self.project, self.config, self.save, None, reviewer)._characters_and_images()
            self.assertEqual(reviewed, ["S01", "S02"])
            self.assertEqual(len(FixtureWorker.calls), 4)
            _Run(self.project, self.config, self.save, None, reviewer)._characters_and_images()
            self.assertEqual(reviewed, ["S01", "S02"])
            # Narrative expectations change while the actual image-generation prompt stays fixed.
            self.project["episode"]["shots"][0]["continuity"] = "She must still be holding the same envelope."
            _Run(self.project, self.config, self.save, None, reviewer)._characters_and_images()
            self.assertEqual(reviewed, ["S01", "S02", "S01"])
            self.assertEqual(len(FixtureWorker.calls), 4)

    def test_enabled_semantic_gate_requires_evaluator_before_production(self):
        self.config["semantic_enabled"] = True
        with mock.patch.object(Scheduler, "worker", fake_worker):
            with self.assertRaises(ProductionNeedsDecision) as caught:
                generate_project(self.project, self.config, self.save)
        self.assertEqual(caught.exception.reason, "semantic_unavailable")
        self.assertEqual(FixtureWorker.calls, [])
        self.assertNotIn("delivery", self.project)

    def test_transient_semantic_failure_retries_review_without_a_new_take(self):
        self.config.update(semantic_enabled=True, semantic_max_attempts=2,
                           semantic_review_revision="unit-policy-v1")
        count = {"value": 0}
        def reviewer(project, shot, path, references):
            count["value"] += 1
            if count["value"] == 1:
                return {"status": "unavailable", "issues": [], "detail": "Temporary timeout"}
            return {"status": "pass", "score": 95, "issues": []}
        with mock.patch.object(Scheduler, "worker", fake_worker):
            run = _Run(self.project, self.config, self.save, None, reviewer)
            run._characters_and_images()
        self.assertEqual(count["value"], 3)
        self.assertEqual(len(FixtureWorker.calls), 4)
        self.assertEqual(len(run.shots[0]["attempts"]), 1)
        self.assertEqual(len(run.shots[0]["attempts"][0]["semantic_attempts"]), 2)

    def test_unknown_reedit_feedback_is_not_ignored(self):
        self.project.update(reedit_requested=True, edit_feedback="Make it completely different")
        with self.assertRaises(ProductionNeedsDecision) as caught:
            _Run(self.project, self.config, self.save, None)
        self.assertEqual(caught.exception.reason, "edit_plan_required")


if __name__ == "__main__":
    unittest.main(verbosity=2)
