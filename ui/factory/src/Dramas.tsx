import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { PageHeader } from './PageHeader';
import { Panel, Field, Modal, ErrorNotice, Loading } from './ui';
import { request, rows, mediaUrl, names, type RecordData } from './api';
import './dramas.css';

export const dramaDefaults = { seconds: 60, maxSeconds: 90 };
const statusNames: Record<string, string> = {draft: '未生成', ready: '规划已确认', awaiting_plan: '待确认全剧规划',
  waiting_dependency: '等待前集结果', unit_ready: '单元已完成', ...names};

export function CreateDrama({ onClose, onCreated, onLegacy }: {
  onClose: () => void; onCreated: (p: RecordData) => void; onLegacy: () => void;
}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [source, setSource] = useState(''),
    [sourceName, setSourceName] = useState(''), [mode, setMode] = useState('brief');
  const requestId = useRef(crypto.randomUUID());
  async function importText(file?: File) {
    if (!file) return;
    try {
      if (file.size > 240000 || !/\.(txt|md|markdown)$/i.test(file.name)) throw new Error('请选择不超过240KB的TXT或Markdown文件。');
      const decoded = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
      if (decoded.length > 60000) throw new Error('剧本最多60000字，请按部拆分。');
      setSource(decoded); setSourceName(file.name); setError('');
    } catch (e) { setError((e as Error).message); }
  }
  return <Modal id="create-drama" title="新建一部剧" onClose={onClose}>
    <form className="drama-form" onSubmit={async event => {
      event.preventDefault(); if (busy) return; setBusy(true); setError('');
      const data = new FormData(event.currentTarget);
      try {
        const p = await request('/dramas', {title: data.get('title'), brief: data.get('brief'),
          genre: data.get('genre'), visual_style: data.get('style'), episode_count: Number(data.get('count')),
          duration_seconds: Number(data.get('seconds')), format: data.get('format'),
          source_text: mode === 'script' ? source : '', source_name: mode === 'script' ? sourceName : '', request_id: requestId.current});
        onCreated(p);
      } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
    }}>
      <Field label="剧名"><input name="title" required maxLength={100} /></Field>
      <Field label="创作起点"><select value={mode} onChange={e => setMode(e.target.value)}>
        <option value="brief">从梗概创作</option><option value="script">已有剧本</option></select></Field>
      <Field label="创作方向"><textarea name="brief" required={mode === 'brief'} minLength={5} maxLength={6000} rows={4} placeholder="故事梗概、人物关系和希望表达的主题" /></Field>
      {mode === 'script' && <>
        <Field label="导入剧本"><input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" onChange={e => void importText(e.target.files?.[0])} /></Field>
        <Field label="剧本正文"><textarea value={source} required minLength={5} maxLength={60000} rows={8} onChange={e => setSource(e.target.value)} /></Field>
      </>}
      <div className="drama-fields">
        <Field label="总集数"><input name="count" type="number" required min={1} max={100} defaultValue={6} /></Field>
        <Field label="每集时长（秒）"><input name="seconds" type="number" required min={15} max={dramaDefaults.maxSeconds} defaultValue={dramaDefaults.seconds} /></Field>
        <Field label="题材"><input name="genre" maxLength={100} defaultValue="剧情" /></Field>
        <Field label="制作路线"><select name="format"><option value="animation">H3 视频剧</option><option value="motion_comic">动态漫</option><option value="live_action">H3 真人风格</option></select></Field>
      </div>
      <Field label="全剧画风"><textarea name="style" maxLength={2000} defaultValue="统一人物造型、色彩与场景画风" rows={2} /></Field>
      <p className="muted">先生成并确认全剧设定与分集大纲，再选择集数自动制作，成片由你验收。</p>
      <ErrorNotice error={error} />
      <div className="drama-actions"><button type="submit" className="button primary" disabled={busy}>{busy ? '正在建立…' : '建立整剧项目'}</button>
        <button type="button" className="button" disabled={busy} onClick={onLegacy}>使用原单集入口</button></div>
    </form>
  </Modal>;
}

