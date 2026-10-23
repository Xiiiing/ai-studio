"""The visual critic receives current evidence, never previous verdicts."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from factory import llm


@pytest.mark.parametrize('severity,score,expected', [('minor',85,'pass'), ('minor',45,'fail'), ('blocking',95,'fail')])
@pytest.mark.parametrize('material_uncertain', [False, True])
def test_review_verdict_uses_severity_and_score_without_dropping_quality_gates(tmp_path, severity, score, expected, material_uncertain):
    answer = {'status':'fail', 'score':score, 'summary':'Current visual assessment',
              'issues':[{'code':'off_prompt','severity':severity,'detail':'Visible candidate evidence'}]}
    shot = {'id':'S04'}
    if material_uncertain:
        shot['material_constraints'] = {'identity':'approved adult'}
        answer['material_alignment'] = {'identity':'uncertain', 'style':'match', 'scene':'match',
                                        'correspondence':'match', 'evidence':'Current material observation'}
        if expected == 'pass':
            expected = 'unavailable'
    with patch.object(llm,'_image_url',return_value='data:image/png;base64,bW9jaw=='), \
         patch.object(llm,'_request',return_value=deepcopy(answer)):
        result = llm.review_media({'slug':'factory-severity','format':'animation'}, shot,
                                 tmp_path/'frame.png', [], {'llm':{'model':'fixture'}})
    assert result['status'] == expected
    assert result['model_status'] == 'fail' and result['issues'] == answer['issues']


@pytest.mark.parametrize("previous,fresh,expected", [
    ({"status": "fail", "score": 45},
     {"status": "pass", "score": 92, "summary": "Current candidate passes", "issues": []},
     "pass"),
    ({"status": "pass", "score": 100},
     {"status": "pass", "score": 95, "summary": "Missing required prop",
      "issues": [{"code": "continuity", "severity": "blocking", "detail": "Required prop is absent"}]},
     "fail"),
])
def test_visual_request_excludes_prior_verdict_and_worker_metadata(tmp_path, previous, fresh, expected):
    sample = tmp_path / "candidate.png"
    reference = tmp_path / "reference.png"
    sample.write_bytes(b"mock image")
    reference.write_bytes(b"mock reference")
    shot = {
        "id": "S03", "seconds": 4, "action": "The adult shows the silver key in one open hand.",
        "prompt": "Approved current scene", "reference_group": "male",
        "dialogue": [{"role": "male", "text": "请看。", "audio_path": "OLD_AUDIO_SECRET"}],
        "qa": {"semantic": {**previous, "summary": "OLD_REVIEW_SENTINEL"}},
        "attempts": [{"qa": previous, "path": "OLD_ATTEMPT_SENTINEL"}],
        "image": {"path": "OLD_IMAGE_SENTINEL"}, "status": "retrying",
        "audio_timing": {"diagnostic": "OLD_TIMING_SENTINEL"},
    }
    captured = {}
    def request(*args, **kwargs):
        captured["content"] = kwargs["content"]
        return deepcopy(fresh)
    config = {"quality": {"semantic_enabled": True, "minimum_score": 60},
              "llm": {"model": "test-visual-model"}}
    with patch.object(llm, "_image_url", return_value="data:image/png;base64,bW9jaw=="), \
         patch.object(llm, "_request", side_effect=request):
        project = {"slug": "factory-test-context", "format": "motion_comic",
                   "script": {"shots": [shot, {"id": "S07", "action": "The woman first gives him a food cup.",
                                             "continuity": "Food is introduced here.", "qa": "OLD_SEQUENCE_SENTINEL"}]}}
        result = llm.review_media(project, shot, sample, [reference], config)
    serialized = json.dumps(captured["content"], ensure_ascii=False)
    context = json.loads(captured["content"][0]["text"])
    assert context["shot"]["action"] == shot["action"]
    assert context["shot"]["prompt"] == shot["prompt"]
    assert context["approved_sequence"][1] == {"id": "S07", "action": "The woman first gives him a food cup.",
                                               "continuity": "Food is introduced here."}
    assert context["shot"]["dialogue"] == [{"role": "male", "text": "请看。"}]
    assert not any(key in context["shot"] for key in ("qa", "attempts", "image", "status", "audio_timing"))
    assert "OLD_" not in serialized
    assert len([item for item in captured["content"] if item["type"] == "image_url"]) == 2
    assert result["status"] == expected
    assert result["score"] == fresh["score"]
    assert shot["qa"]["semantic"]["summary"] == "OLD_REVIEW_SENTINEL"


@pytest.mark.parametrize("action_mismatch", [False, True])
def test_video_review_separates_observed_head_motion_from_approved_action_completion(tmp_path, action_mismatch):
    project = {"slug": "factory-human-motion-review", "format": "live_action", "creative_contract": {
        "schema_version": 1, "visual_style": "photorealistic", "story_mode": "standalone",
        "pacing": "short_drama", "motion": "generated_video"}}
    shot = {"id": "S01", "seconds": 5,
            "action": "The adult turns toward the door, then raises her right hand.",
            "motion_prompt": "Turn toward the door first, then raise the right hand."}
    issues = [{"code": "action", "severity": "blocking", "detail":
               "0至5秒可见转头和表情改变，但批准动作要求随后抬右手；全部样本中右手未抬起。"}] if action_mismatch else []
    fresh = {"status": "pass", "score": 99, "summary": "时间样本可见人物头部和表情变化", "issues": issues,
             "style_alignment": {"status": "match", "evidence": "全部样本中人物皮肤和场景呈写实影像"},
             "motion_alignment": {"status": "character_motion", "evidence":
                 "0.000秒面向前方、1.667秒转头，3.333秒表情改变；手部和躯干静止，镜头也有平移。"}}
    sampled = [{"image": tmp_path / f"sample-{index}.png"} for index in range(4)]
    seen = {}
    def request(*args, **kwargs):
        seen.update(content=kwargs["content"], schema=args[4])
        return deepcopy(fresh)
    with patch.object(llm, "_image_url", return_value="data:image/png;base64,bW9jaw=="), \
         patch("factory.evaluation_sampling.sample_video", return_value=(sampled, {
             "policy": "fixture", "timestamps": [0, 5 / 3, 10 / 3, 5]})), \
         patch.object(llm, "_request", side_effect=request):
        result = llm.review_media(project, shot, tmp_path / "candidate.mp4", [], {
            "llm": {"model": "fixture-vision"}, "quality": {"temporal_samples": 4}}, output_dir=tmp_path / "review")
    context = json.loads(seen["content"][0]["text"])
    rule = context["contract_rule"]
    assert "只分类时间样本之间实际可见的运动，不表示本镜批准动作已经兑现" in rule
    assert "转头、眨眼、眼神或面部表情改变" in rule
    assert "即使手部或躯干保持静止、相机也同时移动" in rule
    assert "所有主体姿态和表情均不变" in rule
    assert "抽帧本身是静态图片" in rule
    assert "code=action、severity=blocking并判fail" in rule
    assert "误标为camera_only" in rule and "无法确定时填uncertain" in rule
    assert context["shot"]["action"] == shot["action"]
    assert context["shot"]["motion_prompt"] == shot["motion_prompt"]
    assert "motion_alignment" in seen["schema"]["required"]
    labels = [part["text"] for part in seen["content"][1:] if part["type"] == "text"]
    assert "时间 0.000 秒" in labels[0] and "时间 5.000 秒" in labels[-1]
    assert result["motion_alignment"]["status"] == "character_motion"
    assert result["status"] == ("fail" if action_mismatch else "pass")
    assert result["issues"] == issues


def test_keyframe_excludes_future_story_beat_without_mutating_physical_state(tmp_path):
    project={'slug':'factory-starting-state','format':'animation','creative_contract':{
        'schema_version':1,'visual_style':'anime_2d','story_mode':'standalone','pacing':'short_drama','motion':'generated_video'}}
    shot={'id':'S04','prompt':'Kneeling with lowered head and empty hands; marks are visible.',
          'dramatic_beat':'FUTURE_DISCOVERY','action':'Open eyes and discover the marks.',
          'character_states':{'female':{'position':'Kneeling, head lowered','held_props':[],
                                       'story_state':'FUTURE_KNOWLEDGE'}}}
    original=deepcopy(shot);seen={}
    def request(*a,**kw):
        seen['context']=json.loads(kw['content'][0]['text'])
        return {'status':'pass','score':95,'summary':'Approved initial pose','issues':[],
                'style_alignment':{'status':'match','evidence':'Drawn outlines and hard cel shadows'}}
    with patch.object(llm,'_request',side_effect=request),patch.object(llm,'_image_url',return_value='data:image/png;base64,fixture'):
        result=llm.review_media(project,shot,tmp_path/'first.png',[],{'llm':{'model':'fixture'}},output_dir=tmp_path/'qa')
    assert result['status']=='pass' and shot==original
    assert 'FUTURE_' not in json.dumps(seen['context'])
    assert seen['context']['shot']['character_states']['female']=={'position':'Kneeling, head lowered','held_props':[]}


def test_image_review_and_repair_share_the_approved_starting_state(tmp_path,monkeypatch):
    from factory.production import _grounded_repair_plan
    shot={'id':'S14','prompt':'Man starts on one knee, holding the standing woman\'s empty hand.',
          'action':'FUTURE_ACTION stand all the way up','motion_prompt':'FUTURE_MOTION rise',
          'continuity':'FUTURE_CONTINUITY standing together',
          'character_states':{'male':{'position':'One knee down, free hand on bent knee.',
                                    'held_props':[],'story_state':'FUTURE_STORY grateful after standing'}}}
    calls=[]
    monkeypatch.setattr(llm,'_image_url',lambda p:'data:image/png;base64,bW9jaw==')
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:calls.append(json.loads(kw['content'][0]['text'])) or {
      'status':'pass','score':90,'summary':'Starting position is visible','issues':[],
      'style_alignment':{'status':'match','evidence':'Cel shaded drawn characters'}})
    project={'slug':'factory-starting-state','format':'animation','script':{'shots':[shot]},
             'creative_contract':{'schema_version':1,'visual_style':'anime_2d','story_mode':'standalone',
                                  'pacing':'short_drama','motion':'generated_video'}}
    result=llm.review_media(project,shot,tmp_path/'image.png',[],{'llm':{'model':'fixture'}})
    assert result['status']=='pass'
    context=calls[0]
    assert 'FUTURE_' not in json.dumps(context)
    repair=_grounded_repair_plan({'issues':[{'code':'action','severity':'blocking'}]},'image',2,shot)
    assert context['shot']['character_states']==json.loads(repair['positive_constraints'][1]['text'])
    assert context['shot']['prompt']==repair['positive_constraints'][0]['text']


def test_video_reassessment_uses_original_approved_action_without_amplifying_motion(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(llm,'_image_url',lambda p:'data:image/png;base64,bW9jaw==')
    monkeypatch.setattr('factory.evaluation_sampling.sample_video',lambda *a,**kw:(
      [{'timestamp':i,'image':tmp_path/'frame.png'} for i in range(4)],{'timestamps':[0,1,2,3]}))
    monkeypatch.setattr(llm,'_request',lambda *a,**kw:calls.append(json.loads(kw['content'][0]['text'])) or {
      'status':'pass','score':90,'summary':'Original subtle hand action is visible','issues':[],
      'style_alignment':{'status':'match','evidence':'Drawn cel shaded figures'},
      'motion_alignment':{'status':'character_motion','evidence':'Hands tighten slightly between actual timestamps'}})
    shot={'id':'S01','action':'Hold the cultivation seal while bracing against the vortex.',
          'motion_prompt':'The fingers tighten slightly and the aura pulses; the background man stays still.'}
    project={'slug':'factory-original-motion','format':'animation','creative_contract':{
       'schema_version':1,'visual_style':'anime_2d','story_mode':'standalone','pacing':'short_drama','motion':'generated_video'}}
    result=llm.review_media(project,shot,tmp_path/'unchanged.mp4',[],{'llm':{'model':'fixture'}})
    assert result['status']=='pass'
    assert calls[0]['shot']['action']==shot['action']
    assert calls[0]['shot']['motion_prompt']==shot['motion_prompt']
    assert '不得额外要求' in calls[0]['approved_action_rule']
    assert '特效变化不能冒充人物运动' in calls[0]['approved_action_rule']


@pytest.mark.parametrize('selected_key', ['selected_style', 'selected_style_id', 'style_id'])
def test_legacy_first_frame_keeps_selected_style_and_static_scope(tmp_path, monkeypatch, selected_key):
    shot = {'id': 'S07', 'prompt': 'Two women in a defensive stance, fantasy anime style.',
            'action': 'FUTURE_ACTION step back', 'motion_prompt': 'FUTURE_MOTION dolly',
            'continuity': 'FUTURE_END settle',
            'character_states': {'woman': {'position': 'knees bent', 'story_state': 'FUTURE_STORY'}}}
    project = {'slug': 'factory-legacy-style', 'format': 'animation', selected_key: 'chosen',
        'episode': {'shots': [shot], 'visual_style': 'unused fallback'},
        'styles': [{'id': 'chosen', 'prompt': 'High fidelity 3D cinematic animation with PBR materials.',
                    'preview': {'qa': 'OLD_REFERENCE_VERDICT'}}]}
    original = deepcopy((project, shot))
    seen = []
    answer = {'status': 'fail', 'score': 45, 'summary': 'Fixture visible mismatch remains blocking.',
              'issues': [{'code': 'identity', 'severity': 'blocking', 'detail': 'Synthetic actual candidate wardrobe mismatch.'}]}
    monkeypatch.setattr(llm, '_image_url', lambda p: 'data:image/png;base64,bW9jaw==')
    monkeypatch.setattr(llm, '_request', lambda *a, **kw: seen.append(json.loads(kw['content'][0]['text'])) or deepcopy(answer))
    result = llm.review_media(project, shot, tmp_path / 'candidate.png', [], {'llm': {'model': 'fixture'}})
    context = seen[0]
    assert context['approved_render_style'] == project['styles'][0]['prompt']
    assert context['approved_visual_style'] == {'id': 'chosen', 'prompt': project['styles'][0]['prompt']}
    assert '本身不等于二维' in context['approved_render_style_rule']
    assert '单帧无法证明' in context['keyframe_rule']
    assert 'FUTURE_' not in json.dumps(context) and 'OLD_REFERENCE_VERDICT' not in json.dumps(context)
    assert context['shot']['prompt'] == shot['prompt']
    assert result['status'] == 'fail' and result['issues'] == answer['issues']
    assert (project, shot) == original
