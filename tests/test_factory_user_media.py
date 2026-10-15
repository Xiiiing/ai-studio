import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest
from factory import workflow_templates as wt, media_profiles, external_comfy
from factory.scheduler import ProductionNeedsDecision
from studio import core


def template():
    return {'name':'custom','kind':'image','graph':{'1':{'class_type':'Prompt','inputs':{'text':''}},'2':{'class_type':'SaveImage','inputs':{'images':['1',0]}}},
            'mappings':[{'node':'1','input':'text','source':'prompt'}], 'output':{'node':'2','field':'images','index':1}}


def test_template_hash_roundtrip_mappings_and_compatibility(tmp_path, monkeypatch):
    monkeypatch.setattr(wt,'DATA',tmp_path)
    t=template(); saved=wt.import_template(t)
    assert wt.load_template(saved['id'])==t and wt.import_template(t)['id']==saved['id']
    assert wt.bind(t,{'prompt':'actual'},[])['1']['inputs']['text']=='actual'
    assert t['graph']['1']['inputs']['text']==''
    with pytest.raises(ValueError,match='参考图'): wt.bind(t,{'prompt':'actual'},['image.png'])
    with pytest.raises(ValueError,match='缺少节点'): wt.check_nodes(t,{'Prompt':{}})
    bad=copy.deepcopy(t); bad['mappings'][0]['input']='missing'
    with pytest.raises(ValueError): wt.validate(bad)
    bad=copy.deepcopy(t); bad['graph']['1']['inputs']['cycle']=['2',0]
    with pytest.raises(ValueError,match='循环'): wt.validate(bad)
    bad=copy.deepcopy(t); bad['graph']['1']['inputs']['api_key']='secret'
    with pytest.raises(ValueError): wt.validate(bad)
    path=tmp_path/'workflow_templates'/(saved['id']+'.json');path.write_text('{}')
    with pytest.raises(ValueError,match='校验'): wt.load_template(saved['id'])


@pytest.fixture
def comfy(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'ROOT',tmp_path)
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/object_info': answer={'Prompt':{},'SaveImage':{}}
            elif self.path.startswith('/history/'):
                answer={'prompt-1':{'status':{'status_str':'success'},'outputs':{'2':{'images':[{'filename':'other.png'},{'filename':'selected.png'}]}}}}
            elif self.path.startswith('/view?'):
                calls.append(('download',self.path)); self.send_response(200);self.end_headers();self.wfile.write(b'fixture-image');return
            else: self.send_response(404);self.end_headers();return
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(answer).encode())
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']));calls.append((self.path,body))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'name':'uploaded.png'} if self.path=='/upload/image' else {'prompt_id':'prompt-1'}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=external_comfy.ExternalComfy({'base_url':f'http://127.0.0.1:{server.server_port}', 'network_mode':'loopback','enabled':True})
    try:yield client,calls
    finally:server.shutdown();server.server_close();thread.join()


def test_external_comfy_upload_selected_output_and_resume(comfy,tmp_path):
    client,calls=comfy
    source=tmp_path/'source.png';source.write_bytes(b'input')
    assert client.upload(source)=='uploaded.png'
    t=template();path=client.execute(t['graph'],tmp_path/'take','image',t['output'])
    assert path.read_bytes()==b'fixture-image'
    assert 'selected.png' in next(v for k,v in calls if k=='download')
    assert client.execute(t['graph'],tmp_path/'take','image',t['output'])==path
    assert sum(k=='/prompt' for k,v in calls)==1
    # Losing a result permits querying the accepted prompt, never another POST.
    (tmp_path/'take/result.json').unlink()
    client.execute(t['graph'],tmp_path/'take','image',t['output'])
    assert sum(k=='/prompt' for k,v in calls)==1


def test_uncertain_comfy_submission_never_reposts(comfy,tmp_path,monkeypatch):
    client,calls=comfy
    original=client.request
    def uncertain(route,*args,**kwargs):
        if route=='/prompt': raise TimeoutError('response lost')
        return original(route,*args,**kwargs)
    monkeypatch.setattr(client,'request',uncertain)
    with pytest.raises(TimeoutError):client.execute(template()['graph'],tmp_path/'take','image')
    monkeypatch.setattr(client,'request',original)
    with pytest.raises(ProductionNeedsDecision,match='不确定'):client.execute(template()['graph'],tmp_path/'take','image')
    assert not any(k=='/prompt' for k,v in calls)


def test_legacy_and_explicit_media_connections_do_not_change_each_other():
    config={'production':{'gpu_service':{'base_url':'https://owned.example/inference'}}}
    assert media_profiles.resolve(config,'image')['provider']=='legacy'
    update={'image_profiles':{'mine':{'provider':'comfyui_external','model':'custom','base_url':'http://127.0.0.1:8188'}},'image_roles':{'image_generator':'mine'}}
    saved=media_profiles.validate_registry(config,update)
    assert saved['production']==config['production']
    assert media_profiles.resolve(saved,'image')['provider']=='comfyui_external'
    assert media_profiles.resolve(saved,'audio')['provider']=='legacy'
    with pytest.raises(ValueError):media_profiles.normalize_media({'provider':'qwen_tts_local','model':'tts','model_dir':'../private'},'audio')


def test_positive_comfy_rejection_is_terminal_not_an_unknown_post(comfy,tmp_path,monkeypatch):
    client,calls=comfy
    original=client.request
    def reject(route,*args,**kwargs):
        if route=='/prompt':return {'error':{'type':'prompt_outputs_failed_validation'}}
        return original(route,*args,**kwargs)
    monkeypatch.setattr(client,'request',reject)
    with pytest.raises(ProductionNeedsDecision) as exc:client.execute(template()['graph'],tmp_path/'take','image')
    assert exc.value.reason=='comfy_task_failed'
    with pytest.raises(ProductionNeedsDecision) as exc:client.execute(template()['graph'],tmp_path/'take','image')
    assert exc.value.reason=='comfy_task_failed'


def test_node_model_enum_is_checked_before_submission():
    t=template();t['graph']['1']['inputs']['model']='not-installed'
    with pytest.raises(ValueError,match='不兼容'):
        wt.check_nodes(t,{'Prompt':{'input':{'required':{'text':['STRING',{}],'model':[['installed']]}}},'SaveImage':{}})
