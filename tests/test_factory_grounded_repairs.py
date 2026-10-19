"""CPU-only regression cases for diagnosis-led, positively grounded repairs."""
from __future__ import annotations

import contextlib
import copy
import unittest
from unittest import mock

import pytest

from factory.production import _grounded_repair_plan
from test_factory_generation_pipeline import production, transport
from test_factory_team_ledger import project


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


@pytest.mark.parametrize('code,needs_frame', [('action', False), ('continuity', True), ('identity', True)])
def test_video_action_uses_existing_frame_while_other_frame_repairs_remain_protected(code, needs_frame):
    shot = {'id': 'S02', 'prompt': 'Medium close-up, closed lips.',
            'action': 'Lean slightly forward.', 'continuity': 'The same person remains in the hall.',
            'motion_prompt': 'A gentle camera push-in.'}
    plan = _grounded_repair_plan({'issues': [{'code': code, 'severity': 'blocking', 'detail': 'Untrusted failure prose.'}]},
                                 'video', 2, shot)
    assert plan['regenerate_keyframe'] is needs_frame
    assert plan['seed_delta'] != 0
    assert 'Untrusted failure prose' not in plan['prompt_prefix']
    for field in ('action', 'continuity', 'prompt', 'motion_prompt'):
        assert shot[field] in plan['prompt_prefix']


def test_late_mouth_failure_creates_compiled_video_retake_without_replacing_approved_frame(production, monkeypatch):
    from factory import production as renderer, generation_pipeline as pipeline
    from factory.video_providers import H3_PRODUCTION_PROFILE
    from factory.scheduler import ProductionNeedsDecision
    p, frame, config = production
    monkeypatch.setattr(renderer, 'ROOT', pipeline.ROOT)
    approved = {'id': 'S02', 'seconds': 5.708333333333333,
                'prompt': 'Medium close-up of an adult woman, leaning slightly forward; visible mouths stay closed.',
                'action': 'Lean slightly forward, maintaining closed lips throughout.',
                'motion_prompt': 'Slow camera push-in on her serious expression.',
                'camera_motion': 'push_in', 'characters': ['female'], 'reference_group': 'female',
                'continuity': 'The same woman stays in the hall.', 'dialogue': [],
                'image': {**pipeline.media_receipt(frame), 'qa': {'status': 'pass'}}}
    p['script']['shots'] = [approved]
    p['production'] = {'gpu_seconds': 651.07, 'elapsed_seconds': 400}
    cfg = {**config, 'video_connection': {'profile': 'local_h3', **H3_PRODUCTION_PROFILE},
           'h3_prompting': 'automatic', 'h3_prompt_config': config['generation_pipeline_config'],
           'review_only_existing_videos': False, 'max_attempts': 2,
           'video_width': 352, 'video_height': 640}
    calls = transport(monkeypatch, approved)
    run = renderer._Run(p, cfg, None, None)
    shot = run.shots[0]
    target = run._target('S02', run.folder / 'renders/S02', shot['motion_prompt'], [frame],
                         kind='video', seconds=shot['seconds'], shot=shot, review=True, reference_roles=['female'])
    assert target.get('generation_plan') and target['generation_plan']['phase'] == 'video'
    previous = {'take': 'take_004', 'attempt': 1, 'stage': 'video', 'state': 'failed', 'seed': 7,
                'source_signature': target['signature'], 'gpu_seconds': 651.07,
                'qa': {'status': 'fail', 'issues': [{'code': 'action', 'severity': 'blocking',
                        'detail': 'Mouth opens only in late video samples despite closed-mouth requirement.'}]},
                'generation_plan': copy.deepcopy(target['generation_plan'])}
    prior_path = target['folder'] / 'take_004/attempt.json'
    renderer.write_json(prior_path, previous)
    old_bytes = prior_path.read_bytes()
    old_frame = copy.deepcopy(shot['image'])
    initial_gpu = run.budget.snapshot()['gpu_seconds']
    initial_calls = len(calls)
    monkeypatch.setattr(run, '_team_admit_target', lambda target: None)
    monkeypatch.setattr(run, '_team_begin_target', lambda target, record: None)
    monkeypatch.setattr(run, '_remote_first_frame', lambda *a, **kw: pytest.fail('Approved frame must be reused'))
    monkeypatch.setattr(run, '_finish_h3_prompt', lambda *a, **kw: pytest.fail('Frozen compiled plan must stay intact'))
    record = run._new_attempt(target)
    assert record['take'] == 'take_005' and record['attempt'] == 2
    assert record['repair']['regenerate_keyframe'] is False
    assert record['seed'] != previous['seed']
    assert record['generation_plan'] == previous['generation_plan']
    assert record['prompt'].startswith(target['generation_plan']['parameters']['prompt'])
    for field in ('action', 'continuity', 'prompt', 'motion_prompt'):
        assert shot[field] in record['prompt']
    assert record['references'] == [pipeline.media_receipt(frame)]
    assert shot['image'] == old_frame and prior_path.read_bytes() == old_bytes
    assert run.budget.snapshot()['gpu_seconds'] == initial_gpu == 651.07
    assert len(calls) == initial_calls
    assert renderer.read_json(target['folder'] / 'take_005/attempt.json')['attempt'] == 2
    # The normal retry cap still applies after the new attempt; history is not reset.
    with pytest.raises(ProductionNeedsDecision) as stopped:
        run._new_attempt(target)
    assert stopped.value.reason == 'retry_limit'
    assert prior_path.read_bytes() == old_bytes
