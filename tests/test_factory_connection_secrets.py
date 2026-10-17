"""Write-only connection keys and draft discovery; isolated local HTTP only."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from factory import connection_secrets as vault, providers, config

KEY = 'fixture-private-token'

@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, 'vault_path', lambda: tmp_path/'model-keys.json')
    profile={'provider':'openai_compatible','base_url':'http://127.0.0.1:8090/v1','model':'fixture','vision':True,'context_tokens':32768}
    cfg={'schema_version':1,'llm_profiles':{'fixture':profile},'llm_roles':{r:'fixture' for r in config.ROLE_LABELS}}
    path=tmp_path/'config.json';path.write_text(json.dumps(cfg))
    def save(changes=None, **kwargs):
        current=json.loads(path.read_text())
        return providers.save_registry(current['llm_profiles'],current['llm_roles'],providers.config_revision(current),config_path=path,secrets=changes,**kwargs)
    return path,save


def test_key_roundtrip_rotation_worker_restart_and_no_public_secret(registry):
    path,save=registry
    public=save({'llm:fixture':KEY})
    saved=json.loads(path.read_text());reference=saved['llm_profiles']['fixture']['api_key_env']
    assert KEY not in path.read_text() and KEY not in json.dumps(public)
    assert public['profiles'][0]['auth_configured'] is True
    assert vault.secret_value(reference)==KEY
    assert vault.vault_path().stat().st_mode & 0o777 == 0o600
    # A fresh process resolves the same opaque reference without an exported key.
    code='from factory import connection_secrets as v;from pathlib import Path;import sys;v.vault_path=lambda:Path(sys.argv[1]);assert v.secret_value(sys.argv[2])==sys.argv[3]'
    subprocess.run([sys.executable,'-c',code,str(vault.vault_path()),reference,KEY],check=True)
    save({'llm:fixture':'fixture-rotated-token'})
    latest=json.loads(path.read_text())['llm_profiles']['fixture']['api_key_env']
    assert latest!=reference and vault.secret_value(reference)==KEY
    assert vault.secret_value(latest)=='fixture-rotated-token'
    save({'llm:fixture':None})
    assert 'api_key_env' not in json.loads(path.read_text())['llm_profiles']['fixture']
    assert vault.secret_value(reference)==KEY # running snapshot still works


@pytest.mark.parametrize('changes',[{'llm:missing':KEY},{'llm:fixture':'bad\nvalue'},{'llm:fixture':123},['bad']])
def test_invalid_updates_never_write_keys_or_config(registry,changes):
    path,save=registry;before=path.read_bytes()
    with pytest.raises(ValueError):save(changes)
    assert path.read_bytes()==before and not vault.vault_path().exists()


def test_stale_or_invalid_registry_does_not_rotate_key(registry):
    path,save=registry;save({'llm:fixture':KEY})
    before=path.read_bytes();keys=vault.vault_path().read_bytes()
    current=json.loads(before);broken=copy.deepcopy(current['llm_profiles']);broken['fixture']['unknown']=True
    for profiles,revision,error in [(broken,providers.config_revision(current),ValueError),(current['llm_profiles'],'0'*64,providers.ConfigConflict)]:
        with pytest.raises(error):providers.save_registry(profiles,current['llm_roles'],revision,config_path=path,secrets={'llm:fixture':'replacement'})
    assert path.read_bytes()==before and vault.vault_path().read_bytes()==keys


def test_failed_config_replace_rolls_back_vault(registry,monkeypatch):
    path,save=registry;save({'llm:fixture':KEY})
    before=path.read_bytes();keys=vault.vault_path().read_bytes();replace=os.replace
    def fail(source,destination):
        if Path(destination)==path:raise OSError('fixture replace failure')
        return replace(source,destination)
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError):save({'llm:fixture':'replacement'})
    assert path.read_bytes()==before and vault.vault_path().read_bytes()==keys


def test_vault_refuses_symlinks_and_public_permissions(registry,tmp_path):
    path,save=registry
    vault.vault_path().write_text('{}');vault.vault_path().chmod(0o644)
    with pytest.raises(ValueError,match='owner-only'):save({'llm:fixture':KEY})
    vault.vault_path().unlink();outside=tmp_path/'outside';outside.write_text('{}');outside.chmod(0o600)
    vault.vault_path().symlink_to(outside)
    with pytest.raises(OSError):save({'llm:fixture':KEY})
    assert outside.read_text()=='{}'


@pytest.mark.parametrize('provider,route,header,payload',[
 ('openai_compatible','/v1/models','Authorization',{'data':[{'id':'local-text','description':KEY}]}),
 ('anthropic','/v1/models','x-api-key',{'data':[{'id':'claude-fixture','description':KEY}]}),
 ('gemini','/v1beta/models','x-goog-api-key',{'models':[{'name':'models/gemini-fixture'}]}),
 ('ollama_native','/api/tags','Authorization',{'models':[{'name':'qwen-local:27b'}]}),
])
def test_draft_discovery_real_http_native_auth_no_persist(registry,provider,route,header,payload):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            calls.append((self.path,self.headers.get(header)))
            data=json.dumps(payload).encode();self.send_response(200);self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'+ ('/v1beta' if provider=='gemini' else '' if provider=='ollama_native' else '/v1')
    path,_=registry;before=path.read_bytes()
    try:
        result=providers.discover_models({'provider':provider,'base_url':base,'network_mode':'loopback','model':'fixture','enabled':True},KEY)
        assert result['generation'] is False and len(result['models'])==1
        assert calls==[(route,('Bearer ' if header=='Authorization' else '')+KEY)]
        assert KEY not in json.dumps(result)
        assert path.read_bytes()==before and not vault.vault_path().exists()
        assert vault._temporary.get()=={}
    finally:server.shutdown();server.server_close();thread.join(2)


def test_media_keys_require_matching_registry_and_support_all_consumers(registry):
    path,save=registry
    current=json.loads(path.read_text());current['image_profiles']={'image':{'provider':'dashscope_image','model':'qwen-image-2.1-pro','base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1','network_mode':'remote','allowed_hosts':['dashscope.aliyuncs.com'],'enabled':True}}
    current['image_roles']={'image_generator':'image'};path.write_text(json.dumps(current))
    with pytest.raises(ValueError,match='对应制作连接'):save({'image:image':KEY})
    public=save({'image:image':KEY},image_profiles=current['image_profiles'],image_roles=current['image_roles'])
    assert KEY not in json.dumps(public)
    ref=json.loads(path.read_text())['image_profiles']['image']['api_key_env']
    assert vault.secret_value(ref)==KEY
