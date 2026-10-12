"""CPU-only contracts for an approved preview entering character generation."""
from __future__ import annotations

import contextlib
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from factory.production import _Run
from factory.scheduler import ProductionNeedsDecision
from studio.core import media_receipt


class SelectedPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="factory-approved-preview-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch("factory.production.ROOT", self.root))
        self.stack.enter_context(mock.patch("studio.core.ROOT", self.root))
        for name in ("subprocess.Popen", "subprocess.run", "subprocess.check_output"):
            self.stack.enter_context(mock.patch(
                name, side_effect=AssertionError("This test must not create subprocesses")))
        self.stack.enter_context(mock.patch(
            "factory.scheduler.gpu_snapshot",
            side_effect=AssertionError("This test must not query a GPU")))
        self.slug = "factory-approved-preview-test"
        self.path = self.root / "production" / self.slug / "style_previews/style-2/take_001/image.png"
        self.write_image(self.path, (60, 90, 130))
        self.project = {
            "id": "approved-preview-test", "slug": self.slug, "format": "motion_comic",
            "selected_style": "style-2",
            "styles": [{"id": "style-2", "name": "Approved fixture",
                        "prompt": "Soft watercolor",
                        "preview": media_receipt(self.path)}],
            "episode": {"title": "CPU preview-reference fixture",
                        "cast": {"female": "Adult woman with a black bob",
                                 "male": "Adult man in a dark jacket"},
                        "shots": []},
        }
        self.config = {"gpu_cards": [0], "max_wall_seconds": 120,
                       "max_gpu_seconds": 120, "max_attempts": 2}

    def write_image(self, path, color):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 24), color).save(path)

    def capture_characters(self, project):
        run = _Run(project, self.config, lambda patch: None, None)
        captured = {}
        def render(targets, phase):
            self.assertEqual(phase, "character_references")
            for target in targets:
                captured[target["character"]] = {
                    "references": list(target["references"]),
                    "signature": target["signature"],
                    "prompt": target["prompt"],
                }
            return []
        with mock.patch.object(run, "_render_targets", side_effect=render) as renderer:
            run._characters_and_images()
        renderer.assert_called_once()
        self.assertEqual(set(captured), {"female", "male"})
        owner = json.loads((run.folder / "factory_owner.json").read_text())
        self.assertEqual(owner["project_id"], self.project["id"])
        return captured

    def test_canonical_approved_preview_constrains_only_its_role_and_signature(self):
        baseline = self.capture_characters(copy.deepcopy(self.project))
        self.assertEqual(baseline["female"]["references"], [self.path.resolve()])
        self.assertEqual(baseline["male"]["references"], [])

        compact_choice = copy.deepcopy(self.project)
        compact_choice["selected_style"] = {"id": "style-2"}
        compact_choice["styles"][0]["preview_role"] = "female"
        canonical = self.capture_characters(compact_choice)
        self.assertEqual(canonical["female"]["references"], [self.path.resolve()])
        self.assertEqual(canonical["female"]["signature"], baseline["female"]["signature"])
        self.assertEqual(canonical["male"]["signature"], baseline["male"]["signature"])

        # A valid replacement at the same path proves file SHA, not only the path,
        # participates in the real media_receipt/_target input signature.
        previous_sha = self.project["styles"][0]["preview"]["sha256"]
        self.write_image(self.path, (110, 170, 80))
        replacement = copy.deepcopy(compact_choice)
        replacement["styles"][0]["preview"] = media_receipt(self.path)
        self.assertNotEqual(replacement["styles"][0]["preview"]["sha256"], previous_sha)
        changed = self.capture_characters(replacement)
        self.assertEqual(changed["female"]["references"], [self.path.resolve()])
        self.assertNotEqual(changed["female"]["signature"], baseline["female"]["signature"])
        self.assertEqual(changed["male"]["references"], [])
        self.assertEqual(changed["male"]["signature"], baseline["male"]["signature"])

    def test_invalid_selected_preview_blocks_before_any_render(self):
        foreign = self.root / "production/factory-another-project/style_previews/style-2/take_001/image.png"
        wrong_style = self.root / "production" / self.slug / "style_previews/style-1/take_001/image.png"
        self.write_image(foreign, (20, 30, 40))
        self.write_image(wrong_style, (90, 80, 70))
        scenarios = []
        def changed(name, modify):
            project = copy.deepcopy(self.project)
            modify(project)
            scenarios.append((name, project))
        changed("missing_receipt", lambda p: p["styles"][0].pop("preview"))
        changed("empty_receipt", lambda p: p["styles"][0].update(preview={}))
        changed("missing_file", lambda p: p["styles"][0]["preview"].update(
            path="production/" + self.slug + "/style_previews/style-2/take_999/missing.png"))
        changed("bad_sha", lambda p: p["styles"][0]["preview"].update(sha256="0" * 64))
        changed("foreign_project", lambda p: p["styles"][0].update(preview=media_receipt(foreign)))
        changed("wrong_style_folder", lambda p: p["styles"][0].update(preview=media_receipt(wrong_style)))
        changed("wrong_preview_role", lambda p: p["styles"][0].update(preview_role="male"))
        changed("unknown_selected_style", lambda p: p.update(selected_style="style-missing"))
        for name, project in scenarios:
            with self.subTest(case=name), mock.patch.object(_Run, "_render_targets") as renderer:
                with self.assertRaises(ProductionNeedsDecision) as caught:
                    run = _Run(project, self.config, lambda patch: None, None)
                    run._characters_and_images()
                self.assertEqual(caught.exception.reason, "approved_preview_invalid")
                renderer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
