"""CPU integration contract for a shot-only visual-style override."""
from __future__ import annotations

import contextlib
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from factory.production import _Run
from studio.core import media_receipt


class ShotStyleTests(unittest.TestCase):
    def test_override_changes_only_selected_shot_compilation_and_signature(self):
        with tempfile.TemporaryDirectory(prefix="factory-shot-style-") as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            stack.enter_context(mock.patch("factory.production.ROOT", root))
            stack.enter_context(mock.patch("studio.core.ROOT", root))
            for name in ("subprocess.Popen", "subprocess.run", "subprocess.check_output"):
                stack.enter_context(mock.patch(
                    name, side_effect=AssertionError("This test must not start subprocesses")))
            stack.enter_context(mock.patch(
                "factory.scheduler.gpu_snapshot",
                side_effect=AssertionError("This test must not query GPUs")))
            slug = "factory-shot-style-test"
            folder = root / "production" / slug

            def receipt(relative, color):
                path = folder / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (16, 24), color).save(path)
                return media_receipt(path)

            preview = receipt("style_previews/style-2/take_001/image.png", (90, 130, 150))
            characters = {
                "female": receipt("assets/characters/female/fixture.png", (110, 90, 130)),
                "male": receipt("assets/characters/male/fixture.png", (80, 100, 70)),
            }
            global_scene = "GLOBAL_RED_STOREFRONT_NEON_WORDS"
            project = {
                "id": "shot-style-test", "slug": slug, "format": "motion_comic",
                "selected_style": {"id": "style-2"},
                "styles": [{"id": "style-2", "prompt": global_scene + ", soft watercolor",
                            "preview_role": "female", "preview": preview}],
                "assets": {"characters": characters},
                "episode": {
                    "title": "CPU shot style fixture",
                    "cast": {"female": "Adult woman with a black bob",
                             "male": "Adult man in a navy jacket"},
                    "shots": [
                        {"id": "S01", "seconds": 4, "reference_group": "female",
                         "prompt": "The woman sits at a plain table."},
                        {"id": "S02", "seconds": 4, "reference_group": "male",
                         "prompt": "The man reads a letter indoors."},
                    ],
                },
            }
            config = {"gpu_cards": [0], "max_wall_seconds": 120,
                      "max_gpu_seconds": 120, "max_attempts": 2}

            def capture(value):
                run = _Run(value, config, lambda patch: None, None)
                captured = {}

                def render(targets, phase):
                    self.assertIn(phase, ("character_references", "story_frames"))
                    for target in targets:
                        captured[target["key"]] = {
                            "prompt": target["prompt"],
                            "source_signature": target["signature"],
                        }
                    return []

                with mock.patch.object(run, "_render_targets", side_effect=render):
                    run._characters_and_images()
                self.assertEqual(set(captured), {"character_female", "character_male", "S01", "S02"})
                return captured

            baseline = capture(copy.deepcopy(project))
            override = copy.deepcopy(project)
            override["episode"]["shots"][0]["render_style_prompt"] = "Muted watercolor pigments, fine paper texture."
            changed = capture(override)
            self.assertIn(global_scene, baseline["S01"]["prompt"])
            self.assertNotIn(global_scene, changed["S01"]["prompt"])
            self.assertIn("Muted watercolor pigments, fine paper texture.", changed["S01"]["prompt"])
            self.assertIn("The woman sits at a plain table.", changed["S01"]["prompt"])
            self.assertNotEqual(baseline["S01"]["source_signature"], changed["S01"]["source_signature"])
            for key in ("S02", "character_female", "character_male"):
                self.assertEqual(changed[key], baseline[key], key)

            for invalid in (None, "", " \n ", 0, False, [], {}):
                with self.subTest(invalid=repr(invalid)):
                    bad = copy.deepcopy(project)
                    bad["episode"]["shots"][0]["render_style_prompt"] = invalid
                    with self.assertRaisesRegex(ValueError, "render_style_prompt"):
                        capture(bad)


if __name__ == "__main__":
    unittest.main()
