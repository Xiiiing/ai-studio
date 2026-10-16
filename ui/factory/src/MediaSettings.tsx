import { useState } from "react";
import { Field, Panel, Modal, ErrorNotice } from "./ui";
import { request, type RecordData } from "./api";

type Kind = "image" | "audio" | "video";
const labels = {image: "图像", audio: "配音", video: "自定义视频工作流"};
const roles = {image: "image_generator", audio: "speech_generator", video: "video_generator"};
export function MediaSettings({draft, inventory, onChange, notify}: {
  draft: RecordData; inventory: RecordData;
  onChange: (fn: (old: RecordData) => RecordData) => void; notify: (s: string) => void;
}) {
  const [editing, setEditing] = useState<{kind: Kind; id: string} | null>(null);
  const [templateJSON, setTemplateJSON] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<RecordData>({});
  const [templateId, setTemplateId] = useState("");
  return <>
    {(["image", "audio", "video"] as Kind[]).map((kind) => inventory.media_presets?.[kind] && <Panel key={kind} title={labels[kind] + "连接"} description="选择自己的制作能力；接口检查不生成素材。"
      action={<button className="button" onClick={() => setEditing({kind, id: ""})}>添加{labels[kind]}连接</button>}>
      <Field label={labels[kind] + "使用的连接"}><select value={draft[kind + "_roles"]?.[roles[kind]] || ""} onChange={(e) => onChange((d) => ({...d, [kind + "_roles"]: {[roles[kind]]: e.target.value}}))}>
        {Object.entries(draft[kind + "_profiles"] || {}).map(([id,p]: [string, any]) => <option key={id} value={id} disabled={p.enabled === false}>{id} · {p.model}</option>)}
      </select></Field>
      <div className="model-grid">{Object.entries(draft[kind + "_profiles"] || {}).filter(([,p]: [string,any]) => kind !== "video" || ["comfyui_local", "comfyui_external"].includes(p.provider)).map(([id,p]: [string,any]) => <article key={id} className="model-card">
        <h3>{id}</h3><p>{inventory.media_providers?.[p.provider]?.label || p.provider} · {p.model}</p>
        <small>{p.base_url || "使用已配置的制作环境"}{p.api_key_env ? ` · 密钥变量：${p.api_key_env}` : ""}</small>
        <div className="button-row"><button className="button" onClick={() => setEditing({kind,id})}>编辑{labels[kind]}连接</button>
          <button className="button" disabled={JSON.stringify(draft[kind+"_profiles"]) !== JSON.stringify(inventory[kind+"_profiles"])} onClick={async () => {
            try {const r = await request("/models/test", {kind, profile_id:id}); setResult((old)=>({...old,[kind+id]:r.message || r.status}));} catch(e) {setError((e as Error).message);}
          }}>检查{labels[kind]}配置</button></div><p role="status">{result[kind+id]}</p>
      </article>)}</div>
    </Panel>)}
    {inventory.media_presets && <Panel title="导入 ComfyUI 工作流" description="导入 API 格式工作流及输入／输出映射；不会运行或安装节点。不同参考图数量可以指定不同模板。">
      <Field label="工作流模板 JSON"><textarea value={templateJSON} onChange={(e)=>setTemplateJSON(e.target.value)} rows={8} placeholder={'{"name":"我的图像工作流","kind":"image","graph":{},"mappings":[{"node":"6","input":"text","source":"prompt"}],"output":{"node":"9","field":"images","index":0}}'} /></Field>
      <div className="button-row"><button className="button" onClick={async()=>{try {const t=await request("/workflow-templates",JSON.parse(templateJSON));setTemplateId(t.id);notify("工作流已导入，请将模板编号配置到制作连接。");}catch(e){setError((e as Error).message);}}}>导入模板</button>
      <button className="button" disabled={!templateId} onClick={async()=>{try{const t=await request("/workflow-templates/"+templateId);const url=URL.createObjectURL(new Blob([JSON.stringify(t,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=templateId+".json";a.click();URL.revokeObjectURL(url);}catch(e){setError((e as Error).message);}}}>导出模板</button></div>
      <Field label="模板编号"><input value={templateId} onChange={(e)=>setTemplateId(e.target.value)} /></Field>
    </Panel>}
    <ErrorNotice error={error}/>
    {editing && <MediaEditor key={editing.kind+editing.id} kind={editing.kind} id={editing.id} current={draft[editing.kind+"_profiles"]?.[editing.id] || {}} presets={inventory.media_presets[editing.kind]} labels={inventory.media_providers}
      onClose={()=>setEditing(null)} onSave={(id,p)=>{const kind=editing.kind; if(!editing.id && draft[kind+"_profiles"]?.[id]) throw new Error("连接编号已存在");onChange(d=>({...d,[kind+"_profiles"]:{...d[kind+"_profiles"],[id]:p}}));setEditing(null);}}/>}
  </>;
}
function MediaEditor({kind,id,current,presets,labels: providerLabels,onClose,onSave}: {kind:Kind;id:string;current:RecordData;presets:RecordData;labels:RecordData;onClose:()=>void;onSave:(id:string,p:RecordData)=>void}) {
  const [provider,setProvider]=useState(current.provider || Object.keys(presets)[0]);
  const [values,setValues]=useState<RecordData>({...presets[provider],...current});
  const [error,setError]=useState("");
  const field=(name:string,label:string,required=false)=><Field label={label}><input name={name} defaultValue={values[name] || ""} required={required}/></Field>;
  const advanced=Object.fromEntries(Object.entries(values).filter(([k])=>!["profile","provider","model","base_url","api_key_env","workflow_template","python_path","model_dir","enabled"].includes(k)));
  return <Modal id="media-dialog" title={labels[kind]+"连接设置"} onClose={onClose}><form key={provider} onSubmit={e=>{e.preventDefault();try{
    const f=Object.fromEntries(new FormData(e.currentTarget));const p={...JSON.parse(String(f.advanced)),provider,model:String(f.model).trim(),enabled:f.enabled==="on"};
    for(const key of ["base_url","api_key_env","workflow_template","python_path","model_dir"]) if(f[key]) p[key]=String(f[key]).trim();
    onSave(String(f.id),p);
  }catch(e){setError((e as Error).message);}}}>
    <Field label="制作连接编号"><input name="id" defaultValue={id} readOnly={!!id} required pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"/></Field>
    <Field label="制作服务类型"><select value={provider} onChange={e=>{setProvider(e.target.value);setValues(presets[e.target.value] || current);}}>
      {!presets[provider] && <option value={provider}>{providerLabels?.[provider]?.label || provider}</option>}
      {Object.keys(presets).map(p=><option key={p} value={p}>{providerLabels?.[p]?.label || p}</option>)}
    </select></Field>
    {field("model","制作模型标识",true)}
    {provider === "comfyui_external" && field("base_url","ComfyUI 服务地址",true)}
    {values.base_url && provider !== "comfyui_external" && field("base_url","API 地址",true)}
    {field("api_key_env","制作密钥环境变量")}
    {kind !== "audio" && field("workflow_template","默认工作流模板编号")}
    {provider === "qwen_tts_local" && <>{field("python_path","TTS Python 绝对路径")}{field("model_dir","TTS 模型绝对目录")}</>}
    <details><summary>高级设置：网络、模型文件、声线和参考图模板</summary><Field label="制作高级设置 JSON"><textarea name="advanced" rows={6} defaultValue={JSON.stringify(advanced,null,2)}/></Field></details>
    <label className="check"><input name="enabled" type="checkbox" defaultChecked={values.enabled!==false}/>启用制作连接</label>
    <ErrorNotice error={error}/><div className="dialog-actions"><button type="button" className="button" onClick={onClose}>取消</button><button className="button primary" type="submit">加入制作配置草稿</button></div>
  </form></Modal>;
}
