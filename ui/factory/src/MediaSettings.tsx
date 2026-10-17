import {
  CredentialField,
  ProviderSymbol,
  endpointValues,
} from "./ConnectionFields";
import { useRef, useState } from "react";
import { Field, Panel, Modal, ErrorNotice } from "./ui";
import { request, type RecordData } from "./api";

type Kind = "image" | "audio" | "video";
const labels = { image: "图像", audio: "配音", video: "自定义视频工作流" };
const roles = {
  image: "image_generator",
  audio: "speech_generator",
  video: "video_generator",
};
export function MediaSettings({
  draft,
  inventory,
  onChange,
  notify,
  mode = "mixed",
  saving = false,
  dirty = false,
  generation = 0,
  probes,
  probing,
  onProbe,
}: {
  mode?: string;
  saving?: boolean;
  dirty?: boolean;
  generation?: number;
  probes?: RecordData;
  probing?: string;
  onProbe?: (kind: Kind, id: string) => void;
  draft: RecordData;
  inventory: RecordData;
  onChange: (fn: (old: RecordData) => RecordData) => void;
  notify: (s: string) => void;
}) {
  const [editing, setEditing] = useState<{ kind: Kind; id: string } | null>(
    null,
  );
  const [templateJSON, setTemplateJSON] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<RecordData>({});
  const [pending, setPending] = useState("");
  const pendingRef = useRef(false);
  const fingerprint = JSON.stringify([
    draft,
    inventory.revision,
    generation,
    saving,
    dirty,
  ]);
  const latest = useRef(fingerprint);
  latest.current = fingerprint;
  const currentResult =
    probes || (result.fingerprint === fingerprint ? result.entries || {} : {});
  const checking = probing === undefined ? pending : probing;
  const draftChanged =
    dirty ||
    ["image", "audio", "video"].some(
      (kind) =>
        JSON.stringify(draft[kind + "_profiles"]) !==
        JSON.stringify(inventory[kind + "_profiles"]),
    );
  async function check(kind: Kind, id: string) {
    if (saving || draftChanged || checking || pendingRef.current) return;
    if (onProbe) {
      onProbe(kind, id);
      return;
    }
    pendingRef.current = true;
    const key = kind + ":" + id;
    const started = fingerprint;
    setPending(key);
    let response: RecordData;
    try {
      response = await request("/models/test", { kind, profile_id: id });
    } catch (e) {
      response = { status: "error", message: (e as Error).message };
    }
    if (latest.current === started)
      setResult((old) => ({
        fingerprint: started,
        entries: {
          ...(old.fingerprint === started ? old.entries : {}),
          [key]: response,
        },
      }));
    pendingRef.current = false;
    setPending("");
  }
  const [templateId, setTemplateId] = useState("");
  return (
    <>
      {(["image", "audio", "video"] as Kind[]).map(
        (kind) =>
          inventory.media_presets?.[kind] &&
          (kind !== "video" || mode !== "api") && (
            <Panel
              key={kind}
              title={labels[kind] + "连接"}
              description="选择自己的制作能力；接口检查不生成素材。"
              action={
                <button
                  className="button"
                  disabled={saving}
                  onClick={() => setEditing({ kind, id: "" })}
                >
                  添加{labels[kind]}连接
                </button>
              }
            >
              <Field label={labels[kind] + "使用的连接"}>
                <select
                  disabled={saving}
                  value={draft[kind + "_roles"]?.[roles[kind]] || ""}
                  onChange={(e) =>
                    onChange((d) => ({
                      ...d,
                      [kind + "_roles"]: { [roles[kind]]: e.target.value },
                    }))
                  }
                >
                  {Object.entries(draft[kind + "_profiles"] || {}).map(
                    ([id, p]: [string, any]) => (
                      <option
                        key={id}
                        value={id}
                        disabled={p.enabled === false}
                      >
                        {id} · {p.model}
                      </option>
                    ),
                  )}
                </select>
              </Field>
              <div className="model-grid">
                {Object.entries(draft[kind + "_profiles"] || {})
                  .filter(
                    ([, p]: [string, any]) =>
                      kind !== "video" ||
                      ["comfyui_local", "comfyui_external"].includes(
                        p.provider,
                      ),
                  )
                  .map(([id, p]: [string, any]) => (
                    <article key={id} className="model-card">
                      <ProviderSymbol provider={p.provider} />
                      <h3>{id}</h3>
                      <p>
                        {inventory.media_providers?.[p.provider]?.label ||
                          p.provider}{" "}
                        · {p.model}
                      </p>
                      <small>
                        {p.base_url || "使用已配置的制作环境"}
                        {p.api_key_env ? " · 已关联服务端凭据" : ""}
                      </small>
                      <div className="button-row">
                        <button
                          className="button"
                          disabled={saving}
                          onClick={() => setEditing({ kind, id })}
                        >
                          编辑{labels[kind]}连接
                        </button>
                        <button
                          className="button"
                          disabled={
                            saving ||
                            draftChanged ||
                            !!checking ||
                            p.enabled === false
                          }
                          onClick={() => check(kind, id)}
                        >
                          {checking === kind + ":" + id
                            ? "检测中…"
                            : "检查" + labels[kind] + "配置"}
                        </button>
                      </div>
                      {currentResult[kind + ":" + id] && (
                        <div className="probe-result" role="status">
                          {currentResult[kind + ":" + id].message ||
                            currentResult[kind + ":" + id].error?.message ||
                            "已完成配置检测；未运行生成任务"}
                        </div>
                      )}
                      {p.enabled === false && <small>已停用</small>}
                      {draftChanged && (
                        <small>配置待保存，保存后可检查连接</small>
                      )}
                    </article>
                  ))}
              </div>
            </Panel>
          ),
      )}
      {inventory.media_presets && mode !== "api" && (
        <details>
          <summary>高级：ComfyUI 工作流导入／导出</summary>
          <Panel
            title="导入 ComfyUI 工作流"
            description="导入 API 格式工作流及输入／输出映射；不会运行或安装节点。不同参考图数量可以指定不同模板。"
          >
            <Field label="工作流模板 JSON">
              <textarea
                disabled={saving}
                value={templateJSON}
                onChange={(e) => setTemplateJSON(e.target.value)}
                rows={8}
                placeholder={
                  '{"name":"我的图像工作流","kind":"image","graph":{},"mappings":[{"node":"6","input":"text","source":"prompt"}],"output":{"node":"9","field":"images","index":0}}'
                }
              />
            </Field>
            <div className="button-row">
              <button
                className="button"
                disabled={saving}
                onClick={async () => {
                  try {
                    const t = await request(
                      "/workflow-templates",
                      JSON.parse(templateJSON),
                    );
                    setTemplateId(t.id);
                    notify("工作流已导入，请将模板编号配置到制作连接。");
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                导入模板
              </button>
              <button
                className="button"
                disabled={saving || !templateId}
                onClick={async () => {
                  try {
                    const t = await request(
                      "/workflow-templates/" + templateId,
                    );
                    const url = URL.createObjectURL(
                      new Blob([JSON.stringify(t, null, 2)], {
                        type: "application/json",
                      }),
                    );
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = templateId + ".json";
                    a.click();
                    URL.revokeObjectURL(url);
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                导出模板
              </button>
            </div>
            <Field label="模板编号">
              <input
                disabled={saving}
                value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
              />
            </Field>
          </Panel>
        </details>
      )}
      <ErrorNotice error={error} />
      {editing && (
        <MediaEditor
          key={editing.kind + editing.id}
          disabled={saving}
          preferLocal={mode === "local"}
          kind={editing.kind}
          id={editing.id}
          current={draft[editing.kind + "_profiles"]?.[editing.id] || {}}
          pendingKey={draft.secrets?.[editing.kind + ":" + editing.id] || ""}
          presets={inventory.media_presets[editing.kind]}
          labels={inventory.media_providers}
          onClose={() => setEditing(null)}
          onSave={(id, p, secret) => {
            if (saving) return;
            const kind = editing.kind;
            if (!editing.id && draft[kind + "_profiles"]?.[id])
              throw new Error("连接编号已存在");
            onChange((d) => ({
              ...d,
              [kind + "_profiles"]: { ...d[kind + "_profiles"], [id]: p },
              ...(secret !== undefined
                ? { secrets: { ...d.secrets, [kind + ":" + id]: secret } }
                : {}),
            }));
            setEditing(null);
          }}
        />
      )}
    </>
  );
}
function MediaEditor({
  disabled = false,
  kind,
  id,
  current,
  presets,
  labels: providerLabels,
  onClose,
  onSave,
  preferLocal,
  pendingKey,
}: {
  pendingKey: string;
  disabled?: boolean;
  preferLocal: boolean;
  kind: Kind;
  id: string;
  current: RecordData;
  presets: RecordData;
  labels: RecordData;
  onClose: () => void;
  onSave: (id: string, p: RecordData, secret?: string) => void;
}) {
  const [provider, setProvider] = useState(
    current.provider ||
      (preferLocal
        ? Object.keys(presets)[0]
        : kind === "image" && presets.dashscope_image
          ? "dashscope_image"
          : kind === "audio" && presets.dashscope_tts
            ? "dashscope_tts"
            : Object.keys(presets)[0]),
  );
  const [values, setValues] = useState<RecordData>({
    ...presets[provider],
    ...current,
  });
  const [error, setError] = useState("");
  const field = (name: string, label: string, required = false) => (
    <Field label={label}>
      <input
        name={name}
        defaultValue={values[name] || ""}
        required={required}
      />
    </Field>
  );
  const advanced = Object.fromEntries(
    Object.entries(values).filter(
      ([k]) =>
        ![
          "id",
          "profile",
          "provider",
          "model",
          "base_url",
          "api_key_env",
          "workflow_template",
          "python_path",
          "model_dir",
          "default_voice",
          "language",
          "enabled",
        ].includes(k),
    ),
  );
  return (
    <Modal
      id="media-dialog"
      title={labels[kind] + "连接设置"}
      onClose={onClose}
    >
      <form
        key={provider}
        onSubmit={(e) => {
          e.preventDefault();
          try {
            const f = Object.fromEntries(new FormData(e.currentTarget));
            const p = {
              ...JSON.parse(String(f.advanced)),
              provider,
              model: String(f.model).trim(),
              enabled: f.enabled === "on",
            };
            for (const key of [
              "base_url",
              "api_key_env",
              "workflow_template",
              "python_path",
              "model_dir",
              "default_voice",
              "language",
            ])
              if (f[key]) p[key] = String(f[key]).trim();
            if (f.workspace_id) {
              p.base_url =
                `https://${f.workspace_id}.cn-beijing.maas.aliyuncs.com/` +
                (provider === "dashscope_image"
                  ? "compatible-mode/v1"
                  : "api/v1");
            }
            if (p.base_url) {
              const changed =
                !values.base_url ||
                new URL(p.base_url).origin !== new URL(values.base_url).origin;
              Object.assign(
                p,
                endpointValues({
                  ...p,
                  network_mode:
                    changed && p.network_mode === values.network_mode
                      ? "auto"
                      : p.network_mode,
                }),
              );
              if (changed)
                for (const key of [
                  "tls_ca_file_env",
                  "tls_client_cert_env",
                  "tls_client_key_env",
                ])
                  delete p[key];
            }
            onSave(
              String(f.id),
              p,
              f.api_key ? String(f.api_key).trim() : undefined,
            );
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        <fieldset disabled={disabled} className="model-editor-fields">
          <Field label="制作连接编号">
            <input
              name="id"
              defaultValue={
                values.id ??
                (id || `connection-${Math.random().toString(36).slice(2, 9)}`)
              }
              readOnly={!!id}
              required
              pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
            />
          </Field>
          <Field label="制作服务类型">
            <select
              value={provider}
              onChange={(e) => {
                const enteredId = new FormData(e.currentTarget.form!).get("id");
                setProvider(e.target.value);
                setValues({ ...presets[e.target.value], id: enteredId });
              }}
            >
              {!presets[provider] && (
                <option value={provider}>
                  {providerLabels?.[provider]?.label || provider}
                </option>
              )}
              {Object.keys(presets).map((p) => (
                <option key={p} value={p}>
                  {providerLabels?.[p]?.label || p}
                </option>
              ))}
            </select>
          </Field>
          <div className="provider-intro">
            <ProviderSymbol provider={provider} />
            <strong>{providerLabels?.[provider]?.label || provider}</strong>
          </div>
          {field("model", "制作模型标识", true)}
          {provider === "comfyui_external" &&
            field("base_url", "ComfyUI 服务地址", true)}
          {values.base_url &&
            provider !== "comfyui_external" &&
            field("base_url", "API 地址", true)}
          <CredentialField
            saved={!!id && !!current.api_key_env}
            value={provider === current.provider ? pendingKey : ""}
          />
          {provider.startsWith("dashscope_") && (
            <Field label="百炼工作空间 ID（专属入口需要时填写）">
              <input name="workspace_id" pattern="[A-Za-z0-9-]+" />
            </Field>
          )}
          {provider === "dashscope_tts" && (
            <>
              {field("default_voice", "默认云端声线", true)}
              {field("language", "配音语言", true)}
            </>
          )}
          {kind !== "audio" &&
            ["comfyui_local", "comfyui_external"].includes(provider) &&
            field("workflow_template", "默认工作流模板编号")}
          {provider === "qwen_tts_local" && (
            <>
              {field("python_path", "TTS Python 绝对路径")}
              {field("model_dir", "TTS 模型绝对目录")}
            </>
          )}
          <details>
            <summary>高级设置：网络、模型文件、声线和参考图模板</summary>
            {field("api_key_env", "制作密钥环境变量")}
            <Field label="制作高级设置 JSON">
              <textarea
                name="advanced"
                rows={6}
                defaultValue={JSON.stringify(advanced, null, 2)}
              />
            </Field>
          </details>
          <label className="check">
            <input
              name="enabled"
              type="checkbox"
              defaultChecked={values.enabled !== false}
            />
            启用制作连接
          </label>
          <ErrorNotice error={error} />
          <div className="dialog-actions">
            <button type="button" className="button" onClick={onClose}>
              取消
            </button>
            <button className="button primary" type="submit">
              加入制作配置草稿
            </button>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
