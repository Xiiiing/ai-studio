"""Real CPU video frames exercise sparse-gap challenges; no model/GPU calls."""
import copy
import json

import av
from PIL import Image, ImageDraw
import pytest

from factory import llm, temporal_review
from factory.evaluation_sampling import sample_video


@pytest.fixture
def video(tmp_path):
    path = tmp_path / 'motion.mp4'
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=24)
        stream.width, stream.height, stream.pix_fmt = 160, 288, 'yuv420p'
        for index in range(48):
            image = Image.new('RGB', (160, 288), 'navy')
            ImageDraw.Draw(image).rectangle((40, 200-index*2, 65, 225-index*2), fill='cyan')
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def verdict(issues=None, score=95, motion='character_motion'):
    return {'status':'pass', 'score':score, 'summary':'Current visible evidence', 'issues':issues or [],
            'style_alignment':{'status':'match','evidence':'Drawn linework and cel-shaded subjects'},
            'motion_alignment':{'status':motion,'evidence':'Head and hand positions change across the actual timestamps'}}


GAP = {'code':'action', 'severity':'blocking', 'detail':'Between sparse samples the hand teleports; no in-between frames.'}


def gap_scope(start=2, end=3, issue_id='I1', claim_type='sampling_gap'):
    return {'from_sample': start, 'to_sample': end, 'issue_ids': [issue_id],
            'claim_type': claim_type, 'detail': 'Uncertain inference between the identified sparse samples.'}


def review(video, tmp_path, monkeypatch, *, disposition='refuted', initial=None, followup=None, evidence='adjacent', sample_count=8, finding_ids=None):
    project = {'slug':'factory-temporal-fixture','format':'animation','creative_contract':{
        'schema_version':1,'visual_style':'anime_2d','story_mode':'standalone','pacing':'short_drama','motion':'generated_video'}}
    calls = []
    def request(config, role, system, payload, schema, record, **kwargs):
        calls.append(kwargs['content'])
        if len(calls)==1:
            result = copy.deepcopy(initial if initial is not None else verdict([GAP],45))
            if GAP in result['issues'] and 'temporal_concerns' not in result:
                result['temporal_concerns'] = [gap_scope()]
            return result
        allowed=schema['properties']['findings']['items']['properties']['evidence_ids']['items']['enum']
        ids=[allowed[0], allowed[-1]]
        if evidence=='adjacent': ids=[allowed[0], allowed[7]]
        if evidence=='short_span': ids=allowed[:2]
        if evidence=='nonadjacent': ids=[allowed[0], 'D2F01']
        if evidence=='fabricated':ids=['D9F99']
        issue_ids=schema['properties']['findings']['items']['properties']['issue_id'].get('enum', [])
        if finding_ids is not None: issue_ids = finding_ids
        return {'review':copy.deepcopy(followup or verdict()),'findings':[
            {'issue_id': ident,'status':disposition.get(ident, 'uncertain') if isinstance(disposition, dict) else disposition,'evidence_ids':ids,
             'detail':'The cited decoded span shows the intermediate approved hand trajectory.'} for ident in reversed(issue_ids)]}
    monkeypatch.setattr(llm,'_request',request)
    result = llm.review_media(project, {'id':'S01','action':'Raise the hand in one continuous movement.'}, video, [],
        {'llm':{'model':'fixture-vision','vision':True},'quality':{'temporal_samples':sample_count}},output_dir=tmp_path/'qa')
    return result, calls


def test_dense_windows_are_true_adjacent_frames_not_resampled_interpolation(video):
    samples,_=sample_video(video,count=8)
    windows=temporal_review.continuous_windows(video,samples,[{'from_sample':2,'to_sample':5,'detail':'Candidate interval'}])
    assert len(windows)==1
    frames=windows[0]['frames']
    assert 12 <= len(frames) <= 32 and windows[0]['consecutive']
    assert frames[0]['frame_index'] <= samples[1]['frame_index']
    assert frames[-1]['frame_index'] >= samples[4]['frame_index']
    assert [f['frame_index'] for f in frames]==list(range(frames[0]['frame_index'],frames[0]['frame_index']+len(frames)))
    assert all(abs(b['timestamp']-a['timestamp']-1/24)<.00001 for a,b in zip(frames,frames[1:]))


