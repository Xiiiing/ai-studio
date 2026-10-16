"""Production ledger integration using isolated SQLite and CPU media fixtures."""
from __future__ import annotations

import copy
import time
import unittest
from pathlib import Path
from unittest import mock

import test_factory_production as fixtures
from factory import store, team_control, team_ledger
from factory.production import _Run, generate_project
from factory.scheduler import ProductionNeedsDecision, Scheduler


class ProductionTeamUnitTests(unittest.TestCase):
    def setUp(self):
        fixtures.FactorySafetyTests.setUp(self)
        root = fixtures.ROOT
        for target, value in (("factory.store.DATA", root / "db"),
                              ("factory.store.DATABASE", root / "db" / "studio.sqlite3")):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        store.initialize()
        self.ident = "abc123def456"
        self.folder = root / "production" / ("factory-" + self.ident)
        self.project.update(id=self.ident, slug="factory-" + self.ident,
                            workflow_version=2, team_control_version=1, status="paused", revision=0,
                            budget={"gpu_minutes": 2, "spent_gpu_minutes": 0})
        with store.connection() as db:
            db.execute("INSERT INTO projects VALUES(?,?,?,?)",
                       (self.ident, time.time(), 0, store.encoded(self.project)))
            team_ledger.ensure_project(db, self.project)

    def save(self, patch):
        self.project.update(copy.deepcopy(patch))
        store.update(self.ident, patch)

    def task(self, ident):
        return team_ledger.task_detail(self.ident, ident)["task"]

    def test_real_image_calls_are_distinct_from_reviews_and_plans(self):
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker):
            run = _Run(self.project, self.config, self.save, None)
            run._characters_and_images()
            image = self.task("image:S01")
            review = self.task("image_review:S01")
            self.assertEqual(image["status"], "completed")
            self.assertEqual(image["attempt"], 1)
            self.assertEqual(image["artifacts"][0]["receipt"]["origin"], "unit_test_fixture_not_model_inference")
            self.assertEqual(review["details"]["qa"]["technical"]["status"], "pass")
            self.assertEqual(review["dependencies"], ["image:S01"])
            self.assertNotEqual(image["attempts"][0]["run_id"], review["attempts"][0]["run_id"])
            video = self.task("video:S01")
            self.assertEqual(video["status"], "pending")
            self.assertEqual(video["attempts"], [])
            previous_calls = list(fixtures.FixtureWorker.calls)
            _Run(self.project, self.config, self.save, None)._characters_and_images()
            self.assertEqual(fixtures.FixtureWorker.calls, previous_calls)
            self.assertEqual(self.task("image:S01")["attempt"], 1)

    def test_unavailable_reviewer_preserves_completed_generation(self):
        def unavailable(*_args):
            return {"status": "unavailable", "detail": "fixture reviewer unavailable"}
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker):
            with self.assertRaises(ProductionNeedsDecision):
                _Run(self.project, self.config, self.save, None, unavailable)._characters_and_images()
        generated = self.task("image:S01")
        reviewed = self.task("image_review:S01")
        self.assertEqual(generated["status"], "completed")
        self.assertEqual(generated["attempt"], 1)
        self.assertNotEqual(reviewed["review_status"], "passed")
        self.assertEqual(len(reviewed["details"]["semantic_attempts"]), 2)
        self.assertEqual(self.task("video:S01")["attempt"], 0)

    def test_speech_lines_have_independent_calls_and_cache_does_not_reexecute(self):
        for index, shot in enumerate(self.project["episode"]["shots"], 1):
            shot["dialogue"] = [{"id": f"{shot['id']}_L1", "role": "female", "text": f"第{index}句。"}]
            shot["seconds"] = 3
        self.save({"episode": self.project["episode"]})
        with mock.patch.object(Scheduler, "audio_lease", fixtures.fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=fixtures.FixtureSpeech.invoke):
            run = _Run(self.project, self.config, self.save, None)
            mapping = run._voice()
            first = self.task("speech:S01_L1")
            second = self.task("speech:S02_L1")
            first_inputs = {row["label"]: row["text"] for row in first["inputs"]}
            self.assertEqual(first_inputs["execution_boundary"], "per_line_speech_call")
            self.assertEqual(fixtures.FixtureSpeech.batches, [["S01_L1"], ["S02_L1"]])
            self.assertEqual(first["status"], "completed")
            self.assertEqual(second["status"], "completed")
            self.assertEqual(first["model"], "fixture/cpu-speech")
            run._fit_dialogue(mapping)
            self.assertEqual(self.task("dialogue:S01")["review_status"], "passed")
            run._voice()
            self.assertEqual(fixtures.FixtureSpeech.calls, ["S01_L1", "S02_L1"])
            self.assertEqual(self.task("speech:S01_L1")["attempt"], 1)

    def test_partial_speech_batch_retains_verified_line_evidence(self):
        self.project["episode"]["shots"][0]["dialogue"] = [
            {"id": "S01_L1", "role": "female", "text": "第一句。"},
            {"id": "S01_L2", "role": "female", "text": "第二句。"}]
        self.save({"episode": self.project["episode"]})
        fixtures.FixtureSpeech.failures = ["partial", "oom"]
        with mock.patch.object(Scheduler, "audio_lease", fixtures.fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=fixtures.FixtureSpeech.invoke):
            with self.assertRaises(ProductionNeedsDecision):
                _Run(self.project, self.config, self.save, None)._voice()
        first = self.task("speech:S01_L1")
        second = self.task("speech:S01_L2")
        self.assertEqual(first["status"], "completed")
        self.assertTrue(first["artifacts"][0]["receipt"]["sha256"])
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(second["artifacts"], [])

    def test_real_camera_and_export_record_outputs_separately_from_technical_review(self):
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker):
            delivery = generate_project(self.project, self.config, self.save)
            self.assertEqual(self.task("video:S01")["model"], "ffmpeg_zoompan_v1")
            self.assertEqual(self.task("video_review:S01")["details"]["qa"]["technical"]["status"], "pass")
            self.assertEqual(self.task("edit:delivery")["model"], "ffmpeg_editor")
            self.assertEqual(self.task("technical_review:delivery")["review_status"], "passed")
            self.assertEqual(delivery["status"], "awaiting_producer_final_review")
            again = generate_project(self.project, self.config, self.save)
            self.assertEqual(again["source_signature"], delivery["source_signature"])
            self.assertEqual(self.task("edit:delivery")["attempt"], 1)

    def test_priority_changes_actual_ready_image_order_without_reordering_film(self):
        with store.connection() as db:
            db.execute("UPDATE team_work_items SET priority=10 WHERE project_id=? AND id='image:S02'", (self.ident,))
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker):
            _Run(self.project, self.config, self.save, None)._characters_and_images()
        shots = [Path(path).parts[-2] for path in fixtures.FixtureWorker.calls if "/assets/shots/" in path]
        self.assertEqual(shots, ["S02", "S01"])
        self.assertEqual([shot["id"] for shot in self.project["shots"]], ["S01", "S02"])

    def test_late_generation_keeps_receipt_but_cannot_publish_or_review(self):
        original_execute = fixtures.FixtureWorker.execute

        def execute(worker, *args, **kwargs):
            result = original_execute(worker, *args, **kwargs)
            task = self.task("image:S01")
            team_control.submit(self.ident, {
                "request_id": "during-image-call", "expected_control_revision": 0,
                "target_type": "task", "target_id": "image:S01", "task_ids": ["image:S01"],
                "task_generations": {"image:S01": task["generation"]},
                "instruction": "保留人物设定，重新调整表情", "operation": "revise", "after": "auto"})
            return result

        run = _Run(self.project, self.config, self.save, None)
        shot = run.shots[0]
        target = run._target(shot["id"], run.folder / "assets/shots" / shot["id"], shot["prompt"], shot=shot)
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker), \
                mock.patch.object(fixtures.FixtureWorker, "execute", execute):
            with self.assertRaises(team_control.TeamInterventionPending):
                run._render_targets([target], "story_frames")
        task = self.task("image:S01")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["attempts"][0]["status"], "superseded")
        self.assertTrue(task["attempts"][0]["artifact"]["receipt"]["sha256"])
        self.assertTrue((target["folder"] / "take_001/result.json").exists())
        self.assertTrue((target["folder"] / "take_001/attempt.json").exists())
        self.assertFalse((target["folder"] / "selected.json").exists())
        self.assertEqual(self.task("image_review:S01")["attempt"], 0)
        self.assertNotIn("image", shot)

    def test_cached_attach_obeys_pending_instruction_fence(self):
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker):
            _Run(self.project, self.config, self.save, None)._characters_and_images()
            calls = list(fixtures.FixtureWorker.calls)

            def fence(_pid, kind, scope, **_kwargs):
                if kind == "image" and scope == "S01":
                    raise team_control.TeamInterventionPending("fixture cached output superseded")

            with mock.patch.object(team_control, "assert_publishable", fence):
                with self.assertRaises(team_control.TeamInterventionPending):
                    _Run(self.project, self.config, self.save, None)._characters_and_images()
            self.assertEqual(fixtures.FixtureWorker.calls, calls)

    def test_locked_first_frame_blocks_internal_video_repair_generation(self):
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker):
            run = _Run(self.project, self.config, self.save, None)
            run._characters_and_images()
            shot = run.shots[0]
            target = run._target(shot["id"], run.folder / "renders" / shot["id"], "move gently",
                                 [run.receipt_path(shot["image"])], kind="video", shot=shot, seconds=shot["seconds"])
            original_new = run._new_attempt

            def repair(value):
                record = original_new(value)
                record["repair"] = {"regenerate_keyframe": True}
                return record

            task = self.task("image:S01")
            team_control.task_action(self.ident, "image:S01", {
                "action": "lock", "request_id": "lock-first-frame", "expected_control_revision": 0,
                "expected_generation": task["generation"]})
            calls = list(fixtures.FixtureWorker.calls)
            with mock.patch.object(run, "_new_attempt", repair):
                with self.assertRaises(ProductionNeedsDecision) as caught:
                    run._render_targets([target], "diffusion_video")
            self.assertEqual(caught.exception.reason, "team_locked_keyframe")
            self.assertEqual(fixtures.FixtureWorker.calls, calls)

    def test_speech_retake_revision_changes_real_seed_and_preserves_other_line_cache(self):
        for shot in self.project["episode"]["shots"]:
            shot["dialogue"] = [{"id": shot["id"] + "_L1", "role": "female", "text": "保留相同对白。"}]
        self.save({"episode": self.project["episode"]})
        seeds = []

        def invoke(argv, **kwargs):
            if len(argv) > 1 and str(argv[1]) == str(fixtures.ROOT / "audio/tts.py"):
                seeds.append(int(argv[argv.index("--seed") + 1]))
            return fixtures.FixtureSpeech.invoke(argv, **kwargs)

        with mock.patch.object(Scheduler, "audio_lease", fixtures.fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=invoke):
            _Run(self.project, self.config, self.save, None)._voice()
            original_signature = self.task("speech:S01_L1")["input_version"]
            self.project["episode"]["shots"][0]["dialogue"][0]["team_revision"] = 1
            self.save({"episode": self.project["episode"]})
            _Run(self.project, self.config, self.save, None)._voice()
        self.assertEqual(fixtures.FixtureSpeech.calls, ["S01_L1", "S02_L1", "S01_L1"])
        self.assertNotEqual(self.task("speech:S01_L1")["input_version"], original_signature)
        self.assertEqual(seeds, [20261012, 20261012, 20262025])
        self.assertEqual(self.task("speech:S02_L1")["attempt"], 1)

    def test_instruction_during_qa_keeps_review_history_without_automatic_selection(self):
        def reviewer(*_args):
            task = self.task("image:S01")
            team_control.submit(self.ident, {
                "request_id": "during-review", "expected_control_revision": 0,
                "target_type": "task", "target_id": "image:S01", "task_ids": ["image:S01"],
                "task_generations": {"image:S01": task["generation"]},
                "instruction": "修改本镜的表情", "operation": "revise", "after": "auto"})
            return {"status": "pass", "issues": []}

        run = _Run(self.project, self.config, self.save, None, reviewer)
        shot = run.shots[0]
        target = run._target(shot["id"], run.folder / "assets/shots" / shot["id"], shot["prompt"], shot=shot, review=True)
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker):
            with self.assertRaises(team_control.TeamInterventionPending):
                run._render_targets([target], "story_frames")
        reviewed = self.task("image_review:S01")
        self.assertEqual(reviewed["attempts"][0]["status"], "superseded")
        self.assertEqual(reviewed["attempts"][0]["review_status"], "passed")
        self.assertTrue((target["folder"] / "take_001/result.json").exists())
        self.assertFalse((target["folder"] / "selected.json").exists())
        self.assertNotIn("image", shot)

    def test_instruction_during_speech_call_preserves_wav_and_stops_next_line(self):
        for shot in self.project["episode"]["shots"]:
            shot["dialogue"] = [{"id": shot["id"] + "_L1", "role": "female", "text": "保留对白。"}]
        self.save({"episode": self.project["episode"]})

        def invoke(argv, **kwargs):
            result = fixtures.FixtureSpeech.invoke(argv, **kwargs)
            if len(argv) > 1 and str(argv[1]) == str(fixtures.ROOT / "audio/tts.py"):
                task = self.task("speech:S01_L1")
                team_control.submit(self.ident, {
                    "request_id": "during-speech", "expected_control_revision": 0,
                    "target_type": "task", "target_id": "speech:S01_L1", "task_ids": ["speech:S01_L1"],
                    "task_generations": {"speech:S01_L1": task["generation"]},
                    "instruction": "重新配音，情绪克制", "operation": "revise", "after": "auto"})
            return result

        with mock.patch.object(Scheduler, "audio_lease", fixtures.fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=invoke):
            with self.assertRaises(team_control.TeamInterventionPending):
                _Run(self.project, self.config, self.save, None)._voice()
        self.assertEqual(fixtures.FixtureSpeech.calls, ["S01_L1"])
        self.assertEqual(self.task("speech:S01_L1")["attempts"][0]["status"], "superseded")
        self.assertTrue(self.task("speech:S01_L1")["attempts"][0]["artifact"]["receipt"]["sha256"])
        self.assertEqual(self.task("speech:S02_L1")["attempt"], 0)
        self.assertEqual(list((self.folder / "audio/tts/lines").glob("*/selected.json")), [])
        self.assertNotIn("audio", self.project.get("assets", {}))

    def test_finish_hold_saves_first_output_before_stopping_next_model_call(self):
        task = self.task("image:S01")
        team_control.task_action(self.ident, "image:S01", {
            "action": "hold_after", "request_id": "hold-first-frame", "expected_control_revision": 0,
            "expected_generation": task["generation"]})
        run = _Run(self.project, self.config, self.save, None)
        targets = [run._target(shot["id"], run.folder / "assets/shots" / shot["id"], shot["prompt"], shot=shot)
                   for shot in run.shots]
        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker):
            with self.assertRaises(team_control.TeamInterventionPending):
                run._render_targets(targets, "story_frames")
        first = self.task("image:S01")
        self.assertTrue(first["awaiting_ack"])
        self.assertEqual(first["status"], "completed")
        self.assertTrue((targets[0]["folder"] / "take_001/result.json").exists())
        self.assertEqual(self.task("image:S02")["attempt"], 0)
        self.assertEqual(len(fixtures.FixtureWorker.calls), 1)

    def test_voice_retry_preserves_locked_frames_videos_and_unrelated_speech_versions(self):
        for shot in self.project["episode"]["shots"]:
            shot["dialogue"] = [{"id": shot["id"] + "_L1", "role": "female", "text": "同一段对白。"}]
            shot["seconds"] = 3
        self.project["production_revision"] = 1
        self.save({"episode": self.project["episode"], "production_revision": 1})
        review_calls = []

        def reviewer(_project, shot, path, _references):
            review_calls.append((shot["id"], path))
            return {"status": "pass", "issues": [], "score": 95}

        with mock.patch.object(Scheduler, "worker", fixtures.fake_worker), \
                mock.patch.object(Scheduler, "audio_lease", fixtures.fake_audio_lease), \
                mock.patch("factory.speech.subprocess.run", side_effect=fixtures.FixtureSpeech.invoke):
            original = generate_project(self.project, self.config, self.save, semantic_evaluator=reviewer)
            media = {shot["id"]: {kind: (shot[kind]["path"], shot[kind]["sha256"])
                                  for kind in ("image", "video")} for shot in self.project["shots"]}
            voices = {line["id"]: copy.deepcopy(line) for line in self.project["assets"]["audio"]["lines"]}
            original_image_calls = list(fixtures.FixtureWorker.calls)
            original_reviews = len(review_calls)
            for index, kind in enumerate(("image", "video")):
                ident = kind + ":S01"
                team_control.task_action(self.ident, ident, {
                    "action": "lock", "request_id": "lock-voice-retake-" + kind,
                    "expected_control_revision": index, "expected_generation": self.task(ident)["generation"]})
            request = {
                "request_id": "voice-retake-keep-visuals", "expected_control_revision": 2,
                "target_type": "task", "target_id": "speech:S01_L1", "task_ids": ["speech:S01_L1"],
                "task_generations": {"speech:S01_L1": self.task("speech:S01_L1")["generation"]},
                "instruction": "相同对白重新生成声音，保留镜头", "operation": "retry", "after": "auto",
                "preserve": ["image:S01", "video:S01", "speech:S02_L1"]}
            preview = team_control.preview(self.ident, request)
            self.assertEqual(preview["conflicts"], [])
            self.assertNotIn("image:S01", preview["affected_tasks"])
            self.assertNotIn("video:S01", preview["affected_tasks"])
            self.assertNotIn("speech:S02_L1", preview["affected_tasks"])
            submitted = team_control.submit(self.ident, request)
            self.assertEqual(submitted["instruction"]["status"], "queued")
            applied = team_control.process_pending(self.ident)
            self.assertEqual(applied["status"], "applied", applied)
            self.project = store.get(self.ident)
            updated = generate_project(self.project, self.config, self.save, semantic_evaluator=reviewer)
        self.assertEqual(fixtures.FixtureWorker.calls, original_image_calls)
        self.assertGreater(len(review_calls), original_reviews)
        self.assertEqual(fixtures.FixtureSpeech.calls, ["S01_L1", "S02_L1", "S01_L1"])
        for shot in self.project["shots"]:
            for kind in ("image", "video"):
                self.assertEqual((shot[kind]["path"], shot[kind]["sha256"]), media[shot["id"]][kind])
        current = {line["id"]: line for line in self.project["assets"]["audio"]["lines"]}
        self.assertNotEqual(current["S01_L1"]["path"], voices["S01_L1"]["path"])
        self.assertNotEqual(current["S01_L1"]["source_signature"], voices["S01_L1"]["source_signature"])
        self.assertEqual(current["S02_L1"], voices["S02_L1"])
        self.assertEqual(updated["sources"][0]["video"]["sha256"], original["sources"][0]["video"]["sha256"])
        self.assertTrue(team_control.is_locked(self.ident, "image", "S01"))
        self.assertTrue(team_control.is_locked(self.ident, "video", "S01"))


if __name__ == "__main__":
    unittest.main()
