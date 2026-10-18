import { ServiceCategories } from "./ServiceCategories";
import { useRef, useState } from "react";
import { Plus } from "lucide-react";
import {
  CredentialField,
  endpointValues,
  ProviderSymbol,
} from "./ConnectionFields";
import {
  makeDraft,
  newProfileId,
  normalizeProfile,
  providerLabel,
  kindLabels,
  profileField,
  type ModelKind,
  type Service,
} from "./ModelConfig";
import { Field, Modal, ErrorNotice } from "./ui";
import type { RecordData } from "./api";

export function MediaServiceEditor({
  inventory,
  service,
  kind,
  onClose,
  onKindChange,
  onSave,
}: {
  inventory: RecordData;
  service?: Service;
  kind: Exclude<ModelKind, "llm">;
  onClose: () => void;
  onKindChange?: (kind: ModelKind) => void;
  onSave: (
    draft: RecordData,
    revision: string,
    selected: string,
  ) => Promise<void>;
}) {
  const snapshot = useRef({
    draft: makeDraft(inventory),
    revision: inventory.revision,
  }).current;
  const original = service?.profiles[0]?.[1];
  const [provider, setProvider] = useState(original?.provider || "");
  const [quality, setQuality] = useState(
    original?.quality ||
      (original?.provider === "comfyui_h3" ? "preview" : "production"),
  );
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState("");
  const presets = {
    ...inventory.media_presets?.[kind],
    ...(kind === "video" ? inventory.video_presets : {}),
  };
  const current: RecordData = {
    ...(original?.provider === "comfyui_h3" && !original.quality
      ? {}
      : presets[provider] || {}),
    ...original,
  };
  const cloud = provider.startsWith("dashscope_") || provider === "minimax";
  const external = provider === "comfyui_external";
  const h3 = provider === "comfyui_h3";
  const field = (key: string, label: string, options: RecordData = {}) => (
    <Field label={label} hint={options.hint}>
      <input
        name={key}
        defaultValue={options.value ?? current[key] ?? ""}
        type={options.type || "text"}
        min={options.min}
        max={options.max}
        step={options.step}
        required={options.required}
      />
    </Field>
  );
  const basic = new Set([
    "provider",
    "model",
    "enabled",
    "base_url",
    "api_key_env",
    "workflow_template",
    "default_voice",
    "language",
    "python_path",
    "model_dir",
    "quality",
    "steps",
    "shift_video",
    "shift_audio",
    "sampler",
  ]);
  const advanced = Object.fromEntries(
    Object.entries(current).filter(([key]) => !basic.has(key)),
  );
  return (
    <Modal
      id="media-dialog"
      title={
        provider ? `${kindLabels[kind]}服务设置` : `添加${kindLabels[kind]}服务`
      }
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {!provider ? (
        <div className="service-picker">
          {onKindChange && (
            <ServiceCategories kind={kind} onChange={onKindChange} />
          )}
          <p>选择已有适配器，连接制作服务器或云端 API。</p>
          <div className="provider-picker-grid">
            {Object.keys(presets).map((id) => (
              <button
                type="button"
                className="provider-pick"
                key={id}
                onClick={() => {
                  setProvider(id);
                  setQuality(presets[id]?.quality || "production");
                }}
              >
                <ProviderSymbol provider={id} />
                <span>{providerLabel(inventory, id)}</span>
                <Plus size={15} />
              </button>
            ))}
          </div>
        </div>
      ) : (
        <form
          key={provider}
          className="service-form"
          onSubmit={async (e) => {
            e.preventDefault();
            if (pending.current) return;
            setError("");
            const values = Object.fromEntries(new FormData(e.currentTarget));
            pending.current = true;
            setBusy(true);
            try {
              let p: RecordData = {
                ...current,
                ...JSON.parse(String(values.advanced)),
                provider,
                model: String(values.model).trim(),
                enabled: values.enabled === "on",
              };
              for (const key of [
                "base_url",
                "api_key_env",
                "workflow_template",
                "default_voice",
                "language",
                "python_path",
                "model_dir",
              ]) {
                if (values[key]) p[key] = String(values[key]).trim();
                else if (values[key] === "") delete p[key];
              }
              if (h3)
                p = normalizeProfile(
                  {
                    ...values,
                    provider,
                    model: p.model,
                    enabled: p.enabled ? "on" : "",
                    quality,
                  },
                  p,
                  true,
                );
              if (p.base_url) {
                const changed =
                  !current.base_url ||
                  new URL(p.base_url).origin !==
                    new URL(current.base_url).origin;
                const base = endpointValues({
                  ...p,
                  network_mode: changed ? "auto" : p.network_mode || "auto",
                });
                // Media APIs have their own endpoint conventions; only ComfyUI uses
                // the generic normalizer's root URL unchanged.
                Object.assign(p, base, {
                  base_url: external
                    ? base.base_url
                    : String(p.base_url).replace(/\/+$/, ""),
                });
                if (changed)
                  for (const key of [
                    "tls_ca_file_env",
                    "tls_client_cert_env",
                    "tls_client_key_env",
                  ])
                    delete p[key];
              }
              const draft = structuredClone(snapshot.draft),
                field = profileField(kind);
              const id =
                service?.profiles[0][0] ||
                newProfileId(draft[field] || {}, kind);
              draft[field] = { ...draft[field], [id]: p };
              if (values.api_key)
                draft.secrets = {
                  [`${kind}:${id}`]: String(values.api_key).trim(),
                };
              await onSave(draft, snapshot.revision, kind + ":" + id);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              pending.current = false;
              setBusy(false);
            }
          }}
        >
          <fieldset className="model-editor-fields" disabled={busy}>
            <div className="service-editor-brand">
              <ProviderSymbol provider={provider} />
              <div>
                <strong>{providerLabel(inventory, provider)}</strong>
                <small>
                  {h3
                    ? "制作服务器 · ComfyUI H3 工作流"
                    : cloud
                      ? "云端 API"
                      : "现有制作服务器"}
                </small>
              </div>
            </div>
            {external &&
              field("base_url", "服务 URL", {
                type: "url",
                required: true,
                hint: "ComfyUI 服务根地址，无需添加 /v1。",
              })}
            {(cloud || external) && (
              <CredentialField saved={!!original?.api_key_env} />
            )}
            {field("model", "模型 ID", { required: true })}
            {h3 && (
              <>
                <Field label="H3 质量档">
                  <select
                    name="quality"
                    value={quality}
                    onChange={(e) => setQuality(e.target.value)}
                  >
                    <option value="production">正式质量 · 基础模型</option>
                    <option value="preview">快速预览 · 4 步 Turbo LoRA</option>
                  </select>
                </Field>
                <p>
                  {quality === "preview"
                    ? "使用 4 步 Turbo LoRA，适合快速检查镜头与提示词。"
                    : "使用基础模型制作；采样步数越高，所需时间越长。"}
                </p>
                {quality === "production" && (
                  <div key={quality}>
                    {field("steps", "采样步数", {
                      type: "number",
                      min: 20,
                      max: 50,
                      required: true,
                      value:
                        current.quality === "production"
                          ? current.steps || 20
                          : 20,
                    })}
                    <details className="service-advanced">
                      <summary>采样参数</summary>
                      <div className="form-row">
                        {field("shift_video", "视频采样偏移", {
                          type: "number",
                          step: "any",
                          min: 1,
                          max: 20,
                          value:
                            current.quality === "production"
                              ? (current.shift_video ?? 12)
                              : 12,
                        })}
                        {field("shift_audio", "音频采样偏移", {
                          type: "number",
                          step: "any",
                          min: 1,
                          max: 10,
                          value:
                            current.quality === "production"
                              ? (current.shift_audio ?? 3)
                              : 3,
                        })}
                      </div>
                      <Field label="采样器">
                        <select
                          name="sampler"
                          defaultValue={
                            current.quality === "production"
                              ? current.sampler || "euler"
                              : "euler"
                          }
                        >
                          <option value="euler">Euler</option>
                          <option value="heun">Heun</option>
                        </select>
                      </Field>
                    </details>
                  </div>
                )}
              </>
            )}
            {provider === "dashscope_tts" && (
              <div className="form-row">
                {field("default_voice", "默认声线", { required: true })}
                {field("language", "配音语言", { required: true })}
              </div>
            )}
            {["comfyui_local", "comfyui_external"].includes(provider) &&
              field("workflow_template", "工作流模板编号", {
                hint: "在「高级工具」导入 API 工作流；留空使用既有模板。",
              })}
            {provider === "qwen_tts_local" && (
              <p className="muted">
                由制作服务器 worker 启动本机 Qwen3-TTS
                推理，使用该服务器的模型路径。
              </p>
            )}
            <details className="service-advanced">
              <summary>高级设置 · 网络与制作参数</summary>
              {cloud &&
                field("base_url", "服务 URL", { required: true, type: "url" })}
              {(cloud || external) && field("api_key_env", "服务端密钥变量")}
              {provider === "qwen_tts_local" && (
                <>
                  {field("python_path", "TTS Python 绝对路径")}
                  {field("model_dir", "TTS 模型绝对目录")}
                </>
              )}
              <Field
                label="高级制作参数 JSON"
                hint="保留下载规则、计费、声线映射及工作流参数。"
              >
                <textarea
                  name="advanced"
                  defaultValue={JSON.stringify(advanced, null, 2)}
                  rows={6}
                />
              </Field>
            </details>
            <label className="check">
              <input
                name="enabled"
                type="checkbox"
                defaultChecked={current.enabled !== false}
              />
              启用服务
            </label>
          </fieldset>
          <ErrorNotice error={error} />
          <div className="service-dialog-actions">
            <small>保存后可在制作分工中选择</small>
            <div>
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={onClose}
              >
                取消
              </button>
              <button type="submit" className="button primary" disabled={busy}>
                {busy ? "保存中…" : "保存连接"}
              </button>
            </div>
          </div>
        </form>
      )}
    </Modal>
  );
}
