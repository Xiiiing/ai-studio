"""CPU-only regression cases for diagnosis-led, positively grounded repairs."""
from __future__ import annotations

import contextlib
import copy
import unittest
from unittest import mock

from factory.production import _grounded_repair_plan


class GroundedRepairTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("subprocess.Popen", "subprocess.run", "subprocess.check_output"):
            self.stack.enter_context(mock.patch(
                name, side_effect=AssertionError("Pure repair tests must not start subprocesses")))
        self.stack.enter_context(mock.patch(
            "factory.scheduler.gpu_snapshot",
            side_effect=AssertionError("Pure repair tests must not query GPUs")))

    def grounded(self, assessment, expected, stage="image", reference_roles=()):
        before = copy.deepcopy(assessment)
        plan = _grounded_repair_plan(
            assessment, stage, 2, expected=expected, reference_roles=reference_roles)
        self.assertEqual(assessment, before)
        self.assertEqual(plan["prompt_suffix"], "")
        self.assertIsInstance(plan["prompt_prefix"], str)
        self.assertIsInstance(plan["positive_constraints"], list)
        if plan["action"] == "repair_media_timing_or_encoding":
            self.assertEqual(plan["positive_constraints"], [])
            self.assertEqual(plan["prompt_prefix"], "")
        else:
            self.assertTrue(plan["positive_constraints"])
        for constraint in plan["positive_constraints"]:
            self.assertIsInstance(constraint["field"], str)
            self.assertIsInstance(constraint["text"], str)
        self.assertEqual(plan["diagnosis"]["issues"], assessment["issues"])
        return plan

    def test_scene_facts_win_over_character_words_and_media_codes_stay_technical(self):
        expected = {
            "id": "S01",
            "prompt": "The keyring hangs visibly from the convenience-store door handle.",
            "action": "钥匙圈挂在门把手上，女主双手空着，侧身看门。",
            "continuity": "钥匙圈和骨头挂件的位置固定在门把手。",
            "reference_group": "female",
        }
        sentinel = "FAILED_DETAIL_SENTINEL_DO_NOT_REPRODUCE"
        assessment = {"status": "fail", "issues": [
            {"code": "action", "severity": "blocking",
             "detail": "角色错误地提前拿走钥匙。" + sentinel},
            {"code": "continuity", "severity": "blocking",
             "detail": "角色动作导致门把手道具位置错误。"},
        ]}
        plan = self.grounded(assessment, expected, reference_roles=("female",))
        self.assertEqual(plan["action"], "repair_scene_action_and_continuity")
        self.assertNotIn("identity", plan["action"].lower())
        self.assertIn("door handle", plan["prompt_prefix"])
        self.assertIn("keyring", plan["prompt_prefix"])
        self.assertNotIn(sentinel, plan["prompt_prefix"])
        self.assertNotIn("提前拿走钥匙", plan["prompt_prefix"])
        self.assertEqual(plan["diagnosis"]["severity"], "blocking")

        for code in ("decode", "audio", "fps", "duration"):
            with self.subTest(media_code=code):
                failure = {"status": "fail", "issues": [
                    {"code": code, "severity": "blocking",
                     "detail": "角色和人脸相关描述不能覆盖媒体错误。" + sentinel}
                ]}
                technical = self.grounded(failure, expected, stage="video")
                self.assertEqual(technical["action"], "repair_media_timing_or_encoding")
                self.assertEqual(technical["diagnosis"]["primary_code"], code)
                self.assertNotIn(sentinel, technical["prompt_prefix"])

    def test_blocking_continuity_outranks_minor_identity_in_either_order(self):
        expected = {
            "id": "S03",
            "prompt": "Show the keyring and its small bone-shaped pendant clearly.",
            "action": "女主左手举起钥匙圈，骨头挂件清晰可见。",
            "continuity": "钥匙圈上的骨头挂件保持同一形状和位置。",
            "reference_group": "female",
        }
        continuity = {"code": "continuity", "severity": "blocking",
                      "detail": "未展示钥匙和挂件。FAILURE_FACT_MUST_STAY_IN_DIAGNOSIS"}
        identity = {"code": "identity", "severity": "minor",
                    "detail": "角色眉毛与前一镜有轻微不同。"}
        for ordered in ([continuity, identity], [identity, continuity]):
            with self.subTest(order=[issue["code"] for issue in ordered]):
                assessment = {"status": "fail", "issues": copy.deepcopy(ordered)}
                plan = self.grounded(assessment, expected, reference_roles=("female",))
                self.assertEqual(plan["diagnosis"]["primary_code"], "continuity")
                self.assertEqual(plan["diagnosis"]["severity"], "blocking")
                self.assertEqual(set(plan["diagnosis"]["codes"]), {"continuity", "identity"})
                self.assertEqual(len(plan["diagnosis"]["issues"]), 2)
                self.assertEqual(plan["action"], "repair_scene_action_and_continuity")
                self.assertIn("keyring", plan["prompt_prefix"])
                self.assertIn("bone-shaped pendant", plan["prompt_prefix"])
                self.assertNotIn("未展示钥匙", plan["prompt_prefix"])
                self.assertNotIn("FAILURE_FACT_MUST_STAY_IN_DIAGNOSIS", plan["prompt_prefix"])

    def test_identity_repair_preserves_two_people_and_independent_references(self):
        assessment = {"status": "fail", "issues": [
            {"code": "identity", "severity": "blocking",
             "detail": "角色身份漂移。IDENTITY_FAILURE_DETAIL"}
        ]}
        base = {
            "id": "S05",
            "prompt": "The foreground woman offers the key; the man remains visible beside the door in the background.",
            "action": "女主在前景递出钥匙，男主在背景伸手接住。",
            "continuity": "两人保持各自的脸、发型和服装。",
            "cast": ["female", "male"],
        }
        for group, roles in (("two", ("female", "male")),
                             ("female", ("female",)),
                             ("male", ("male",))):
            with self.subTest(reference_group=group):
                expected = {**base, "reference_group": group}
                plan = self.grounded(assessment, expected, reference_roles=roles)
                prefix = plan["prompt_prefix"]
                lower = prefix.lower()
                self.assertEqual(plan["diagnosis"]["primary_code"], "identity")
                self.assertIn("the man remains visible", prefix)
                self.assertIn("foreground woman", prefix)
                for forbidden in ("one clear subject", "single-subject", "single subject"):
                    self.assertNotIn(forbidden, lower)
                self.assertNotIn("IDENTITY_FAILURE_DETAIL", prefix)
                if group == "two":
                    self.assertRegex(lower, r"\bfemale\b")
                    self.assertRegex(lower, r"\bmale\b")
                    self.assertIn("reference image 1 corresponds to female", lower)
                    self.assertIn("reference image 2 corresponds to male", lower)
                    self.assertIn("preserve both approved characters", lower)
                else:
                    self.assertIn("foreground and background characters", lower)