def test_sparse_gap_refuted_by_adjacent_evidence_can_pass_without_regeneration(video,tmp_path,monkeypatch):
    result,calls=review(video,tmp_path,monkeypatch)
    assert result['status']=='pass' and result['score']==95
    assert result['issues']==[] and len(calls)==2
    assert result['temporal_verification']['initial_review']['score']==45
    assert result['temporal_verification']['findings'][0]['status']=='refuted'
    context=json.loads(calls[0][0]['text'])
    assert context['temporal_sampling']['frames_are_consecutive'] is False
    assert '不等于原视频缺帧' in context['temporal_rule']
    assert any('连续窗口D1' in p.get('text','') for p in calls[1])


@pytest.mark.parametrize('disposition,expected',[('confirmed','fail'),('uncertain','unavailable')])
def test_actual_defect_blocks_and_unresolved_evidence_preserves_media(video,tmp_path,monkeypatch,disposition,expected):
    result,_=review(video,tmp_path,monkeypatch,disposition=disposition)
    assert result['status']==expected
    if expected=='fail':assert GAP in result['issues']
    assert video.is_file()


@pytest.mark.parametrize('evidence',['nonadjacent','fabricated'])
def test_uncited_or_nonadjacent_evidence_never_clears_temporal_failure(video,tmp_path,monkeypatch,evidence):
    result,_=review(video,tmp_path,monkeypatch,evidence=evidence)
    assert result['status']=='unavailable'


def test_cited_span_is_grounded_by_all_intervening_original_frames(video,tmp_path,monkeypatch):
    result,_=review(video,tmp_path,monkeypatch,evidence='span')
    assert result['status']=='pass'
    window=result['temporal_verification']['windows'][0]
    assert len(window['frames'])==12
    assert [f['frame_index'] for f in window['frames']]==list(range(
        window['frames'][0]['frame_index'],window['frames'][-1]['frame_index']+1))


def test_temporal_reassessment_cannot_clear_identity_or_style_errors(video,tmp_path,monkeypatch):
    identity={'code':'identity','severity':'blocking','detail':'Wrong approved character is visible.'}
    result,_=review(video,tmp_path,monkeypatch,initial=verdict([GAP,identity],40))
    assert result['status']=='fail' and identity in result['issues']
    result,_=review(video,tmp_path,monkeypatch,followup={**verdict(),'style_alignment':{'status':'mismatch','evidence':'Photorealistic image replaces the requested drawn anime style'}})
    assert result['status']=='fail'


def test_contradictory_new_gap_verdict_requires_review_not_automatic_retake(video,tmp_path,monkeypatch):
    result,_=review(video,tmp_path,monkeypatch,followup=verdict([GAP],40))
    assert result['status']=='unavailable'


def test_small_context_does_not_drop_references_or_approve(video,tmp_path,monkeypatch):
    monkeypatch.setattr('factory.providers.context_budget',lambda *a,**k:{'fits':False})
    monkeypatch.setattr('factory.config.resolve_llm',lambda *a,**k:{'model':'fixture','profile':'fixture','context_tokens':100})
    result,calls=review(video,tmp_path,monkeypatch)
    assert len(calls)==1 and result['status']=='unavailable'
    assert result['temporal_verification']['reason']=='continuous_review_context_too_small'


def test_absence_claim_checks_early_action_even_when_claims_point_only_to_late_frames(video):
    samples,_=sample_video(video,count=8)
    late=[{'from_sample':6,'to_sample':8,'detail':'Claim that the whole shot is static'}]
    windows=temporal_review.continuous_windows(video,samples,late,cover_full_clip=True)
    assert len(windows)==2
    assert windows[0]['frames'][0]['timestamp']<1.0
    assert windows[1]['frames'][-1]['timestamp']>=1.0
    for window in windows:
        frames=window['frames']
        assert [f['frame_index'] for f in frames]==list(range(frames[0]['frame_index'],frames[-1]['frame_index']+1))


