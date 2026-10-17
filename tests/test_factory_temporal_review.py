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


def review(video, tmp_path, monkeypatch, *, disposition='refuted', initial=None, followup=None, evidence='adjacent'):
    project = {'slug':'factory-temporal-fixture','format':'animation','creative_contract':{
        'schema_version':1,'visual_style':'anime_2d','story_mode':'standalone','pacing':'short_drama','motion':'generated_video'}}
    calls = []
    def request(config, role, system, payload, schema, record, **kwargs):
        calls.append(kwargs['content'])
        if len(calls)==1:
            return copy.deepcopy(initial or verdict([GAP],45))
        allowed=schema['properties']['findings']['items']['properties']['evidence_ids']['items']['enum']
        ids=allowed[:2] if evidence=='adjacent' else [allowed[0],allowed[12]]
        if evidence=='span':ids=[allowed[0],allowed[11]]
        if evidence=='fabricated':ids=['D9F99']
        return {'review':copy.deepcopy(followup or verdict()),'findings':[
            {'issue_id':'I1','status':disposition,'evidence_ids':ids,'detail':'Adjacent decoded frames show the intermediate hand trajectory.'}]}
    monkeypatch.setattr(llm,'_request',request)
    result = llm.review_media(project, {'id':'S01','action':'Raise the hand in one continuous movement.'}, video, [],
        {'llm':{'model':'fixture-vision','vision':True},'quality':{'temporal_samples':8}},output_dir=tmp_path/'qa')
    return result, calls


def test_dense_windows_are_true_adjacent_frames_not_resampled_interpolation(video):
    samples,_=sample_video(video,count=8)
    windows=temporal_review.continuous_windows(video,samples,[{'from_sample':2,'to_sample':5,'detail':'Candidate interval'}])
    assert len(windows)==1
    frames=windows[0]['frames']
    assert len(frames)==12 and windows[0]['consecutive']
    assert [f['frame_index'] for f in frames]==list(range(frames[0]['frame_index'],frames[0]['frame_index']+12))
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
