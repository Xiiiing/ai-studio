"""Sample adjacent unit endpoints; never claim full-film visual coverage."""
from __future__ import annotations

import json
import av
from studio.core import ROOT, fingerprint, read_json, write_json, sha256
from .config import llm_identity
from .episode_assembly import verified_artifact, boundary
from .drama_planning import obj, txt, arr
from .llm import _request, _image_url
from .workflows import ReviewBlocked


def frame(path, seconds):
    with av.open(str(path)) as container:
        stream=container.streams.video[0]
        container.seek(max(0,int(seconds/stream.time_base)),stream=stream,backward=True)
        last=None
        for value in container.decode(stream):
            last=value
            if value.time is not None and value.time>=seconds: break
        if last is None: raise ValueError('无法抽取单元交界帧')
        return last.to_image()


def review(episode,units,config,control=None):
    results=[]
    for previous,current in zip(units,units[1:]):
        boundary(control)
        first=verified_artifact(previous,'episode_clean.mp4')
        second=verified_artifact(current,'episode_clean.mp4')
        identity={'from':previous['delivery']['source_signature'],'to':current['delivery']['source_signature'],
            'from_sha256':sha256(first),'to_sha256':sha256(second),
            'model':llm_identity(config,'visual_critic'),'policy':'unit_transition_endpoints.v1'}
        signature=fingerprint(identity)
        folder=ROOT/'production'/episode['slug']/'quality/transitions'/signature
        saved=folder/'review.json'
        if saved.exists(): result=read_json(saved)
        else:
            payload={'previous':{'id':previous['global_prefix'],'scene':previous['scene_id'],'ending':(previous.get('script') or {}).get('ending')},
                'next':{'id':current['global_prefix'],'scene':current['scene_id'],'opening':current['outline']['opening_state']},
                'characters':[c for c in episode['plan']['characters'] if c['character_id'] in
                    set(previous['outline']['characters']) & set(current['outline']['characters'])],
                'visual_rules':episode['plan']['visual_rules'],
                'scope':'previous final frame and next first frame, not full video'}
            content=[{'type':'text','text':json.dumps(payload,ensure_ascii=False)},
                {'type':'image_url','image_url':{'url':_image_url(frame(first,max(0,previous['delivery']['duration_seconds']-1/24)))}},
                {'type':'image_url','image_url':{'url':_image_url(frame(second,0))}}]
            result=_request(config,'visual_critic',
                '审核相邻制作单元的两个交界帧。检查共同人物身份、指定画风、必要道具与承接状态的明确矛盾。'
                '场景切换允许构图、光照和位置变化；只将清晰可见且破坏剧情的矛盾判fail。'
                '不得由两帧推断全过程动作或口型同步。issues逐条指出两帧证据；通过时issues为空。',
                payload,obj({'status':{'type':'string','enum':['pass','fail']},'summary':txt(1,2000),'issues':arr(txt(1,1500),0,12)}),
                folder,max_tokens=2400,temperature=.2,content=content)
            result={**result,'identity':identity,'from_unit':previous['id'],'to_unit':current['id']}
            write_json(saved,result)
            boundary(control)
        results.append(result)
        if result['status']!='pass' or result['issues']:
            raise ReviewBlocked('跨单元连续性审核未通过，原素材已保留，请局部修改指定单元',
                {'type':'episode_continuity','unit_id':current['id'],'review':result})
    return {'scope':'sampled_adjacent_unit_endpoints','status':'pass' if results else 'not_applicable','boundaries':results}
