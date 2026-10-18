"""Static contact challenges use real source pixels without certifying by crop."""
import json
from PIL import Image,ImageDraw
import pytest
from factory import llm

GAP={'code':'action','severity':'blocking','detail':'Energy connection does not contact the palm.'}
def verdict(issues=None):
 return {'status':'pass','score':90,'summary':'Visible contact evidence','issues':issues or [],
         'style_alignment':{'status':'match','evidence':'Drawn outlines and cel shadows'}}

def run_review(tmp_path,monkeypatch,status='refuted',extra=None,ids=None,small=False):
 path=tmp_path/'image.png';im=Image.new('RGB',(736,1312),'navy')
 ImageDraw.Draw(im).line((200,650,500,670),fill='purple',width=8);im.save(path)
 calls=[]
 def request(*a,**kw):
  calls.append(kw['content'])
  if len(calls)==1:return verdict([GAP,*(extra or [])])
  return {'review':verdict(),'findings':[{'issue_id':'I1','status':status,
   'evidence_ids':ids or ['F0','C1'],'detail':'Purple line reaches the hand contact area in the full image and source crop.'}]}
 monkeypatch.setattr(llm,'_request',request)
 if small:
  monkeypatch.setattr('factory.config.resolve_llm',lambda *a,**kw:{'model':'fixture','profile':'fixture','context_tokens':100})
  monkeypatch.setattr('factory.providers.context_budget',lambda *a,**kw:{'fits':False})
 project={'id':'fixture','slug':'factory-spatial','format':'animation','creative_contract':{
  'schema_version':1,'visual_style':'anime_2d','story_mode':'standalone','pacing':'short_drama','motion':'generated_video'}}
 result=llm.review_media(project,{'id':'S04','prompt':'Palm touches upper back; energy joins the contact hand.'},path,[],
  {'llm':{'model':'fixture','vision':True}},output_dir=tmp_path/'qa')
 return result,calls

@pytest.mark.parametrize('disposition,expected',[('refuted','pass'),('confirmed','fail'),('uncertain','unavailable')])
def test_spatial_dispositions_preserve_real_gates(tmp_path,monkeypatch,disposition,expected):
 result,calls=run_review(tmp_path,monkeypatch,disposition)
 assert result['status']==expected and len(calls)==2
 assert result['spatial_verification']['initial_review']['issues']==[GAP]
 if disposition=='confirmed':assert GAP in result['issues']
 context=json.loads(calls[1][0]['text'])
 assert context['source_dimensions']==[736,1312]
 assert len(context['detail_images'])==2
 assert len([part for part in calls[1] if part['type']=='image_url'])==3

@pytest.mark.parametrize('ids',[['C1','C2'],['F0','invented']])
def test_missing_full_frame_or_invalid_crop_cannot_clear_failure(tmp_path,monkeypatch,ids):
 result,_=run_review(tmp_path,monkeypatch,ids=ids);assert result['status']=='unavailable'

def test_spatial_challenge_cannot_clear_identity_or_unrelated_action(tmp_path,monkeypatch):
 for issue in [{'code':'identity','severity':'blocking','detail':'Wrong character.'},
               {'code':'action','severity':'blocking','detail':'Required sword is missing.'}]:
  result,_=run_review(tmp_path,monkeypatch,extra=[issue]);assert result['status']=='fail' and issue in result['issues']

def test_insufficient_context_preserves_references_and_media(tmp_path,monkeypatch):
 result,calls=run_review(tmp_path,monkeypatch,small=True)
 assert result['status']=='unavailable' and len(calls)==1
 assert (tmp_path/'image.png').exists()
