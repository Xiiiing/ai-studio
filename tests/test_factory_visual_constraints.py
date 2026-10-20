import copy

import pytest

from factory import visual_constraints as v
from studio.core import sha256


@pytest.fixture
def case(tmp_path):
    path = tmp_path / 'candidate.mp4';path.write_bytes(b'actual candidate bytes')
    shot = {'id': 'S02', 'prompt': 'All dialogue is off-screen voice-over; visible mouths stay closed.'}
    config = {'production': {'production_recovery': {'version': 'ai-studio.production-recovery.v1', 'enabled': True}}}
    sampling = {'timestamps': [0.0, 1.667, 3.333, 5.0]}
    initial = {'status': 'pass', 'score': 92, 'issues': []}
    return path, shot, config, sampling, initial, tmp_path / 'review'


def invoke(case, request):
    path, shot, config, sampling, initial, output = case
    return v.verify(config, shot, path, ['a', 'b', 'c', 'd'], sampling,
                    initial, output, sha256(path), request, lambda image: 'pixel:'+image)


def observations(states):
    return {'observations': [{'sample': i, 'mouth': state, 'evidence': 'Visible lips touch.' if state == 'closed' else 'Visible mouth gap.'}
                              for i, state in enumerate(states, 1)]}


def test_high_general_score_does_not_clear_late_visible_violation(case):
    original = copy.deepcopy(case[4]);seen=[]
    def request(config, role, system, payload, schema, output, **kwargs):
        seen.append(kwargs['content'])
        return observations(['closed', 'closed', 'closed', 'open'])
    result = invoke(case, request)
    assert case[4] == original
    assert result['explicit_constraint_verification']['status'] == 'fail'
    assert result['issues'][-1]['severity'] == 'blocking'
    assert '5.000' in result['issues'][-1]['detail']
    assert [c['image_url']['url'] for c in seen[0] if c['type'] == 'image_url'] == ['pixel:a', 'pixel:b', 'pixel:c', 'pixel:d']
    assert '92' not in str(seen) and '先前' not in str(seen)
    assert (case[5] / 'explicit-constraints/evidence.json').exists()


@pytest.mark.parametrize('bad', [
    {'observations': [{'sample': 1, 'mouth': 'closed', 'evidence': 'Closed lips'}] * 4},
    {'observations': []},
    {'observations': [{'sample': 9, 'mouth': 'open', 'evidence': 'Gap'}] * 4},
])
def test_missing_duplicate_or_invented_frame_evidence_is_pending(case, bad):
    result = invoke(case, lambda *a, **k: bad)
    assert result['_constraint_unavailable'] is True
    assert result['explicit_constraint_verification']['status'] == 'unavailable'
    assert result['issues'] == []


def test_uncertain_frame_cannot_certify_constraint(case):
    result=invoke(case,lambda *a,**k:observations(['closed','closed','uncertain','closed']))
    assert result['_constraint_unavailable'] and result['explicit_constraint_verification']['status']=='unavailable'


def test_not_visible_is_distinct_from_closed(case):
    result=invoke(case,lambda *a,**k:observations(['not_visible']*4))
    audit=result['explicit_constraint_verification']
    assert audit['status']=='pass' and all(r['mouth']=='not_visible' for r in audit['observations'])
    assert audit['scope']=='actual_sampled_frames_not_exhaustive_video'


def test_late_changed_pixels_prevent_certification(case):
    def request(*a,**k):
        case[0].write_bytes(b'changed pixels')
        return observations(['closed']*4)
    result=invoke(case,request)
    assert result['_constraint_unavailable'] and 'changed' in result['explicit_constraint_verification']['reason']


def test_network_failure_is_pending_and_old_execution_does_not_call(case):
    def failure(*a,**k):raise TimeoutError('inspection unavailable')
    with pytest.raises(TimeoutError):
        invoke(case,failure)
    assert (case[5] / 'explicit-constraints/evidence.json').exists()
    case[2]['production'].clear()
    assert invoke(case,lambda *a,**k:pytest.fail('Legacy execution is frozen')) is case[4]


@pytest.mark.parametrize('text', ['Visible mouths remain naturally closed.', 'closed lips', '嘴巴保持闭合', '全程闭口'])
def test_only_explicit_approved_constraints_are_extracted(text):
    assert v.required({'prompt':text})[0]['id']=='visible_mouths_closed'
    assert v.required({'prompt':'A silent face, unspecified lips.'})==[]


def test_no_requirement_does_not_start_extra_model_request(case):
    case[1]['prompt']='A visible character.'
    assert invoke(case,lambda *a,**k:pytest.fail('No invented constraint')) is case[4]


@pytest.mark.parametrize('last_state,expected',[('open','fail'),('uncertain','unavailable'),('closed','pass')])
def test_actual_review_media_entry_enforces_independent_per_frame_evidence(case,monkeypatch,last_state,expected):
    from factory import llm
    path,shot,config,sampling,initial,output=case
    calls=[]
    def request(*args,**kwargs):
        calls.append(kwargs['content'])
        if len(calls)==1:return {**initial,'summary':'General review passes'}
        return observations(['closed','closed','closed',last_state])
    monkeypatch.setattr(llm,'_request',request)
    monkeypatch.setattr(llm,'_image_url',lambda value:'data:image/png;base64,ZmFrZQ==')
    monkeypatch.setattr('factory.evaluation_sampling.sample_video',lambda *a,**k:([{'image':str(i)} for i in range(4)],sampling))
    config['llm']={'model':'fixture'}
    result=llm.review_media({'slug':'factory-fixture','format':'animation'},shot,path,[],config,output_dir=output)
    assert result['status']==expected and len(calls)==2
    assert result['explicit_constraint_verification']['observations'][-1]['mouth']==last_state


@pytest.mark.parametrize('malformed',[None,'bad',{'policy':v.POLICY,'status':'pass'},
    {'policy':v.POLICY,'status':'pass','observations':[{'sample':None}]*4}])
def test_malformed_constraint_certificate_is_not_approval(case,malformed):
    assert not v.certified({'explicit_constraint_verification':malformed},case[0])


def test_execution_adapter_constraint_does_not_need_prompt_repetition():
    assert v.required({'prompt':'An adult listens.','execution_constraints':{'visible_mouths_closed':True}})
