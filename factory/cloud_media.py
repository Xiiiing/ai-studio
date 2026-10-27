"""Synchronous paid image/TTS calls with durable intents and download recovery."""
from __future__ import annotations
from .connection_secrets import secret_value
import base64
import copy
import io
import json
import os
from pathlib import Path
import time
import wave

from studio.core import fingerprint, media_receipt, read_json, sha256, write_json
from . import cloud_budget, store
from .config import DATA
from .media_profiles import identity
from .providers import ProviderError, validate_endpoint
from .scheduler import ProductionNeedsDecision
from .video_transport import _transfer, download_video

PROVIDERS = {'dashscope_image', 'dashscope_tts'}


def paid_profile(profile, kind):
    return {**profile, '_media_kind': kind}


def image_body(profile, prompt, refs, size, seed):
    from PIL import Image
    maximum = 3 if profile['model'].startswith('qwen-image-3.') else 10
    if len(refs) > maximum: raise ValueError(f'图像模型最多支持{maximum}张参考图；请调整模型或镜头参考')
    images = []
    for path in refs:
        path = Path(path)
        if path.stat().st_size > 10*1024*1024: raise ValueError('百炼参考图须小于10 MiB')
        with Image.open(path) as im:
            if not 1 <= min(im.size) or max(im.size) > 8000 or max(im.size)/min(im.size) > 10:
                raise ValueError('参考图尺寸超出模型限制')
            im.verify(); mime=Image.MIME.get(im.format)
        if not mime: raise ValueError('参考图格式无效')
        images.append('data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode())
    width,height=size
    # Ask for a provider-supported canvas, then normalize without cropping people.
    factor=max(1, (512*512/(width*height))**.5)
    requested=(round(width*factor),round(height*factor))
    body={'model':profile['model'], 'prompt':prompt, 'n':1, 'size':f'{requested[0]}x{requested[1]}',
          'seed':seed % (2**31), 'prompt_extend':False, 'watermark':False}
    if images: body['image']=images
    return body


def make_line(pid, item, record, profile, body, kind):
    source=record['source_signature']
    signature=fingerprint({'connection':identity(profile),'body':body})
    if record.get('team_revision'):
        signature=fingerprint({'request':signature,'explicit_retake':record['team_revision']})
    return {'intent_id':fingerprint({'project':pid,'kind':kind,'item':item,'take':record['take']}),
            'shot_id':kind+':'+item, 'attempt':record['attempt'], 'take':record['take'],
            'provider':profile['provider'], 'model':profile['model'],
            'request_signature':signature, 'source_signature':source}


def prepare_images(engine, targets):
    profile=paid_profile(engine.config['image_connection'],'image')
    prepared={}; fresh=[]
    for target in targets:
        engine._assert_model_capabilities(target)
        record=target.pop('resume_attempt',None) or engine._new_attempt(target)
        engine._assert_model_capabilities(target, prompt=record['prompt'])
        body=image_body(profile,record['prompt'],target['references'],engine.image_size,record['seed'])
        line=make_line(engine.ident,target['key'],record,profile,body,'image')
        prepared[target['key']]=(record,body,line)
        with store.connection() as db:
            old=db.execute('SELECT intent_id FROM cloud_tasks WHERE intent_id=?',(line['intent_id'],)).fetchone()
        if not old: fresh.append(line)
    batch=cloud_budget.ensure_batch(engine.project,profile,fresh) if fresh else None
    return profile,prepared,batch


def _request(profile, body):
    secret=secret_value(profile['api_key_env'])
    if not secret or any(c in secret for c in '\r\n'): raise ProviderError('missing_auth','制作 API Key 缺失或无效')
    route='/images/generations' if profile['provider']=='dashscope_image' else '/services/aigc/multimodal-generation/generation'
    encoded=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
    if len(encoded)>150*1024*1024: raise ValueError('制作请求超出大小限制')
    return _transfer(validate_endpoint(profile)+route,profile,{'Authorization':'Bearer '+secret,'Content-Type':'application/json'},
                     payload=encoded,timeout=profile.get('request_timeout_seconds',120),max_bytes=8*1024*1024)


def _observe(pid, line, state, response=None):
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE cloud_tasks SET status=?,phase=?,updated=? WHERE intent_id=? AND project_id=?',(state,'quality_review' if state=='downloaded' else 'remote_download' if state=='generated' else 'remote_submit',time.time(),line['intent_id'],pid))
        if response is not None:
            db.execute('INSERT OR REPLACE INTO cloud_media_receipts(intent_id,media_kind,request_id,usage_json) VALUES(?,?,?,?)',
                       (line['intent_id'],line['shot_id'].split(':',1)[0],str(response.get('request_id') or response.get('id') or '')[:128],store.encoded(response.get('usage',{}))))


def raw_artifact(engine, profile, body, line, folder, batch, kind):
    """A response saved before download can be reused; an uncertain POST cannot."""
    import fcntl
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    with (folder/'cloud.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        journal=DATA/'cloud_media_responses'/(line['intent_id']+'.json')
        original=folder/('original.png' if kind=='image' else 'original.audio')
        with store.connection() as db:
            row=db.execute('SELECT * FROM cloud_tasks WHERE intent_id=?',(line['intent_id'],)).fetchone()
        if row:
            if row['request_signature']!=line['request_signature']:
                raise ProductionNeedsDecision('已提交请求与当前参数不匹配','cloud_request_changed')
            if row['status'] in {'failed','rejected'}:
                raise ProviderError('generation_failed','云端已拒绝或终止该次生成')
            if not journal.exists():
                raise ProductionNeedsDecision('云端提交结果不确定；请核对供应商记录，禁止自动重投','cloud_submission_unknown',{'intent_id':line['intent_id']})
            saved=read_json(journal)
            if saved['request_signature']!=line['request_signature']: raise ValueError('云回执与当前请求不匹配')
            response=saved['response']
        else:
            secret=secret_value(profile['api_key_env'])
            if not secret or any(c in secret for c in '\r\n'):
                raise ProviderError('missing_auth','制作 API Key 缺失或无效')
            engine.boundary()
            cloud_budget.validate_live_profile(profile)
            cloud_budget.reserve(engine.ident,batch['id'],line,profile,
                                 {'request_fingerprint':line['request_signature'],'source_signature':line['source_signature']})
            try:
                response=_request(profile,body)
            except ProviderError as exc:
                if exc.code.startswith('http_4'):
                    _observe(engine.ident,line,'rejected')
                    raise
                _observe(engine.ident,line,'unresolved')
                raise ProductionNeedsDecision('云端生成响应不确定，提交记录已保留','cloud_submission_unknown',{'intent_id':line['intent_id']}) from None
            except Exception:
                _observe(engine.ident,line,'unresolved')
                raise ProductionNeedsDecision('云端生成响应不确定，提交记录已保留','cloud_submission_unknown',{'intent_id':line['intent_id']}) from None
            # Private signed download URLs must not enter public receipts or UI exports.
            write_json(journal,{'request_signature':line['request_signature'],'response':response});journal.chmod(0o600)
            _observe(engine.ident,line,'generated',response)
        if response.get('model') and response['model'] != profile['model']:
            raise ProductionNeedsDecision('云端响应模型与配置不匹配','cloud_model_mismatch')
        if response.get('code') or response.get('error'):
            _observe(engine.ident,line,'failed',response)
            raise ProviderError('generation_failed','供应商返回生成失败；详情保留在私有回执中')
        if kind=='image':
            data=response.get('data',[])
            if len(data)!=1 or not isinstance(data[0],dict) or not isinstance(data[0].get('url'),str):
                raise ProductionNeedsDecision('图像响应未包含唯一的下载地址','cloud_invalid_response')
            url=data[0]['url']
        else:
            url=response.get('output',{}).get('audio',{}).get('url')
            if not isinstance(url,str): raise ProductionNeedsDecision('语音响应缺少下载地址','cloud_invalid_response')
        raw_receipt=folder/'original.receipt.json'
        if original.exists() and raw_receipt.exists() and read_json(raw_receipt).get('sha256') != sha256(original):
            raise ProductionNeedsDecision('原始云素材与回执不匹配','cache_integrity')
        if not original.exists() or not raw_receipt.exists():
            engine.boundary()
            download_video({**profile,'max_download_bytes':profile.get('max_download_bytes',256*1024*1024)},url,original)
            original.chmod(0o600)
            write_json(raw_receipt,media_receipt(original))
        _observe(engine.ident,line,'downloaded',response)
        return original,response


def render_image(engine,target,record,prepared):
    profile,records,batch=prepared
    _,body,line=records[target['key']]
    folder=target['folder']/record['take']
    raw,response=raw_artifact(engine,profile,body,line,folder,batch,'image')
    path=folder/'image.png'
    if not (folder/'result.json').exists():
        from PIL import Image, ImageOps
        with Image.open(raw) as image:
            normalized=ImageOps.pad(image.convert('RGB'),engine.image_size,method=Image.Resampling.LANCZOS,color='black')
            normalized.save(path)
    receipt=media_receipt(path,provider=profile['provider'],model=profile['model'],connection=identity(profile),
                          original=media_receipt(raw),cloud_intent_id=line['intent_id'],usage=response.get('usage'),origin='cloud_image')
    write_json(folder/'result.json',receipt)
    return receipt


def speech_sources(engine,lines,voices):
    profile=engine.config['audio_connection'];expected={}
    for original in lines:
        line={'id':original['id'],'role':original['role'],'text':str(original['text']).strip()}
        voice=profile.get('voices',{}).get(line['role'],profile.get('default_voice','Cherry'))
        if isinstance(voice,dict): voice=voice.get('speaker',voice.get('voice'))
        if not isinstance(voice,str) or not voice: raise ValueError('请配置有效的云端角色声线')
        source={'line':line,'settings':{'connection':identity(profile),'voice':voice,'language':profile.get('language','Chinese')}}
        if original.get('team_revision'):
            source['team_revision']=original['team_revision']
        signature=fingerprint(source)
        expected[line['id']]={'source':source,'source_sha256':signature,'key':fingerprint({'source':signature,'schema':'cloud_tts.v1'}),
                              'model_state':{'provider':profile['provider'],'model':profile['model']}}
    return expected


def _wav_quality(path):
    import numpy as np
    with wave.open(str(path),'rb') as wav:
        if wav.getframerate()!=24000 or wav.getnchannels()!=1 or wav.getsampwidth()!=2: raise ValueError('语音标准化格式不正确')
        frames=wav.getnframes();samples=np.frombuffer(wav.readframes(frames),dtype='<i2').astype(float)/32768
    if frames<2400 or len(samples)!=frames: raise ValueError('语音过短或不完整')
    peak=float(np.max(np.abs(samples)));rms=float(np.sqrt(np.mean(samples*samples)))
    if peak<.001 or rms<.0001 or float(np.mean(np.abs(samples)>=.9999))>.001: raise ValueError('语音静音或削波')
    return {'sample_rate':24000,'channels':1,'frames':frames,'duration_seconds':round(frames/24000,6),'wav_sha256':sha256(path)}


def speech_batch(engine,lines,voices,expected):
    profile=paid_profile(engine.config['audio_connection'],'audio')
    prepared=[];fresh=[];records={}
    for line in lines:
        e=expected[line['id']];source=e['source']
        folder=engine.folder/'audio/cloud'/e['key'];path=folder/(line['id']+'.wav')
        record=engine._speech_receipt(path,e)
        if record:
            records[line['id']]=engine._speech_index(e,record);continue
        body={'model':profile['model'],'input':{'text':source['line']['text'],'voice':source['settings']['voice'],'language_type':source['settings']['language']}}
        # A changed source gets a new take identity, but cannot bypass a prior
        # unresolved request for this logical line in cloud_budget.reserve.
        take='tts_'+e['key'][:24]
        info=make_line(engine.ident,line['id'],{'source_signature':e['key'],'take':take,'attempt':1,'team_revision':source.get('team_revision')},profile,body,'audio')
        with store.connection() as db:
            old=db.execute('SELECT * FROM cloud_tasks WHERE project_id=? AND shot_id=? AND request_signature=? ORDER BY created DESC LIMIT 1',
                           (engine.ident,info['shot_id'],info['request_signature'])).fetchone()
        if old:
            number=old['attempt']+(1 if old['status'] in {'failed','rejected'} else 0)
            if number>engine.config.get('max_attempts',3):
                raise ProductionNeedsDecision('云端配音已达到本次输入的尝试上限','voice_failed')
            take=old['take'] if number==old['attempt'] else 'tts_'+e['key'][:24]+f'_{number:03d}'
            info=make_line(engine.ident,line['id'],{'source_signature':e['key'],'take':take,'attempt':number,'team_revision':source.get('team_revision')},profile,body,'audio')
        prepared.append((line,e,folder,path,body,info))
        if not old or old['status'] in {'failed','rejected'}:fresh.append(info)
    batch=cloud_budget.ensure_batch(engine.project,profile,fresh) if fresh else None
    for line,e,folder,path,body,info in prepared:
        raw,response=raw_artifact(engine,profile,body,info,folder,batch,'audio')
        if not path.with_suffix('.json').exists():
            temporary=path.with_suffix('.part.wav')
            engine.ffmpeg(['-y','-i',raw,'-ar',24000,'-ac',1,'-c:a','pcm_s16le',temporary],limit=300)
            os.replace(temporary,path)
        quality=_wav_quality(path)
        write_json(path.with_suffix('.json'),{'id':line['id'],'role':line['role'],'text':e['source']['line']['text'],
                   'source':e['source'],'source_sha256':e['source_sha256'],'model_state':e['model_state'],
                   'quality':quality,'provider':profile['provider'],'model':profile['model'],'cloud_intent_id':info['intent_id'],
                   'usage':response.get('usage'),'original':media_receipt(raw)})
        record=engine._speech_receipt(path,e)
        if not record:raise ValueError('云端语音未通过来源校验')
        records[line['id']]=engine._speech_index(e,record)
    return records
