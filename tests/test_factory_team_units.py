"""Production ledger integration using isolated SQLite and CPU media fixtures."""
from __future__ import annotations

import copy
import time
import unittest
from pathlib import Path
from unittest import mock

import test_factory_production as fixtures
from factory import store, team_ledger
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

    def test_speech_lines_report_shared_batch_and_cache_does_not_reexecute(self):
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
            second_inputs = {row["label"]: row["text"] for row in second["inputs"]}
            self.assertEqual(first_inputs["execution_boundary"], "shared_speech_batch")
            self.assertEqual(first_inputs["batch_signature"], second_inputs["batch_signature"])
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
        self.assertTrue(first["details"]["partial_batch"])
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


if __name__ == "__main__":
    unittest.main()
