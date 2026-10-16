import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request, rows, mediaUrl, type RecordData } from "./api";
import { Heading, Panel, Field, ErrorNotice, Empty, Loading } from "./ui";

export const materialKinds: Record<string, string> = { character: "人物", scene: "场景", style: "画风", composition: "组合参考" };

export async function readMaterialFile(file: File): Promise<string> {
  if (file.size > 8 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type))
    throw new Error("请选择不超过8 MiB的PNG/JPEG/WebP图片。");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取图片"));
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.readAsDataURL(file);
  });
}

export function MaterialLibrary({ project, notify }: { project?: RecordData; notify: (message: string) => void }) {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<RecordData | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const query = useQuery({ queryKey: ["materials", project?.id, search, kind, offset],
    queryFn: () => request("/library?" + new URLSearchParams({ ...(project ? { project_id: project.id } : {}), q: search, kind, offset: String(offset) })) });
  const selected = useQuery({ queryKey: ["material-selection", project?.id], enabled: !!project,
    queryFn: () => request("/projects/" + project!.id + "/materials") });
  async function run(work: () => Promise<unknown>, message: string) {
    setBusy(true); setFailure("");
    try { await work(); notify(message); await client.invalidateQueries(); }
    catch (error) { setFailure((error as Error).message); }
    finally { setBusy(false); }
  }
  async function select(asset: RecordData, role: string, shotIds: string[]) {
    const refs = rows(selected.data?.selected).map(row => row.binding).filter(ref => !(ref.kind === asset.kind && (asset.kind === "style" || ref.role === role)));
    refs.push({ asset_id: asset.asset_id, version: asset.version, role, ...(shotIds.length ? { shot_ids: shotIds } : {}) });
    await request("/projects/" + project!.id + "/materials", { action: "select", expected_revision: selected.data!.revision, references: refs.map(({ asset_id, version, role, shot_ids }) => ({ asset_id, version, role, ...(shot_ids ? { shot_ids } : {}) })) });
  }
  return <div className="stack">
    <Heading eyebrow="MATERIAL LIBRARY" title={project ? "本剧素材与定稿" : "统一素材库"}
      description={project ? "选用人物、场景和共同画风，集中确认后用于后续镜头制作。剧内修改保留独立版本。" : "保存可跨剧复用的人物、场景、画风与同框参考。"} />
    <ErrorNotice error={failure || query.error || selected.error} />
    <div className="form-row">
      <Field label="搜索素材"><input value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></Field>
      <Field label="素材分类"><select value={kind} onChange={e => { setKind(e.target.value); setOffset(0); }}><option value="">全部分类</option>{Object.entries(materialKinds).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
      <button className="button primary" onClick={() => setEditing({ kind: kind || "character", data: { document: { name: "", description: "" } } })}>新建素材</button>
    </div>
    {editing && <MaterialEditor key={editing.asset_id || "new"} asset={editing} project={project} busy={busy} onCancel={() => setEditing(null)}
      save={(document, uploads, scope) => run(async () => {
        let response = await request("/library", { action: editing.asset_id ? "revise" : "create", asset_id: editing.asset_id, expected_version: editing.version, kind: editing.kind,
          project_id: project?.id, document, scope });
        // Each upload is independently versioned and bounded; a failed later file leaves earlier views available.
        for (const file of uploads) response = await request("/library", { action: "upload", project_id: project?.id, asset_id: response.asset.asset_id,
          expected_version: response.asset.version, image_base64: await readMaterialFile(file), label: file.name });
        setEditing(null);
      }, "素材已保存，可继续添加视图或选入项目。")}
    />}
    {project && selected.data && <Panel title="全剧定稿" description="确认前请检查人物身份、画风及同框对应。更新选择后，旧定稿继续使用，直到明确确认新版。">
      {rows(selected.data.selected).map(row => <div className="row-between" key={row.binding.kind + row.binding.role}>
        <span>{materialKinds[row.binding.kind]} · {row.asset.data.document.name} · v{row.binding.version} · {row.binding.role}</span>
        <button className="button" disabled={busy} onClick={() => run(() => request("/projects/" + project.id + "/materials", { action: "select", expected_revision: selected.data!.revision,
          references: rows(selected.data!.selected).filter(other => other !== row).map(other => { const { asset_id, version, role, shot_ids } = other.binding; return { asset_id, version, role, ...(shot_ids ? { shot_ids } : {}) }; }) }), "已更新待定稿选择。")}>移出本批</button>
      </div>)}
      <p>{selected.data.package ? "已有定稿，确认新的选择会显示并更新相关镜头的制作版本。" : "尚未定稿。"}</p>
      <button className="button primary" disabled={busy || !rows(selected.data.selected).length} onClick={() => run(() => request("/projects/" + project.id + "/materials", { action: "confirm", expected_revision: selected.data!.revision, confirmed: true }), "本批视觉素材已定稿，后续制作使用固定版本。")}>确认本批视觉定稿</button>
    </Panel>}
    {!query.data ? <Loading /> : !rows(query.data.assets).length ? <Empty title="还没有素材">上传参考图片，或先建立人物和场景设定。</Empty> : <div className="asset-grid">
      {rows(query.data.assets).map(asset => <MaterialCard key={asset.asset_id + ":" + asset.version} asset={asset} project={project} selected={selected.data} busy={busy}
        edit={() => {
          if (project && asset.library_key === "global") run(async () => {
            const response = await request("/library", { action: "clone", project_id: project.id, asset_id: asset.asset_id, expected_version: asset.version, scope: "project" });
            setEditing(response.asset);
          }, "已建立剧内副本，可独立修改。");
          else setEditing(asset);
        }} run={run} select={select} />)}
    </div>}
    <div className="form-row"><button className="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button><button className="button" disabled={!query.data?.has_more} onClick={() => setOffset(offset + 50)}>下一页</button></div>
  </div>;
}

function MaterialCard({ asset, project, selected, busy, edit, run, select }: {
  asset: RecordData; project?: RecordData; selected?: RecordData; busy: boolean; edit: () => void;
  run: (work: () => Promise<unknown>, message: string) => Promise<void>;
  select: (asset: RecordData, role: string, shots: string[]) => Promise<void>;
}) {
  const roles = project?.production_profile?.characters || [{ character_id: "female", name: "角色一" }, { character_id: "male", name: "角色二" }];
  const [role, setRole] = useState(asset.kind === "character" ? roles[0].character_id : asset.asset_id);
  const [shots, setShots] = useState<string[]>([]);
  const document = asset.data.document;
  return <Panel title={document.name + " · v" + asset.version} description={materialKinds[asset.kind] + " · " + (asset.library_key === "global" ? "统一库" : "剧内库") + " · " + (asset.status === "approved" ? "已确认" : "候选")}>
    <div className="material-views">{rows(asset.data.images).map(image => <figure key={image.path}><img src={mediaUrl("/media/" + image.path)} alt={document.name + " · " + image.label} /><figcaption>{image.label}</figcaption></figure>)}</div>
    <p>{document.description}</p><p>{document.identity_constraints || document.visual_rules}</p>
    <div className="form-row">
      <button className="button" disabled={busy} onClick={edit}>编辑 / 添加图片</button>
      {asset.status !== "approved" && <button className="button" disabled={busy || !rows(asset.data.images).length} onClick={() => run(() => request("/library", { action: "approve", project_id: project?.id, asset_id: asset.asset_id, expected_version: asset.version, confirmed: true }), "此版本已确认。")}>确认参考</button>}
      {project && <button className="button" disabled={busy} onClick={() => run(() => request("/library", { action: "clone", project_id: project.id, asset_id: asset.asset_id, expected_version: asset.version,
        scope: asset.library_key === "global" ? "project" : "global" }), "已保存独立副本，来源与原素材保持可追溯。")}>{asset.library_key === "global" ? "复制为剧内造型" : "保存到统一库"}</button>}
    </div>
    {project && selected && <div className="stack">
      {asset.kind === "character" && <Field label={document.name + "对应角色"}><select value={role} onChange={e => setRole(e.target.value)}>{roles.map((r: RecordData) => <option key={r.character_id} value={r.character_id}>{r.name}（{r.character_id}）</option>)}</select></Field>}
      {(asset.kind === "scene" || asset.kind === "composition") && <Field label={document.name + "适用镜头"}><select multiple value={shots} onChange={e => setShots(Array.from(e.target.selectedOptions, option => option.value))}>{rows(selected.shots).map(shot => <option key={shot.id} value={shot.id}>{shot.id} · {shot.prompt}</option>)}</select><small>也可由分镜根据素材设定关联，人物按每镜实际出镜角色引用。</small></Field>}
      <button className="button" disabled={busy} onClick={() => run(() => select(asset, role, shots), "已选入本批，集中确认后正式采用。")}>选入本剧</button>
    </div>}
    {rows(asset.affected_shots).length > 0 && <details><summary>采用此版的影响范围</summary>{rows(asset.affected_shots).map(impact => <p key={impact.project_id}>{impact.project_id}：{impact.shot_ids.join("、")}</p>)}</details>}
  </Panel>;
}

function MaterialEditor({ asset, project, busy, save, onCancel }: { asset: RecordData; project?: RecordData; busy: boolean;
  save: (document: RecordData, uploads: File[], scope: string) => Promise<void>; onCancel: () => void }) {
  const [document, setDocument] = useState<RecordData>(asset.data.document);
  const [files, setFiles] = useState<File[]>([]);
  const [scope, setScope] = useState(project ? "project" : "global");
  const edit = (field: string, value: string) => setDocument(old => ({ ...old, [field]: value }));
  return <Panel title={asset.asset_id ? "追加素材版本" : "新建" + materialKinds[asset.kind]}>
    <form className="stack" onSubmit={e => { e.preventDefault(); save(document, files, scope); }}>
      <Field label="素材名称"><input required maxLength={100} value={document.name} onChange={e => edit("name", e.target.value)} /></Field>
      <Field label="素材说明"><textarea maxLength={4000} value={document.description || ""} onChange={e => edit("description", e.target.value)} /></Field>
      {asset.kind === "character" && <><Field label="不变的身份与外观"><textarea maxLength={4000} value={document.identity_constraints || ""} onChange={e => edit("identity_constraints", e.target.value)} /></Field><Field label="默认服装"><input value={document.default_wardrobe || ""} onChange={e => edit("default_wardrobe", e.target.value)} /></Field></>}
      {asset.kind === "style" && <Field label="共同画风规则"><textarea maxLength={4000} value={document.visual_rules || ""} onChange={e => edit("visual_rules", e.target.value)} /></Field>}
      <Field label="添加参考图片"><input type="file" multiple accept="image/png,image/jpeg,image/webp" onChange={e => setFiles(Array.from(e.target.files || []))} /><small>PNG/JPEG/WebP，单张不超过8 MiB，64–4096像素。</small></Field>
      {!asset.asset_id && project && <Field label="保存位置"><select value={scope} onChange={e => setScope(e.target.value)}><option value="project">本剧素材库</option><option value="global">统一素材库</option></select></Field>}
      <div className="form-row"><button className="button primary" type="submit" disabled={busy}>保存素材</button><button className="button" type="button" disabled={busy} onClick={onCancel}>取消</button></div>
    </form>
  </Panel>;
}