if __name__ == "__main__":
    unittest.main()


def test_starting_pose_repair_never_reintroduces_full_future_action():
    expected = {'id':'S14', 'prompt':'Woman stands left. Man kneels on one knee right; their empty hands are clasped.',
                'action':'FUTURE_ACTION_SENTINEL pull him all the way to standing',
                'continuity':'FUTURE_CONTINUITY_SENTINEL now standing together',
                'motion_prompt':'FUTURE_MOTION_SENTINEL rise to standing',
                'character_states':{'male':{'position':'One knee down, free hand on bent knee.',
                                             'held_props':[], 'story_state':'FUTURE_STORY_SENTINEL stands with help'}}}
    before=copy.deepcopy(expected)
    problem={'issues':[{'code':'action','severity':'blocking','detail':'The man is already fully standing.'}]}
    image=_grounded_repair_plan(problem,'image',2,expected)
    assert expected==before
    assert 'one knee' in image['prompt_prefix'] and 'bent knee' in image['prompt_prefix']
    assert 'static STARTING frame' in image['prompt_prefix']
    assert 'FUTURE_' not in image['prompt_prefix']
    assert [v['field'] for v in image['positive_constraints']]==['prompt','character_states']
    video=_grounded_repair_plan(problem,'video',2,expected)
    assert 'FUTURE_ACTION_SENTINEL' in video['prompt_prefix']
    assert 'FUTURE_MOTION_SENTINEL' in video['prompt_prefix']
