"""CLI contract tests against an isolated store; no model, GPU, or service calls."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from factory import cli, config, store


class FactoryCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for target, value in [
            ("factory.store.DATA", self.root / "runtime"),
            ("factory.store.DATABASE", self.root / "runtime/studio.sqlite3"),
            ("factory.config.DATA", self.root / "runtime"),
            ("factory.cli.ROOT", self.root),
        ]:
            owned = patch(target, value)
            owned.start()
            self.addCleanup(owned.stop)
        store.initialize()

    def invoke(self, *args):
        out, error = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(error):
            result = cli.main(list(args))
        return result, out.getvalue(), error.getvalue()

    def project(self):
        return store.create({"brief": "两个成年邻居归还一把钥匙，结尾解开误会。",
                             "duration_seconds": 60,
                             "budget": {"gpu_minutes": 30, "wall_minutes": 90, "max_attempts": 3}})

    def test_run_submits_once_with_request_id_and_file_input(self):
        brief = self.root / "brief.txt"
        brief.write_text("原创成年邻居归还钥匙，冲突简单，保留温暖结局。", encoding="utf-8")
        args = ("run", "--brief-file", str(brief), "--request-id", "cli-create-once",
                "--gpu-minutes", "35", "--wall-minutes", "95", "--max-attempts", "2", "--json")
        first = self.invoke(*args)
        second = self.invoke(*args)
        self.assertEqual((first[0], second[0]), (0, 0))
        p = json.loads(first[1])
        self.assertEqual(json.loads(second[1])["id"], p["id"])
        self.assertEqual(p["status"], "queued")
        self.assertEqual(p["budget"], {"gpu_minutes": 35.0, "wall_minutes": 95.0,
                                      "max_attempts": 2, "spent_gpu_minutes": 0})
        self.assertEqual(store.queue_summary()["queued"], 1)
        self.assertEqual(len(store.list_projects()), 1)
        brief.write_text("改变了创作方向：成年邻居共同开店的喜剧。", encoding="utf-8")
        self.assertEqual(self.invoke(*args)[0], 2)
        self.assertEqual(len(store.list_projects()), 1)

    def test_choose_uses_explicit_gate_and_replays_without_another_answer(self):
        p = self.project()
        old = store.gate(p["id"], "story", [{"id": "A"}], "旧提案", 1)
        store.update(p["id"], {"proposal_generation": 2, "proposals_complete_generation": 2,
                               "proposals": [{"id": "A", "eligible": True}],
                               "creation_review": {"passed": True}})
        current = store.gate(p["id"], "story", [{"id": "A"}], "当前提案", 2)
        stale = self.invoke("choose", p["id"], "--type", "story", "--choice", "A",
                            "--decision-id", old["id"])
        self.assertEqual(stale[0], 2)
        self.assertEqual(store.get(p["id"])["answers"], {})
        args = ("choose", p["id"], "--type", "story", "--choice", "A",
                "--decision-id", current["id"], "--feedback", "保留钥匙线索。")
        self.assertEqual(self.invoke(*args)[0], 0)
        after = store.get(p["id"])
        self.assertEqual(self.invoke(*args)[0], 0)
        self.assertEqual(store.get(p["id"]), after)
        self.assertEqual(list(after["answers"]), [current["id"]])
        self.assertEqual(after["answers"][current["id"]]["feedback"], "保留钥匙线索。")
        self.assertIsNone(after["selected_story"])  # Selection effects belong to the worker.

    def test_existing_style_and_final_choices_require_current_gate_and_feedback(self):
        p = self.project()
        store.update(p["id"], {"styles": [{"id": "style-2"}], "style_previews_generation": 1,
                               "script_complete_generation": 1, "script_review": {"passed": True}})
        style = store.gate(p["id"], "style", [{"id": "style-2"}], "选画风", 1)
        self.assertEqual(self.invoke("choose", p["id"], "--type", "style", "--choice", "style-2")[0], 0)
        self.assertIn(style["id"], store.get(p["id"])["answers"])
        store.update(p["id"], {"delivery_complete_revision": 1, "delivery": {"path": "synthetic.mp4"}})
        final = store.gate(p["id"], "final", [{"id": "approve"}, {"id": "revise"}], "审片", 1)
        before = store.get(p["id"])
        self.assertEqual(self.invoke("choose", p["id"], "--type", "final", "--choice", "revise")[0], 2)
        self.assertEqual(store.get(p["id"]), before)
        self.assertEqual(self.invoke("choose", p["id"], "--type", "final", "--choice", "revise",
                                    "--decision-id", final["id"], "--feedback", "只修改 S01，保留 S02。")[0], 0)
        answer = store.get(p["id"])["answers"][final["id"]]
        self.assertEqual(answer["choice"], "revise")
        self.assertEqual(answer["feedback"], "只修改 S01，保留 S02。")

    def test_production_revise_keeps_budget_and_enforces_snapshot_revision(self):
        p = self.project()
        episode = {"shots": [{"id": "S01", "prompt": "old first"}, {"id": "S02", "prompt": "old second"}]}
        store.update(p["id"], {"status": "blocked", "selected_story": "A", "selected_style": "style-2",
                              "script": episode, "episode": copy.deepcopy(episode),
                              "needs_decision": {"type": "production", "reason": "retry_limit"},
                              "production": {"gpu_seconds": 120, "elapsed_seconds": 180, "takes": {"S01": 3}}})
        before = store.get(p["id"])
        feedback = self.root / "feedback.txt"
        feedback.write_text("只修改 S01，钥匙放在桌面。保留 S02。", encoding="utf-8")
        def arguments(revision):
            return ("revise", p["id"], "--shots", "S01,S01", "--expected-revision", str(revision),
                    "--feedback-file", str(feedback), "--json")
        self.assertEqual(self.invoke(*arguments(before["revision"] - 1))[0], 2)
        self.assertEqual(store.get(p["id"]), before)
        self.assertEqual(self.invoke(*arguments(before["revision"]))[0], 0)
        after = store.get(p["id"])
        self.assertEqual(after["revision_request"]["feedback"]["shot_ids"], ["S01"])
        for key in ("script", "episode", "budget", "production"):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after["production_revision"], before["production_revision"] + 1)
        self.assertEqual(after["script_generation"], before["script_generation"] + 1)
        self.assertEqual(self.invoke(*arguments(before["revision"]))[0], 0)
        self.assertEqual(store.get(p["id"]), after)

    def test_pause_and_budget_resume_preserve_usage_and_require_exhausted_component(self):
        p = self.project()
        self.assertEqual(self.invoke("pause", p["id"])[0], 0)
        self.assertEqual(store.get(p["id"])["status"], "paused")
        self.assertEqual(self.invoke("resume", p["id"])[0], 0)
        store.update(p["id"], {"status": "blocked",
                              "needs_decision": {"type": "budget", "exhausted_limits": ["wall"]},
                              "production": {"gpu_seconds": 90, "elapsed_seconds": 5401}})
        before = store.get(p["id"])
        self.assertEqual(self.invoke("resume", p["id"], "--gpu-minutes", "40")[0], 2)
        self.assertEqual(store.get(p["id"]), before)
        self.assertEqual(self.invoke("resume", p["id"], "--wall-minutes", "120")[0], 0)
        after = store.get(p["id"])
        self.assertEqual(after["production"], before["production"])
        self.assertEqual(after["budget"]["gpu_minutes"], before["budget"]["gpu_minutes"])
        self.assertEqual(after["budget"]["wall_minutes"], 120)
        self.assertEqual(after["script_generation"], before["script_generation"])

    def test_status_human_and_json_are_observational(self):
        p = self.project()
        store.update(p["id"], {"proposals": [{"id": "A", "title": "钥匙", "logline": "一把钥匙解开邻里误会。"}]})
        gate = store.gate(p["id"], "story", [{"id": "A", "label": "钥匙"}], "选故事", 1)
        before = store.get(p["id"])
        result, output, error = self.invoke("status", p["id"])
        self.assertEqual((result, error), (0, ""))
        self.assertIn("一把钥匙解开邻里误会。", output)
        self.assertIn("项目 revision：" + str(before["revision"]), output)
        self.assertIn(gate["id"], output)
        self.assertIn("每个素材版本最多尝试", output)
        raw = self.invoke("status", p["id"], "--json")
        self.assertEqual(json.loads(raw[1]), before)
        self.assertEqual(store.get(p["id"]), before)

    def test_outputs_list_existing_current_project_files_without_approving_or_reencoding(self):
        p = self.project()
        directory = self.root / "production" / p["slug"] / "release"
        directory.mkdir(parents=True)
        video = directory / "episode.mp4"
        video.write_bytes(b"fixture-only-not-a-rendered-video")
        manifest = directory / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        foreign = self.root / "production" / "another-project.mp4"
        foreign.write_bytes(b"foreign")
        path = video.relative_to(self.root).as_posix()
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        delivery = {"artifacts": [{"path": path, "sha256": digest},
                                 {"path": directory.relative_to(self.root).as_posix() + "/missing.srt"},
                                 {"path": str(foreign)}],
                    "video_url": "/media/" + path,
                    "manifest_url": "/media/" + manifest.relative_to(self.root).as_posix()}
        store.update(p["id"], {"delivery": delivery})
        store.gate(p["id"], "final", [{"id": "approve"}], "待终审", 1)
        before = store.get(p["id"])
        result = self.invoke("outputs", p["id"], "--json")
        self.assertEqual(result[0], 0)
        files = json.loads(result[1])
        self.assertEqual({v["path"] for v in files["files"]}, {str(video), str(manifest)})
        self.assertEqual(len(files["unavailable"]), 2)
        self.assertEqual(files["files"][0]["recorded_sha256"], digest)
        self.assertEqual(json.loads(self.invoke("export", p["id"], "--json")[1]), files)
        self.assertEqual(store.get(p["id"]), before)
        self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(), digest)

    def test_roles_use_effective_resolver_without_initializing_store_or_exposing_secrets(self):
        configuration = {
            "llm": {"base_url": "http://127.0.0.1:8090/v1", "model": "fixture-qwen", "vision": True,
                    "context_tokens": 8192},
            "llm_profiles": {"reviewer": {"base_url": "http://127.0.0.1:8089/v1", "model": "fixture-deepseek",
                                         "vision": False, "context_tokens": 8192}},
            "llm_roles": {"story_critic": "reviewer", "independent_critic": "reviewer"},
        }
        with patch.object(config, "load_config", return_value=configuration), \
                patch.object(store, "initialize", side_effect=AssertionError("roles must not open DB")):
            result, output, error = self.invoke("roles", "--json")
            human = self.invoke("roles")
        self.assertEqual((result, error), (0, ""))
        rows = json.loads(output)
        self.assertEqual({row["role"] for row in rows}, set(config.ROLE_LABELS))
        self.assertEqual(sum(v["model"] == "fixture-deepseek" for v in rows), 2)
        self.assertEqual(next(v for v in rows if v["role"] == "visual_critic")["model"], "fixture-qwen")
        self.assertNotIn("never-print-this", output + human[1])
        self.assertNotIn("also-private", output + human[1])
        self.assertIn("未进行在线健康探测", human[1])

    def test_roles_reject_inline_credentials_without_echoing_them(self):
        configuration = {"llm": {"base_url": "http://127.0.0.1:8090/v1", "model": "fixture",
                                  "context_tokens": 8192, "vision": True,
                                  "api_key": "private-fixture-do-not-echo"}}
        with patch.object(config, "load_config", return_value=configuration), \
                patch.object(store, "initialize", side_effect=AssertionError("roles must not open DB")):
            result, output, error = self.invoke("roles", "--json")
        self.assertEqual(result, 2)
        self.assertNotIn("private-fixture-do-not-echo", output + error)
        self.assertIn("api_key_env", error)

    def test_model_export_apply_preserves_other_config_and_rejects_stale_revision(self):
        import os
        from factory import providers
        value = {"schema_version": 1, "production": {"gpu_cards": [1, 2], "max_parallel": 1},
                 "llm_profiles": {
                    "writer": {"base_url": "http://127.0.0.1:8090/v1", "model": "fixture",
                               "context_tokens": 8192, "vision": True}},
                 "llm_roles": {name: "writer" for name in config.ROLE_LABELS}}
        path = self.root / "factory.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        exported = self.root / "models.json"
        with patch.dict(os.environ, {"DRAMA_FACTORY_CONFIG": str(path)}), \
                patch.object(store, "initialize", side_effect=AssertionError("models must not open DB")):
            result, _, error = self.invoke("models", "export", "--file", str(exported))
            self.assertEqual((result, error), (0, ""))
            draft = json.loads(exported.read_text())
            self.assertEqual(draft["revision"], providers.config_revision(value))
            draft["llm_profiles"]["writer"]["model"] = "changed"
            exported.write_text(json.dumps(draft), encoding="utf-8")
            self.assertEqual(self.invoke("models", "apply", "--file", str(exported))[0], 0)
            actual = json.loads(path.read_text())
            self.assertEqual(actual["production"], value["production"])
            self.assertEqual(actual["llm_profiles"]["writer"]["model"], "changed")
            self.assertEqual(self.invoke("models", "apply", "--file", str(exported))[0], 2)
            self.assertEqual(json.loads(path.read_text()), actual)

    def test_model_probe_requires_a_profile_and_does_not_initialize_store(self):
        from factory import providers
        with patch.object(store, "initialize", side_effect=AssertionError("probe must not open DB")), \
                patch.object(providers, "probe_profile", return_value={"status": "ready", "generation": False}) as probe:
            self.assertEqual(self.invoke("models", "probe")[0], 2)
            probe.assert_not_called()
            code, text, error = self.invoke("models", "probe", "writer")
            self.assertEqual((code, error), (0, ""))
            self.assertFalse(json.loads(text)["generation"])
            probe.assert_called_once_with("writer")

    def test_advance_refuses_online_worker_without_claiming_a_job(self):
        p = self.project()
        before = store.get(p["id"])
        with patch("fcntl.flock", side_effect=BlockingIOError), \
                patch.object(store, "claim_job") as claim, patch("factory.worker.handle") as handle:
            result, output, error = self.invoke("advance", p["id"])
        self.assertEqual(result, 2)
        self.assertIn("worker 已在线", error)
        claim.assert_not_called()
        handle.assert_not_called()
        self.assertEqual(store.get(p["id"]), before)


if __name__ == "__main__":
    unittest.main()