function PlanEditor({ document, busy, confirm }: {document: RecordData; busy: boolean; confirm: (value: RecordData) => void}) {
  const [draft, setDraft] = useState<RecordData>(() => structuredClone(document));
  function update(key: string, value: unknown) { setDraft({...draft, [key]: value}); }
  return <form className="drama-form" onSubmit={e => {e.preventDefault(); confirm(draft);}}>
    <Field label="全剧主线"><textarea rows={5} value={draft.storyline} onChange={e => update('storyline', e.target.value)} /></Field>
    <Field label="世界与背景"><textarea rows={4} value={draft.world} onChange={e => update('world', e.target.value)} /></Field>
    <Field label="画风规则"><textarea rows={3} value={draft.visual_rules} onChange={e => update('visual_rules', e.target.value)} /></Field>
    <div className="drama-cards">{rows(draft.characters).map((c, i) => <div className="drama-card" key={c.character_id}>
      <strong>{c.name}</strong><p>{c.identity_constraints}</p><p>{c.default_wardrobe}</p>
      <Field label={`${c.name}声线`}><select value={c.voice_id} onChange={e => update('characters', draft.characters.map((v: RecordData, index: number) => index === i ? {...v, voice_id: e.target.value} : v))}>
        <option value="female">女声</option><option value="male">男声</option><option value="narrator">旁白</option></select></Field>
    </div>)}</div>
    {rows(draft.episodes).map((e, i) => <Field key={e.number} label={`第${e.number}集 · ${e.title}`}>
      <textarea rows={4} value={e.synopsis} onChange={event => update('episodes', draft.episodes.map((v: RecordData, index: number) => index === i ? {...v, synopsis: event.target.value} : v))} /></Field>)}
    {!!draft.uncertainties?.length && <div><strong>待确认信息</strong><ul>{draft.uncertainties.map((v: string, i: number) => <li key={i}>{v}</li>)}</ul></div>}
    <button className="button primary" disabled={busy} type="submit">确认全剧规划</button>
    <small>修改规划后会重新独立审核，审核通过后再确认。</small>
  </form>;
}