@pytest.fixture
def late_mouth_video(tmp_path):
    """Six-second CPU fixture; early frames closed, late visible mouth open."""
    path = tmp_path / 'late-mouth.mp4'
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=24)
        stream.width, stream.height, stream.pix_fmt = 160, 288, 'yuv420p'
        for index in range(141):
            image = Image.new('RGB', (160, 288), 'navy')
            draw = ImageDraw.Draw(image)
            draw.ellipse((35, 40, 125, 150), fill='pink')
            draw.rectangle((65, 115, 95, 137 if index >= 70 else 116), fill='black')
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


MOUTH = {'code': 'action', 'severity': 'blocking',
         'detail': 'The visible mouth opens in the late samples despite the closed-mouth requirement.'}


def test_visible_mouth_defect_without_temporal_concern_is_not_challenged(late_mouth_video, tmp_path, monkeypatch):
    initial = {**verdict([MOUTH], 45), 'temporal_concerns': []}
    result, calls = review(late_mouth_video, tmp_path, monkeypatch, initial=initial)
    assert len(calls) == 1 and result['status'] == 'fail'
    assert MOUTH in result['issues']


def test_early_closed_windows_cannot_clear_late_sparse_mouth_evidence(late_mouth_video, tmp_path, monkeypatch):
    samples, _ = sample_video(late_mouth_video, count=8)
    # Independent motion clarification sees only early closed-mouth windows.
    # Every late original sample must still be supplied, and its issue protected.
    windows = []
    with av.open(str(late_mouth_video)) as container:
        decoded = list(container.decode(video=0))
    for number, start in enumerate((12, 36), 1):
        windows.append({'image': decoded[start].to_image(), 'consecutive': True,
            'frames': [{'id': f'D{number}F{i+1:02d}', 'frame_index': start+i,
                        'timestamp': (start+i)/24} for i in range(12)]})
    monkeypatch.setattr(temporal_review, 'continuous_windows', lambda *args, **kwargs: windows)
    initial = {**verdict([MOUTH], 45, motion='uncertain'), 'temporal_concerns': []}
    result, calls = review(late_mouth_video, tmp_path, monkeypatch, initial=initial)
    assert len(calls) == 2 and result['status'] == 'fail' and MOUTH in result['issues']
    assert result['temporal_verification']['review']['issues'] == []
    supplied = calls[1]
    assert len([part for part in supplied if part.get('text', '').startswith('间隔样本S')]) == 8
    late_index = next(i for i, part in enumerate(supplied) if part.get('text', '').startswith('间隔样本S8'))
    assert supplied[late_index+1]['image_url']['url'] == llm._image_url(samples[-1]['image'])
    payload = json.loads(supplied[0]['text'])
    assert payload['protected_visible_issues'] == [{'issue_id': 'I1', 'claim': MOUTH}]


def test_refuting_linked_gap_never_clears_another_visible_issue(video, tmp_path, monkeypatch):
    initial = {**verdict([MOUTH, GAP], 45), 'temporal_concerns': [gap_scope(issue_id='I2')]}
    result, calls = review(video, tmp_path, monkeypatch, initial=initial)
    assert result['status'] == 'fail' and MOUTH in result['issues'] and GAP not in result['issues']
    assert result['temporal_verification']['findings'][0]['issue_id'] == 'I2'


def test_local_normal_span_does_not_refute_whole_suspect_interval(video, tmp_path, monkeypatch):
    result, _ = review(video, tmp_path, monkeypatch, evidence='short_span')
    assert result['status'] == 'unavailable' and GAP in result['issues']
    audit = result['temporal_verification']
    assert audit['reason'] == 'continuous_review_refutation_outside_issue_scope'
    assert audit['initial_review']['issues'] == [GAP]
    assert audit['proposed_findings'][0]['status'] == 'refuted'  # Rejected answer stays evidence.
    assert 'findings' not in audit


