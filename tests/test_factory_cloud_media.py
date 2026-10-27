"""Paid media protocol and complete CPU production with no local inference."""
import copy
import io
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import wave
import numpy as np
from PIL import Image, ImageDraw
import pytest

from factory import cloud_media as cm, cloud_budget as costs, config, store, production, capabilities
from factory.media_profiles import normalize_media, resolve
from factory.scheduler import ProductionNeedsDecision, Scheduler
from studio import core


@pytest.fixture(params=["environment", "managed"])
def cloud(tmp_path,monkeypatch,request):
    for module in (core,production,__import__('factory.speech',fromlist=['ROOT']),__import__('factory.audio_editor',fromlist=['ROOT']),__import__('studio.media',fromlist=['ROOT'])):
        monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(cm,'DATA',tmp_path/'db')
    monkeypatch.setattr(store,'DATA',tmp_path/'db');monkeypatch.setattr(store,'DATABASE',tmp_path/'db/studio.sqlite3')
    store.initialize();monkeypatch.setenv('FIXTURE_CLOUD_KEY','fixture-private-key')
    credential_name='FIXTURE_CLOUD_KEY'
    if request.param=='managed':
        from factory import connection_secrets as vault
        monkeypatch.setattr(vault,'vault_path',lambda:tmp_path/'keys.json')
        credential_name=vault.PREFIX+'a'*32
        vault.write_vault({credential_name:'fixture-private-key'})
        monkeypatch.delenv('FIXTURE_CLOUD_KEY')
    calls=[];assets={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path,body,dict(self.headers)))
            if self.path.endswith('/images/generations'):
                w,h=map(int,body['size'].split('x'));image=Image.new('RGB',(w,h),(90,130,180));d=ImageDraw.Draw(image)
                for x in range(0,w,24):d.rectangle((x,0,x+8,h),fill=(130,170,210))
                d.ellipse((w//4,h//5,3*w//4,3*h//5),fill=(213,177,142))
                buf=io.BytesIO();image.save(buf,format='PNG');name=f'/image-{len(calls)}';assets[name]=buf.getvalue()
                answer={'data':[{'url':base+name}],'usage':{'output_image_count':1}}
            elif self.path.endswith('/services/aigc/multimodal-generation/generation'):
                buf=io.BytesIO()
                samples=(np.sin(np.arange(24000)*2*np.pi*220/24000)*5000).astype('<i2')
                with wave.open(buf,'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(samples.tobytes())
                name=f'/audio-{len(calls)}';assets[name]=buf.getvalue();answer={'output':{'audio':{'url':base+name}},'request_id':'tts-fixture'}
            elif self.path.endswith('/video_generation'):answer={'task_id':'fixture-video'}
            else: self.send_response(404);self.end_headers();return
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps({**answer,'base_resp':{'status_code':0}}).encode())
        def do_GET(self):
            calls.append((self.path,None,dict(self.headers)))
            if self.path.startswith('/v1/query/video_generation'):
                result={'task_id':'fixture-video','status':'Success','file_id':'fixture-file'}
            elif self.path.startswith('/v1/files/retrieve'):
                result={'file':{'file_id':'fixture-file','download_url':base+'/video'}}
            else:
                self.send_response(200);self.end_headers();self.wfile.write(assets[self.path]);return
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps({**result,'base_resp':{'status_code':0}}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    base=f'http://127.0.0.1:{server.server_port}'
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    image=normalize_media({'provider':'dashscope_image','model':'qwen-image-2.1-pro','enabled':True,'base_url':base+'/v1','network_mode':'loopback','api_key_env':credential_name},'image')
    audio=normalize_media({'provider':'dashscope_tts','model':'qwen3-tts-flash','enabled':True,'base_url':base+'/api/v1','network_mode':'loopback','api_key_env':credential_name},'audio')
    cfg={'schema_version':1,'image_profiles':{'api':image},'image_roles':{'image_generator':'api'},'audio_profiles':{'api':audio},'audio_roles':{'speech_generator':'api'},
         'video_profiles':{'api':{'provider':'minimax','model':'MiniMax-Hailuo-2.3','enabled':True,'base_url':base+'/v1','network_mode':'loopback','api_key_env':credential_name}},'video_roles':{'video_generator':'api'}}
    path=tmp_path/'config.json';path.write_text(json.dumps(cfg));monkeypatch.setattr(config,'config_read_path',lambda:path)
    p=store.create({'brief':'两个成年人物在窗边重逢的原创故事','format':'animation'})
    from factory.video_providers import resolve_video
    flat={'image_connection':resolve(cfg,'image'),'audio_connection':resolve(cfg,'audio'),'video_connection':resolve_video(cfg),
          'image_width':384,'image_height':704,'output_width':288,'output_height':512,'max_wall_seconds':180,'max_gpu_seconds':120,'max_attempts':2,'semantic_enabled':False,'video_profile':'quality'}
    try:yield p,cfg,flat,calls,assets,tmp_path
    finally:server.shutdown();server.server_close();thread.join()


def authorize_next(pid,cfg):
    current=store.get(pid);q=current['cloud_quote'];kind=q.get('media_kind')
    from factory.video_providers import resolve_video
    profile=cm.paid_profile(resolve(cfg,kind),kind) if kind else resolve_video(cfg)
    costs.authorize(pid,{'batch_id':q['id'],'expected_revision':current['revision'],'max_submissions':q['estimated_submissions'],
                         'max_estimated_cost_micros':None,'confirm_unknown_cost':True},profile)


def invoke_with_authorization(pid,cfg,fn):
    for _ in range(20):
        try:return fn()
        except ProductionNeedsDecision as exc:
            if exc.reason!='cloud_authorization_required':raise AssertionError((exc.reason,exc.details)) from exc
            authorize_next(pid,cfg)
    raise AssertionError('production did not converge')


def test_cloud_image_authorization_download_resume_and_private_receipts(cloud,monkeypatch):
    p,cfg,flat,calls,assets,root=cloud
    from types import SimpleNamespace
    engine=SimpleNamespace(ident=p['id'],project=p,boundary=lambda:None)
    profile=cm.paid_profile(flat['image_connection'],'image')
    body=cm.image_body(profile,'Adult portrait',[],(288,512),42)
    line=cm.make_line(p['id'],'preview',{'source_signature':'source','take':'take_001','attempt':1},profile,body,'image')
    with pytest.raises(ProductionNeedsDecision):costs.ensure_batch(p,profile,[line])
    assert not calls
    authorize_next(p['id'],cfg);batch=costs.ensure_batch(p,profile,[line])
    raw,response=cm.raw_artifact(engine,profile,body,line,root/'take',batch,'image')
    count=len(calls);raw.unlink()
    raw,_=cm.raw_artifact(engine,profile,body,line,root/'take',None,'image')
    assert len(calls)==count+1 and calls[-1][1] is None
    assert not any(k.lower()=='authorization' for k in calls[-1][2])
    assert not list((root/'take').glob('*private*'))
    assert costs.summary(p['id'])['tasks'][0]['media_kind']=='image'


def test_uncertain_paid_image_never_reposts_even_after_recovery(cloud,monkeypatch):
    p,cfg,flat,calls,assets,root=cloud
    from types import SimpleNamespace
    engine=SimpleNamespace(ident=p['id'],project=p,boundary=lambda:None)
    profile=cm.paid_profile(flat['image_connection'],'image');body=cm.image_body(profile,'Portrait',[],(288,512),1)
    line=cm.make_line(p['id'],'preview',{'source_signature':'same','take':'take_001','attempt':1},profile,body,'image')
    costs.prepare_batch(p,profile,[line]);authorize_next(p['id'],cfg);batch=costs.ensure_batch(p,profile,[line])
    attempted=[]
    def uncertain(*a):attempted.append(1);raise ProviderError('transport_failed','lost')
    from factory.providers import ProviderError
    monkeypatch.setattr(cm,'_request',uncertain)
    for _ in range(2):
        with pytest.raises(ProductionNeedsDecision,match='不确定'):cm.raw_artifact(engine,profile,body,line,root/'take',batch,'image')
    assert len(attempted)==1


def test_complete_api_movie_never_initializes_local_inference(cloud,monkeypatch):
    p,cfg,flat,calls,assets,root=cloud
    from factory.audio_editor import FFMPEG
    import subprocess
    raw_image=root/'video-source.png';Image.new('RGB',(288,512),(90,130,180)).save(raw_image)
    raw_video=root/'cloud-fixture.mp4'
    subprocess.run([FFMPEG,'-hide_banner','-loglevel','error','-loop','1','-i',str(raw_image),'-t','6','-r','24','-c:v','libx264','-pix_fmt','yuv420p',str(raw_video)],check=True)
    assets['/video']=raw_video.read_bytes()
    def forbidden(*a,**kw):raise AssertionError('local inference used in cloud mode')
    monkeypatch.setattr(Scheduler,'__init__',forbidden)
    monkeypatch.setattr(production,'Scheduler',forbidden)
    import factory.speech as speech
    monkeypatch.setattr(speech.importlib.util,'spec_from_file_location',forbidden)
    p=store.update(p['id'],{'episode':{'title':'API fixture','cast':{'female':'Adult woman','male':'Adult man'},'shots':[
        {'id':'S01','seconds':3,'prompt':'Adult woman by a window','motion_prompt':'Woman turns','reference_group':'female','dialogue':[{'id':'L01','role':'female','text':'你好。','start':.2}],
         'continuity_state':{'mode':'continuous','opening_state':'The adult woman stands by the window.',
                             'ending_state':'The same woman has turned.','allowed_changes':['The approved turn.']}},
        {'id':'S02','seconds':3,'prompt':'Adult man enters','motion_prompt':'Man enters','reference_group':'male','dialogue':[{'id':'L02','role':'male','text':'再见。','start':.2}],
         'continuity_state':{'mode':'scene_cut','opening_state':'The adult man stands outside the entrance.',
                             'ending_state':'The same man has entered.','allowed_changes':['Cut from the woman to the man at the entrance.']}}]},
        'styles':[{'id':'a','name':'A','prompt':'Warm illustration'},{'id':'b','name':'B','prompt':'Cool illustration'},{'id':'c','name':'C','prompt':'Ink illustration'}]})
    def checkpoint(patch):store.update(p['id'],copy.deepcopy(patch))
    previews=invoke_with_authorization(p['id'],cfg,lambda:production.generate_style_previews(store.get(p['id']),flat,checkpoint))
    store.update(p['id'],{'styles':previews,'selected_style':'a'})
    result=invoke_with_authorization(p['id'],cfg,lambda:production.generate_project(store.get(p['id']),flat,checkpoint))
    assert (root/result['path']).is_file() and result['technical_qa']['subtitled']['status']=='pass'
    tasks=costs.summary(p['id'])['tasks']
    assert {'image','audio','video'} <= {t['media_kind'] for t in tasks}
    assert all(t['status']=='downloaded' for t in tasks)
    before=sum(path.endswith(('/images/generations','/video_generation','/generation')) and body is not None for path,body,_ in calls)
    invoke_with_authorization(p['id'],cfg,lambda:production.generate_project(store.get(p['id']),flat,checkpoint))
    after=sum(path.endswith(('/images/generations','/video_generation','/generation')) and body is not None for path,body,_ in calls)
    assert before==after
    assert len(list((root/'production').rglob('*.mp4')))>0


def test_cloud_capabilities_do_not_probe_local_gpu_or_weights(cloud,monkeypatch):
    p,cfg,flat,_,_,_=cloud
    def forbidden(*a,**kw):raise AssertionError('local probe used')
    monkeypatch.setattr(capabilities,'_model_files',forbidden)
    monkeypatch.setattr(capabilities.subprocess,'run',forbidden)
    info=capabilities._observe(cfg)
    assert info['gpus']==[] and info['image']['provider']=='dashscope_image'
    assert production.capabilities(flat)['scheduler']['physical_cards']==[]


def test_image_references_preserve_order_and_enforce_model_limits(cloud):
    _,_,flat,calls,_,root=cloud
    first=root/'first.png';second=root/'second.png'
    Image.new('RGB',(384,512),'red').save(first);Image.new('RGB',(384,512),'blue').save(second)
    body=cm.image_body(flat['image_connection'],'Two adults',[first,second],(384,704),9)
    import base64
    assert base64.b64decode(body['image'][0].split(',')[1])==first.read_bytes()
    assert base64.b64decode(body['image'][1].split(',')[1])==second.read_bytes()
    with pytest.raises(ValueError,match='最多支持3'):
        cm.image_body({**flat['image_connection'],'model':'qwen-image-3.0'},'Four adults',[first]*4,(384,704),9)
    assert not calls


def test_cloud_speech_cache_tracks_text_and_voice_not_timeline_positions(cloud):
    from types import SimpleNamespace
    _,_,flat,_,_,_=cloud
    engine=SimpleNamespace(config=flat)
    first={'id':'L1','role':'female','text':'你好。','start':.2}
    a=cm.speech_sources(engine,[first],{})
    b=cm.speech_sources(engine,[{**first,'start':1.0,'audio':'runtime-only'}],{})
    assert a==b
    engine.config={**flat,'audio_connection':{**flat['audio_connection'],'voices':{'female':'Ethan'}}}
    assert cm.speech_sources(engine,[first],{})['L1']['key']!=a['L1']['key']


def test_changed_billing_identity_invalidates_cloud_authorization(cloud):
    p,cfg,flat,_,_,root=cloud
    profile=cm.paid_profile(flat['image_connection'],'image')
    changed={**profile,'api_key_env':'DIFFERENT_ACCOUNT_KEY'}
    with pytest.raises(ProductionNeedsDecision,match='发生变化'):costs.validate_live_profile(changed)


def test_corrupt_cloud_download_is_never_reused(cloud):
    p,cfg,flat,calls,_,root=cloud
    from types import SimpleNamespace
    engine=SimpleNamespace(ident=p['id'],project=p,boundary=lambda:None)
    profile=cm.paid_profile(flat['image_connection'],'image');body=cm.image_body(profile,'Portrait',[],(384,704),1)
    line=cm.make_line(p['id'],'preview',{'source_signature':'same','take':'take_001','attempt':1},profile,body,'image')
    costs.prepare_batch(p,profile,[line]);authorize_next(p['id'],cfg);batch=costs.ensure_batch(p,profile,[line])
    raw,_=cm.raw_artifact(engine,profile,body,line,root/'take',batch,'image');raw.write_bytes(b'corrupted')
    before=len(calls)
    with pytest.raises(ProductionNeedsDecision,match='不匹配'):cm.raw_artifact(engine,profile,body,line,root/'take',None,'image')
    assert before==len(calls)


def test_public_api_example_is_complete_but_not_claimed_verified():
    from factory import providers
    value=json.loads(Path('config/factory.api.example.json').read_text())
    validated=providers.validate_registry(value['llm_profiles'],value['llm_roles'],value)
    from factory.video_providers import video_registry
    video_registry(validated)
    assert resolve(value,'image')['provider']=='dashscope_image'
    assert resolve(value,'audio')['provider']=='dashscope_tts'
    assert not any(__import__('factory.media_profiles',fromlist=['local_kinds']).local_kinds(value).values())
    assert providers.public_inventory(value)['readiness']['generation_verified'] is False


def test_missing_key_never_reserves_a_paid_submission(cloud,monkeypatch):
    p,cfg,flat,calls,_,root=cloud
    from types import SimpleNamespace
    from factory.providers import ProviderError
    engine=SimpleNamespace(ident=p['id'],project=p,boundary=lambda:None)
    profile=cm.paid_profile(flat['image_connection'],'image');body=cm.image_body(profile,'Portrait',[],(384,704),1)
    line=cm.make_line(p['id'],'preview',{'source_signature':'same','take':'take_001','attempt':1},profile,body,'image')
    costs.prepare_batch(p,profile,[line]);authorize_next(p['id'],cfg);batch=costs.ensure_batch(p,profile,[line])
    monkeypatch.delenv('FIXTURE_CLOUD_KEY',raising=False)
    from factory import connection_secrets as vault
    if profile['api_key_env'].startswith(vault.PREFIX):vault.write_vault({})
    with pytest.raises(ProviderError):cm.raw_artifact(engine,profile,body,line,root/'take',batch,'image')
    assert costs.summary(p['id'])['tasks']==[] and not calls
