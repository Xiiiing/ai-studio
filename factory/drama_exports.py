"""Read-only delivery selection; build a cache of verified files for download."""
from __future__ import annotations

import fcntl
import zipfile
import uuid
from studio.core import ROOT, within, fingerprint, sha256
from . import dramas, store


def archive(pid,batch_id=None):
    with store.connection() as db:
        db.execute('BEGIN')
        drama=dramas.require(db,pid)
        ids=drama['episodes']
        if batch_id:
            batch=next((b for b in drama['batches'] if b['id']==batch_id),None)
            if not batch: raise ValueError('批次不属于本剧')
            ids=batch['episode_ids']
        episodes=[store._read(db,eid) for eid in ids]
    manifest={'drama_id':pid,'title':drama['title'],'batch_id':batch_id,'episodes':[]}
    files=[]
    documents=[]
    from .drama_intent import enabled
    if enabled(drama):
        manifest['series_review_sha256'] = fingerprint(drama.get('series_review'))
        manifest['evidence_scope'] = 'sampled_episode_reviews_and_measured_audio_not_human_approval'
        documents.append(('series-review.json', store.encoded(drama.get('series_review'))))
    for e in sorted(episodes,key=lambda v:v['episode_number']):
        delivery=e.get('delivery')
        item={'episode_number':e['episode_number'],'id':e['id'],'title':e['title'],'status':e['status'],
              'plan_version':e['plan_version'],'reason':e.get('error') or ('尚未完成制作' if not delivery else '待用户验收' if e['status']!='approved' else None)}
        if delivery:
            if enabled(drama):
                item['episode_review_sha256'] = fingerprint(e.get('episode_review'))
                documents.append((f"E{e['episode_number']:03d}/episode-review.json",store.encoded(e.get('episode_review'))))
            documents.append((f"E{e['episode_number']:03d}/manifest.json",store.encoded(delivery)))
            item['delivery_signature']=fingerprint(delivery)
            artifacts=delivery.get('artifacts',[])
            item['artifacts']=artifacts
            item['delivery_status']=delivery.get('status')
            for artifact in artifacts:
                path=within(ROOT,artifact['path'])
                if not path.is_relative_to((ROOT/'production'/e['slug']).resolve()): raise ValueError('交付文件归属不匹配')
                if sha256(path)!=artifact['sha256'] or path.stat().st_size!=artifact['bytes']: raise ValueError('下载文件不匹配交付回执')
                files.append((path,f"E{e['episode_number']:03d}/"+path.name,artifact))
        manifest['episodes'].append(item)
    folder=dramas.owned_folder(drama,ROOT)/'exports'
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/(fingerprint(manifest)+'.zip')
    with (folder/'archive.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not target.exists():
            temporary=folder/(uuid.uuid4().hex+'.partial.zip')
            try:
                with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_STORED) as bundle:
                    bundle.writestr('manifest.json',store.encoded(manifest))
                    for name,content in documents:
                        bundle.writestr(name,content)
                    for path,name,artifact in files:
                        bundle.write(path,name)
                        if sha256(path)!=artifact['sha256']: raise ValueError('打包期间文件发生变化')
                temporary.replace(target)
            finally: temporary.unlink(missing_ok=True)
    return target