def test_normal_six_second_sparse_interval_gets_complete_bounded_window(late_mouth_video, tmp_path, monkeypatch):
    result, _ = review(late_mouth_video, tmp_path, monkeypatch, evidence='span')
    assert result['status'] == 'pass'
    frames = result['temporal_verification']['windows'][0]['frames']
    assert 20 <= len(frames) <= temporal_review.MAX_WINDOW_FRAMES
    assert frames[0]['frame_index'] == 20 and frames[-1]['frame_index'] == 40


def test_interval_beyond_window_limit_stays_pending_instead_of_false_refutation(late_mouth_video, tmp_path, monkeypatch):
    initial = {**verdict([GAP], 45), 'temporal_concerns': [gap_scope(1, 8)]}
    result, _ = review(late_mouth_video, tmp_path, monkeypatch, initial=initial, evidence='span')
    assert result['status'] == 'unavailable' and GAP in result['issues']
    assert result['temporal_verification']['reason'] == 'continuous_review_refutation_outside_issue_scope'
    assert all(len(w['frames']) <= 32 for w in result['temporal_verification']['windows'])


def test_scoped_absence_can_be_refuted_by_positive_approved_motion(video, tmp_path, monkeypatch):
    absent = {'code': 'action', 'severity': 'blocking', 'detail': 'No approved hand movement occurs anywhere in the clip.'}
    initial = {**verdict([absent], 45, motion='camera_only'),
               'temporal_concerns': [gap_scope(1, 8, claim_type='motion_absence')]}
    result, _ = review(video, tmp_path, monkeypatch, initial=initial)
    assert result['status'] == 'pass' and result['issues'] == []
    assert len(result['temporal_verification']['windows']) == 2


def test_local_stillness_cannot_confirm_global_absence_of_approved_motion(late_mouth_video, tmp_path, monkeypatch):
    absent = {'code': 'action', 'severity': 'blocking', 'detail': 'No approved motion anywhere.'}
    initial = {**verdict([absent], 45, motion='camera_only'),
               'temporal_concerns': [gap_scope(1, 8, claim_type='motion_absence')]}
    result, _ = review(late_mouth_video, tmp_path, monkeypatch, initial=initial, disposition='confirmed')
    assert result['status'] == 'unavailable' and absent in result['issues']


def test_motion_uncertainty_without_issues_still_gets_independent_clarification(video, tmp_path, monkeypatch):
    initial = {**verdict([], 45, motion='uncertain'), 'temporal_concerns': []}
    result, calls = review(video, tmp_path, monkeypatch, initial=initial)
    assert result['status'] == 'pass' and len(calls) == 2


def test_context_limit_cannot_remove_original_sparse_samples(video, tmp_path, monkeypatch):
    image_counts = []
    def limited(profile, messages, max_tokens):
        content = messages[-1]['content']
        count = sum(part.get('type') == 'image_url' for part in content)
        image_counts.append(count)
        return {'fits': count <= 10}
    monkeypatch.setattr('factory.providers.context_budget', limited)
    monkeypatch.setattr('factory.config.resolve_llm', lambda *a, **k: {'context_tokens': 100})
    result, calls = review(video, tmp_path, monkeypatch, sample_count=16)
    assert result['status'] == 'unavailable' and len(calls) == 1
    assert len(image_counts) == 1 and image_counts[0] >= 17
    assert result['temporal_verification']['reason'] == 'continuous_review_context_too_small'


def test_missing_or_unrelated_issue_binding_requires_review(video, tmp_path, monkeypatch):
    initial = {**verdict([GAP], 45), 'temporal_concerns': [gap_scope(issue_id='I9')]}
    result, calls = review(video, tmp_path, monkeypatch, initial=initial)
    assert result['status'] == 'unavailable' and len(calls) == 1
    assert result['temporal_verification']['reason'] == 'continuous_review_issue_scope_missing'