export function DramaDetail({id, onBack, openEpisode}: {id: string; onBack: () => void; openEpisode: (id: string) => void}) {
  const client = useQueryClient();
  const query = useQuery({queryKey: ['drama', id], queryFn: () => request('/dramas/'+id), refetchInterval: 5000});
  const [selected, setSelected] = useState<number[]>([]), [tab, setTab] = useState('episodes'),
    [busy, setBusy] = useState(false), [error, setError] = useState(''), [feedback, setFeedback] = useState(''), [reviewing, setReviewing] = useState<RecordData|null>(null);
  const pending = useRef(false);
  const requestCache = useRef<Record<string,string>>({});
  const p = query.data;
  async function operation(path: string, body: RecordData) {
    if (!p || pending.current) return;
    pending.current = true; setBusy(true); setError('');
    const signature = JSON.stringify({path, body, revision: p.revision});
    const requestId = requestCache.current[signature] ||= crypto.randomUUID();
    try {
      await request('/dramas/'+id+'/'+path, {...body, expected_revision: p.revision, request_id: requestId});
      setReviewing(null); setSelected([]); await client.invalidateQueries();
    } catch (e) {setError((e as Error).message);} finally {pending.current = false; setBusy(false);}
  }
  if (!p) return <><ErrorNotice error={query.error} /><Loading /></>;
  return <>
    <PageHeader title={p.title} parent={<button className="icon-button" onClick={onBack} aria-label="返回项目列表"><ArrowLeft size={20}/></button>}
      meta={<span>{p.episode_count}集 · 每集{p.duration_seconds}秒 · {p.format === 'motion_comic' ? '动态漫' : 'H3视频剧'} · {statusNames[p.status] || p.status}</span>} />
    <ErrorNotice error={error || query.error} />
    <nav className="drama-tabs" aria-label="整剧工作区">{[['episodes','剧集'],['plan','全剧规划'],['assets','共享设定'],['batches','生成批次'],['results','成果']].map(([key,label]) =>
      <button key={key} className={'button '+(tab===key?'primary':'')} aria-pressed={tab===key} onClick={() => setTab(key)}>{label}</button>)}</nav>
    {p.error && <Panel title="需要处理"><p>{p.error}</p>{p.plan_review?.issues?.map((v: string, i: number) => <p key={i}>{v}</p>)}</Panel>}
    {(!p.plan_version || tab === 'plan') && <Panel title="全剧规划">
      {p.draft_plan ? <PlanEditor key={String(p.plan_attempt)+p.status+p.plan_version} document={p.draft_plan} busy={busy} confirm={value => void operation('confirm', {plan:value})} /> :
      <p>策划与编剧正在准备全剧设定和分集大纲，独立审核后将在这里显示。</p>}
      {p.plan_review && <p>{p.plan_review.passed ? '全剧规划已通过独立审核' : '规划需要修改后重新审核'}</p>}
      {!!p.affected_episodes?.length && <p>新设定影响第{p.affected_episodes.map((e: RecordData) => e.number).join('、')}集；已有剧集继续保留原引用版本。</p>}
    </Panel>}
    {tab === 'episodes' && !!p.plan_version && <>
      <Panel title="选择本次制作的剧集"><div className="drama-actions">
        <button className="button" onClick={() => setSelected(rows(p.episodes).filter(e => e.status === 'draft').map(e => e.episode_number))}>选择全部未生成剧集</button>
        <button className="button primary" disabled={busy || !selected.length} onClick={() => void operation('generate', {episode_numbers: selected})}>生成所选 {selected.length} 集</button>
        <button className="button" disabled={busy || p.status==='cancelled'} onClick={() => void operation('actions', {action:p.pause_requested?'resume':'pause'})}>{p.pause_requested?'继续整剧制作':'暂停整剧制作'}</button>
      </div><p className="muted">按集号排队，失败集保留进度，其他无依赖剧集继续。每个制作单元默认上限120卡分钟、180分钟，最多3次尝试；长集按单元累计。</p></Panel>
      <div className="drama-cards">{rows(p.episodes).map(e => <article className="drama-card" key={e.id}>
        <label className="drama-episode-select"><input type="checkbox" aria-label={`选择第${e.episode_number}集`} checked={selected.includes(e.episode_number)} disabled={e.status!=='draft' || busy}
          onChange={event => setSelected(event.target.checked ? [...selected,e.episode_number] : selected.filter(n=>n!==e.episode_number))}/><strong>{e.title}</strong></label>
        <p>{e.outline.synopsis}</p><p>{statusNames[e.status] || e.status} · {e.progress}% · 设定v{e.plan_version}</p>
        {!!e.error && <p role="alert">{e.error}</p>}
        <div className="drama-actions"><button className="button" onClick={() => openEpisode(e.id)}>进入剧集</button>
          {['queued','producing','scripting','waiting_remote','waiting_dependency'].includes(e.status) && <button className="button" disabled={busy} onClick={() => void operation('actions',{action:'pause',episode_id:e.id})}>暂停</button>}
          {['paused','blocked','failed','waiting_dependency'].includes(e.status) && <button className="button" disabled={busy} onClick={() => void operation('actions',{action:e.status==='blocked'?'retry':'resume',episode_id:e.id})}>继续此集</button>}
          {!['approved','cancelled','draft','awaiting_final'].includes(e.status) && <button className="button" disabled={busy} onClick={() => void operation('actions',{action:'cancel',episode_id:e.id})}>取消此集</button>}
          {e.status==='awaiting_final' && <button className="button primary" onClick={() => {setReviewing(e);setFeedback('');}}>观看与验收</button>}
        </div>
      </article>)}</div>
    </>}
    {tab === 'assets' && <Panel title="全剧共享设定"><p>全剧角色使用固定身份和声线；生成素材按设定版本引用。</p>
      {rows(p.plans).at(-1)?.document && <>{rows(p.plans).at(-1)!.document.characters.map((c: RecordData) => <div className="drama-card" key={c.character_id}><strong>{c.name}</strong><p>{c.identity_constraints}</p><p>{c.default_wardrobe} · 声线 {c.voice_id}</p></div>)}
        {['scenes','props'].map(kind => <div key={kind}><h3>{kind==='scenes'?'场景':'道具'}</h3>{rows(rows(p.plans).at(-1)!.document[kind]).map(v => <p key={v.entity_id}>{v.name}：{v.description}</p>)}</div>)}</>}
    </Panel>}
    {tab === 'batches' && <Panel title="生成批次">{rows(p.batches).map(b => <div className="drama-card" key={b.id}><strong>第{b.episode_numbers.join('、')}集 · 设定v{b.plan_version}</strong>
      <p>{b.members.map((e: RecordData) => `第${e.number}集 ${statusNames[e.status] || e.status}`).join('；')}</p>
      <div className="drama-actions">{['pause','resume','cancel'].map(action => <button className="button" disabled={busy} key={action} onClick={() => void operation('actions',{action,batch_id:b.id})}>{action==='pause'?'暂停批次':action==='resume'?'继续批次':'取消批次'}</button>)}</div>
    </div>)}</Panel>}
    {tab === 'results' && <Panel title="逐集成果">{rows(p.episodes).map(e => <div className="drama-card" key={e.id}><strong>{e.title}</strong>{e.delivery ? <><video className="drama-video" controls preload="metadata" src={mediaUrl(e.delivery.video_url || e.delivery.final?.path)}/>
      <div className="drama-actions">{[['video_url','成片'],['clean_video_url','无字幕成片'],['subtitle_url','字幕']].map(([key,label]) => e.delivery[key] && <a className="button" key={key} href={mediaUrl(e.delivery[key]) || ''} download>{label}</a>)}</div></> : <p>{e.error || statusNames[e.status] || '尚未生成'}</p>}</div>)}</Panel>}
    {reviewing && <Modal id="review-episode" title={reviewing.title+' · 终片验收'} onClose={() => setReviewing(null)}><video className="drama-video" controls src={mediaUrl(reviewing.delivery.video_url || reviewing.delivery.final?.path)}/>
      <Field label="局部修改或剪辑意见"><textarea rows={4} value={feedback} onChange={e=>setFeedback(e.target.value)}/></Field>
      <div className="drama-actions"><button className="button primary" disabled={busy} onClick={() => void operation('actions',{action:'approve',episode_id:reviewing.id})}>通过本集</button>
        {['revise','reedit'].map(action => <button className="button" key={action} disabled={busy || feedback.trim().length<2} onClick={() => void operation('actions',{action,episode_id:reviewing.id,feedback})}>{action==='revise'?'局部修改':'重新剪辑'}</button>)}</div>
    </Modal>}
  </>;
}