def test_minor_confirmation_cannot_approve_an_unresolved_blocking_claim(video, tmp_path, monkeypatch):
    minor = {**GAP, 'severity': 'minor', 'detail': 'Possible small pause between sparse samples.'}
    initial = {**verdict([minor, GAP], 45),
               'temporal_concerns': [{**gap_scope(), 'issue_ids': ['I1', 'I2']}]}
    result, _ = review(video, tmp_path, monkeypatch, initial=initial,
                       disposition={'I1': 'confirmed', 'I2': 'uncertain'}, followup=verdict([minor]))
    assert result['status'] == 'unavailable'
    assert result['temporal_verification']['status'] == 'uncertain'


def test_confirmed_blocking_claim_still_fails_with_an_unresolved_minor_claim(video, tmp_path, monkeypatch):
    minor = {**GAP, 'severity': 'minor', 'detail': 'Possible small pause between sparse samples.'}
    initial = {**verdict([minor, GAP], 45),
               'temporal_concerns': [{**gap_scope(), 'issue_ids': ['I1', 'I2']}]}
    result, _ = review(video, tmp_path, monkeypatch, initial=initial,
                       disposition={'I1': 'uncertain', 'I2': 'confirmed'}, followup=verdict([GAP]))
    assert result['status'] == 'fail' and GAP in result['issues']


def test_rejected_confirmation_is_not_a_validated_blocking_finding(late_mouth_video, tmp_path, monkeypatch):
    initial = {**verdict([GAP], 45), 'temporal_concerns': [gap_scope(1, 8, claim_type='motion_absence')]}
    result, _ = review(late_mouth_video, tmp_path, monkeypatch, initial=initial, disposition='confirmed')
    assert result['status'] == 'unavailable'
    audit = result['temporal_verification']
    assert audit['proposed_findings'][0]['status'] == 'confirmed'
    assert 'findings' not in audit and audit['followup_review']


def test_all_six_linked_claims_are_retained_when_challenge_capacity_is_exceeded(video, tmp_path, monkeypatch):
    issues = [{**GAP, 'detail': f'Uncertain temporal claim {i}'} for i in range(6)]
    initial = {**verdict(issues, 45), 'temporal_concerns': [
        {**gap_scope(), 'issue_ids': ['I1', 'I2', 'I3']},
        {**gap_scope(5, 6), 'issue_ids': ['I4', 'I5', 'I6']}]}
    result, calls = review(video, tmp_path, monkeypatch, initial=initial)
    assert result['status'] == 'unavailable' and len(calls) == 1
    audit = result['temporal_verification']
    assert audit['reason'] == 'continuous_review_issue_capacity'
    assert set(audit['issue_scopes']) == {f'I{i}' for i in range(1, 7)}
    assert audit['initial_review']['issues'] == issues


@pytest.mark.parametrize('finding_ids', [['I9'], ['I1', 'I1']])
def test_foreign_or_duplicate_issue_ids_cannot_clear_a_failure(video, tmp_path, monkeypatch, finding_ids):
    result, _ = review(video, tmp_path, monkeypatch, finding_ids=finding_ids)
    assert result['status'] == 'unavailable' and GAP in result['issues']
    assert 'findings' not in result['temporal_verification']
    assert result['temporal_verification']['proposed_findings']


@pytest.mark.parametrize('concerns', [None, [{}], [{'from_sample': 'bad', 'to_sample': 2}]])
def test_malformed_concerns_preserve_original_review_without_crashing(video, tmp_path, monkeypatch, concerns):
    samples, sampling = sample_video(video, count=8)
    initial = {**verdict([GAP], 45), 'temporal_concerns': concerns}
    monkeypatch.setattr(llm, '_request', lambda *a, **kw: pytest.fail('Malformed scope cannot start another review'))
    result = temporal_review.verify_temporal({}, {}, {}, [], samples, sampling, initial, video, tmp_path / 'audit')
    assert result['_temporal_unavailable'] is True and result['issues'] == [GAP]
    assert result['temporal_verification']['initial_review'] == initial
    assert result['temporal_verification']['reason'] == 'continuous_review_issue_scope_invalid'
