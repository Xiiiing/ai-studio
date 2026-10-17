import {
  builtinProviders,
  CredentialField,
  endpointValues,
  protocolName,
  providerLinks,
  ProviderSymbol,
} from "./ConnectionFields";
import { MediaSettings } from "./MediaSettings";
import { useModelDraft } from "./ModelDraft";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Network, Save, PlugZap } from "lucide-react";
import {
  request,
  keys,
  rows,
  roleName,
  parseMoney,
  type RecordData,
} from "./api";
import {
  Panel,
  Heading,
  Field,
  Modal,
  ErrorNotice,
  Badge,
  Loading,
} from "./ui";
const split = (v: any) =>
  String(v || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
const h3Defaults = {
  preview: {
    steps: 4,
    shift_video: 12,
    shift_audio: 3,
    sampler: "euler",
    revision: "wkp-dualgpu-turbo4-v1",
  },
  production: {
    steps: 20,
    shift_video: 12,
    shift_audio: 3,
    sampler: "euler",
    revision: "wkp-dualgpu-base-v1",
  },
};
function h3Description(profile: RecordData) {
  return profile.quality === "production"
    ? `正式质量档 · 基础模型 · ${profile.steps ?? 20} 步 · ${profile.sampler || "euler"}`
    : "快速预览档 · Turbo LoRA · 4 步";
}
export function normalizeProfile(
  values: RecordData,
  previous: RecordData = {},
  video = false,
) {
  const provider = values.provider;
  const out: RecordData = {
    ...(previous.provider === provider ? previous : {}),
    provider,
    model: values.model.trim(),
    enabled: values.enabled === "on",
  };
  delete out.discovered_models;
  delete out.pending_key;
  delete out.id;
  if (video) {
    if (provider === "comfyui_h3" && values.quality) {
      const quality = values.quality as keyof typeof h3Defaults;
      const defaults = h3Defaults[quality];
      if (!defaults) throw new Error("请选择有效的 H3 质量档。");
      // Keeping an unchanged legacy preview profile preserves its cache identity.
      if (
        previous.quality ||
        quality !== "preview" ||
        previous.provider !== provider
      ) {
        Object.assign(out, defaults, {
          quality,
          steps: quality === "preview" ? 4 : Number(values.steps),
          shift_video: quality === "preview" ? 12 : Number(values.shift_video),
          shift_audio: quality === "preview" ? 3 : Number(values.shift_audio),
          sampler: quality === "preview" ? "euler" : values.sampler,
        });
      }
    }
    if (provider === "minimax")
      Object.assign(out, {
        network_mode: "remote",
        base_url: values.base_url.trim(),
        api_key_env: values.api_key_env.trim(),
        allowed_hosts: split(values.allowed_hosts),
        download_hosts: split(values.download_hosts),
        duration: Number(values.duration),
        resolution: values.resolution,
        poll_interval_seconds: Number(values.poll_interval_seconds),
        request_timeout_seconds: Number(values.request_timeout_seconds),
        max_wait_seconds: Number(values.max_wait_seconds),
        pricing: {
          unit_cost_micros: values.unit_cost?.trim()
            ? parseMoney(values.unit_cost)
            : null,
          currency: values.currency || "CNY",
          source: values.price_source || "",
        },
      });
    return out;
  }
  Object.assign(out, {
    ...endpointValues(values, previous),
    api_key_env: values.api_key_env?.trim() || "",
    context_tokens: Number(values.context_tokens),
    vision: values.vision === "on",
    request_timeout_seconds: Number(values.request_timeout_seconds),
    response_format: values.response_format,
    model_discovery: values.model_discovery,
    context_validation:
      values.context_validation ||
      (provider === "llama_cpp" ? "llama_tokenize" : "conservative_estimate"),
  });
  try {
    if (new URL(previous.base_url).origin !== new URL(out.base_url).origin)
      for (const key of [
        "tls_ca_file_env",
        "tls_client_cert_env",
        "tls_client_key_env",
      ])
        delete out[key];
  } catch {
    for (const key of [
      "tls_ca_file_env",
      "tls_client_cert_env",
      "tls_client_key_env",
    ])
      delete out[key];
  }
  if (values.thinking === "true" || values.thinking === "false")
    out.thinking = values.thinking === "true";
  else delete out.thinking;
  if (values.temperature_override?.trim())
    out.temperature_override = Number(values.temperature_override);
  else delete out.temperature_override;
  if (out.context_validation === "llama_tokenize" && provider !== "llama_cpp")
    out.context_validation = "conservative_estimate";
  if (["ollama_native", "dashscope"].includes(provider))
    delete out.chat_template_kwargs;
  if (!["ollama_native", "dashscope"].includes(provider)) delete out.thinking;
  return out;
}
function makeDraft(data: RecordData) {
  return {
    profiles: structuredClone(data.editable_profiles || {}),
    ...(data.image_profiles
      ? {
          image_profiles: structuredClone(data.image_profiles),
          image_roles: { ...data.image_roles },
        }
      : {}),
    ...(data.audio_profiles
      ? {
          audio_profiles: structuredClone(data.audio_profiles),
          audio_roles: { ...data.audio_roles },
        }
      : {}),
    roles: {
      ...Object.fromEntries(
        rows(data.roles).map((r) => [r.role, r.profile || ""]),
      ),
      ...data.role_assignments,
      ...(rows(data.roles).some((r) => r.role === "executive_producer") &&
      !data.role_assignments?.executive_producer
        ? { executive_producer: "" }
        : {}),
    },
    ...(data.video_profiles
      ? {
          video_profiles: structuredClone(data.video_profiles),
          video_roles: { ...data.video_roles },
        }
      : {}),
  };
}
export function Models({ notify }: { notify: (v: string) => void }) {
  const client = useQueryClient(),
    query = useQuery({
      queryKey: keys.models,
      queryFn: () => request("/models"),
      refetchInterval: 60000,
    });
  const {
    draft,
    base,
    dirty,
    saving,
    error,
    probes,
    probing,
    setupMode: savedSetupMode,
    store,
  } = useModelDraft(client);
  const [editing, setEditing] = useState<{ id: string; video: boolean } | null>(
    null,
  );
  const setupMode = savedSetupMode || "api";
  useEffect(() => {
    if (query.data) {
      if (!store.getSnapshot().setupMode) {
        const connections = [
          ...Object.values(query.data.editable_profiles || {}),
          ...Object.values(query.data.image_profiles || {}),
          ...Object.values(query.data.audio_profiles || {}),
          ...Object.values(query.data.video_profiles || {}),
        ] as RecordData[];
        const kinds = new Set(
          connections
            .filter((p) => p.enabled !== false)
            .map((p) => (p.network_mode === "remote" ? "api" : "local")),
        );
        store.update({
          setupMode:
            kinds.size > 1 ? "mixed" : kinds.has("local") ? "local" : "api",
        });
      }
      const current = store.getSnapshot();
      if (current.dirty || current.saving) return;
      const changed = current.base !== query.data.revision;
      store.update({
        draft: makeDraft(query.data),
        base: query.data.revision,
        ...(changed ? { generation: current.generation + 1, probes: {} } : {}),
      });
    }
  }, [query.data, dirty, saving, store]);
  function change(fn: (old: RecordData) => RecordData) {
    const current = store.getSnapshot();
    if (current.saving || !current.draft) return;
    store.update({
      draft: fn(current.draft),
      dirty: true,
      generation: current.generation + 1,
      probes: {},
    });
  }
  async function save() {
    const current = store.getSnapshot();
    if (!current.draft || current.saving || !current.dirty) return;
    const payload = structuredClone(current.draft);
    store.update({
      saving: true,
      error: "",
      generation: current.generation + 1,
      probes: {},
    });
    const savedGeneration = store.getSnapshot().generation;
    try {
      const roles = Object.fromEntries(
        Object.entries(payload.roles).filter(([, value]) => value !== ""),
      );
      const result = await request("/models", {
        ...payload,
        roles,
        expected_revision: current.base,
      });
      client.setQueryData(keys.models, result);
      if (store.getSnapshot().generation === savedGeneration)
        store.update({
          draft: makeDraft(result),
          base: result.revision,
          dirty: false,
        });
      notify("配置已保存，后续执行批次使用新的分工。");
    } catch (e) {
      store.update({ error: (e as Error).message });
    } finally {
      store.update({ saving: false });
    }
  }
  async function probe(
    id: string,
    video: boolean,
    mediaKind?: "image" | "audio" | "video",
  ) {
    const current = store.getSnapshot();
    if (current.dirty || current.saving || !!current.probing) return;
    const g = current.generation,
      key = (mediaKind ? mediaKind + ":" : video ? "video:" : "") + id;
    store.update({ probing: key });
    try {
      const result = await request("/models/test", {
        profile_id: id,
        ...(mediaKind ? { kind: mediaKind } : video ? { kind: "video" } : {}),
      });
      if (g === store.getSnapshot().generation)
        store.update({
          probes: { ...store.getSnapshot().probes, [key]: result },
        });
    } catch (e) {
      if (g === store.getSnapshot().generation)
        store.update({
          probes: {
            ...store.getSnapshot().probes,
            [key]: { status: "error", message: (e as Error).message },
          },
        });
    } finally {
      store.update({ probing: "" });
    }
  }
  const inventory = query.data;
  const providers = Object.fromEntries(
    rows(inventory?.providers).map((p) => [p.id, p.label]),
  );
  if (!draft || !inventory)
    return (
      <>
        <Heading eyebrow="CONNECTIONS" title="模型与连接" />
        <ErrorNotice error={query.error} />
        {query.error ? (
          <button className="button" onClick={() => query.refetch()}>
            重新读取模型配置
          </button>
        ) : (
          <Loading />
        )}
      </>
    );
  const profileCards = (video: boolean) =>
    Object.entries(video ? draft.video_profiles || {} : draft.profiles)
      .filter(
        ([, p]: [string, any]) =>
          !video || !["comfyui_local", "comfyui_external"].includes(p.provider),
      )
      .map(([id, p]: [string, any]) => {
        const result = probes[(video ? "video:" : "") + id];
        const observed = !video
          ? rows(inventory.profiles).find((entry) => entry.id === id)
          : null;
        const credentialMounted =
          observed?.api_key_credential === p.api_key_credential &&
          observed?.auth_configured === true;
        return (
          <article className="model-card" key={id}>
            <div className="row-between">
              <ProviderSymbol provider={p.provider} />
              <Badge
                status={
                  !p.enabled
                    ? "paused"
                    : dirty
                      ? "pending"
                      : result?.status || "unverified"
                }
                label={
                  !p.enabled
                    ? "已停用"
                    : dirty
                      ? "待保存"
                      : result?.status === "ready"
                        ? "连接通过"
                        : result?.status === "unverified"
                          ? "尚未验证连接"
                          : result
                            ? "查看检测结果"
                            : "尚未测试"
                }
              />
            </div>
            <h3>{id}</h3>
            <strong className="model-name">{p.model || "尚未填写模型"}</strong>
            <p>
              {providers[p.provider] || p.provider} ·{" "}
              {p.network_mode === "lan"
                ? "局域网服务"
                : p.network_mode === "remote"
                  ? "远程服务"
                  : "服务器管理"}
            </p>
            {!video && (
              <small className="protocol-label">
                {protocolName(p.provider)}
              </small>
            )}
            <code className="endpoint">
              {p.base_url || "服务器托管的生成工作流"}
            </code>
            {video && p.provider === "comfyui_h3" && (
              <small>{h3Description(p)}</small>
            )}
            {!video && p.tls_client_cert_env ? (
              <small>双向 TLS · 客户端身份由控制服务器管理</small>
            ) : !video && p.api_key_credential ? (
              <small>
                系统凭据：{p.api_key_credential} ·{" "}
                {credentialMounted ? "已挂载" : "尚未挂载或状态待同步"}
              </small>
            ) : p.api_key_env ? (
              <small>
                {p.api_key_env.startsWith("STUDIO_KEY_")
                  ? "Key 已保存在服务端"
                  : "已关联服务器凭据"}
              </small>
            ) : null}
            <div className="member-chips">
              {Object.entries(video ? draft.video_roles || {} : draft.roles)
                .filter(([, v]) => v === id)
                .map(([r]) => (
                  <span key={r}>{roleName(r)}</span>
                ))}
            </div>
            {result && (
              <div className="probe-result" role="status">
                {result.message ||
                  result.error?.message ||
                  "已完成配置检测；未运行生成任务"}
                {result.input_modalities && (
                  <span>
                    {" "}
                    · 服务声明的输入：{result.input_modalities.join("、")}
                    （未验证生成）
                  </span>
                )}
              </div>
            )}
            <div className="button-row">
              <button
                className="button"
                disabled={saving}
                data-model-action={video ? "video-edit" : "edit"}
                data-profile={id}
                onClick={() => setEditing({ id, video })}
              >
                编辑连接
              </button>
              <button
                className="button quiet"
                data-model-action={video ? "video-test" : "test"}
                data-profile={id}
                disabled={dirty || saving || !!probing || p.enabled === false}
                onClick={() => probe(id, video)}
              >
                {probing === (video ? "video:" : "") + id
                  ? "检测中…"
                  : video
                    ? "预检配置"
                    : "测试连接"}
              </button>
            </div>
          </article>
        );
      });
  const assignments = (video: boolean) =>
    Object.entries(video ? draft.video_roles || {} : draft.roles).map(
      ([r, value]) => {
        const meta =
          rows(video ? inventory.video_role_options : inventory.roles).find(
            (x) => x.role === r,
          ) || {};
        return (
          <Field label={meta.label || roleName(r)} key={r}>
            <select
              id={"role-" + (video ? "video-" : "") + r}
              value={String(value)}
              disabled={saving}
              onChange={(e) =>
                change((d) => ({
                  ...d,
                  [video ? "video_roles" : "roles"]: {
                    ...d[video ? "video_roles" : "roles"],
                    [r]: e.target.value,
                  },
                }))
              }
            >
              {r === "executive_producer" ? (
                <option value="">不启用（转人工）</option>
              ) : (
                <option value="" disabled>
                  选择连接
                </option>
              )}
              {Object.entries(
                video ? draft.video_profiles || {} : draft.profiles,
              ).map(([id, p]: [string, any]) => (
                <option
                  key={id}
                  value={id}
                  disabled={
                    p.enabled === false ||
                    (!video && meta.requires_vision && !p.vision)
                  }
                >
                  {id} · {p.model}
                  {!video && meta.requires_vision && !p.vision
                    ? "（不支持图像输入）"
                    : ""}
                </option>
              ))}
            </select>
            {!video && meta.requires_vision && (
              <small>
                视觉审核需要支持图片输入的模型；文本创作模型不能仅通过改名替代视觉服务。
              </small>
            )}
            {video &&
              draft.video_profiles?.[String(value)]?.provider ===
                "comfyui_h3" && (
                <small>
                  使用制作服务器的 ComfyUI / MiniMax H3 双 GPU
                  工作流，首帧图生视频；
                  {h3Description(draft.video_profiles[String(value)])}。
                </small>
              )}
          </Field>
        );
      },
    );
  return (
    <>
      <Heading
        eyebrow="MODELS & CONNECTIONS"
        title="模型与连接"
        description="配置创作、审核与媒体制作的模型。当前草稿保存后，供下一个执行批次使用。"
        action={
          <button
            className="button primary"
            disabled={saving}
            data-model-action="add"
            onClick={() => setEditing({ id: "", video: false })}
          >
            <Plus size={16} />
            添加连接
          </button>
        }
      />
      <ErrorNotice error={error || query.error} />
      {dirty && inventory.revision !== base && (
        <ErrorNotice error="其他终端已更新配置。你的草稿仍保留，请核对后重新载入。" />
      )}
      <div className="connection-note">
        <PlugZap size={20} />
        <p>
          模型、Qwen TTS 与视频服务在已配置的服务器运行。在这里选择服务、填写
          URL 与 Key；连接测试不会生成素材。
        </p>
      </div>
      <Panel
        title="接入向导"
        description="内置服务填 Key 即可开始；自定义服务填写 URL 与 Key，再选择实际模型。密钥仅保存在服务端。"
      >
        <Field label="使用方式">
          <select
            disabled={saving}
            value={setupMode}
            onChange={(e) => store.update({ setupMode: e.target.value })}
          >
            <option value="api">购买 API（无需 GPU）</option>
            <option value="local">本地模型</option>
            <option value="mixed">混合使用</option>
          </select>
        </Field>
        <p>
          选择引导方式后添加连接，再分配角色并保存；制作按下方分工执行。
          {setupMode !== "api" && "localhost 指运行 AI Studio 后端的机器。"}
        </p>
        <p role="status">
          尚未分配：
          {rows(inventory.roles)
            .filter((r) => !r.optional && !draft.roles[r.role])
            .map((r) => r.label || roleName(r.role))
            .join("、") || "文本角色已分配；请测试连接并检查制作能力"}
        </p>
        {(inventory.readiness?.missing || []).length > 0 && (
          <p role="status">{inventory.readiness.missing.join("；")}</p>
        )}
        <div className="button-row">
          <button
            className="button"
            disabled={saving}
            onClick={() => {
              const entries = Object.entries(draft.profiles) as [string, any][];
              const text = entries.find(([, p]) => p.enabled !== false);
              const vision = entries.find(
                ([, p]) => p.enabled !== false && p.vision,
              );
              change((d) => ({
                ...d,
                roles: {
                  ...d.roles,
                  ...Object.fromEntries(
                    rows(inventory.roles)
                      .filter((r) => !r.optional)
                      .map((r) => [
                        r.role,
                        (r.requires_vision ? vision : text)?.[0] || "",
                      ]),
                  ),
                },
              }));
            }}
          >
            应用基础角色分工
          </button>
        </div>
      </Panel>
      <Panel
        title="文本与视觉理解"
        description="同一连接可以承担多个角色，创作与独立审核分别记录。"
      >
        <div className="model-grid">{profileCards(false)}</div>
      </Panel>
      <Panel title="Agent 角色分工">
        <div className="role-assignment-grid">{assignments(false)}</div>
      </Panel>
      {draft.video_profiles && (
        <Panel
          title="视频模型"
          action={
            <button
              className="button"
              disabled={saving}
              data-model-action="video-add"
              onClick={() => setEditing({ id: "", video: true })}
            >
              <Plus size={16} />
              添加视频连接
            </button>
          }
        >
          <div className="model-grid">{profileCards(true)}</div>
          <div className="role-assignment-grid">{assignments(true)}</div>
        </Panel>
      )}
      <MediaSettings
        mode={setupMode}
        draft={draft}
        inventory={inventory}
        onChange={change}
        notify={notify}
        saving={saving}
        dirty={dirty}
        generation={store.getSnapshot().generation}
        probes={probes}
        probing={probing}
        onProbe={(kind, id) => probe(id, kind === "video", kind)}
      />
      {(dirty || saving) && (
        <div className="mobile-save-spacer" aria-hidden="true" />
      )}
      <div
        className={"config-savebar" + (dirty || saving ? " is-dirty" : "")}
        aria-busy={saving}
      >
        <div role="status">
          <strong className={error ? "error-text" : undefined}>
            {saving
              ? "正在保存配置…"
              : error
                ? "保存未完成，草稿已保留"
                : dirty
                  ? "有尚未保存的配置修改"
                  : "当前配置已同步"}
          </strong>
          <p>
            {dirty
              ? "保存前不会修改服务器的角色分工，切换页面将保留草稿。"
              : "新批次使用已保存配置，历史实际模型保持原记录。"}
          </p>
        </div>
        <div className="button-row">
          <button
            className="button"
            disabled={saving || query.isFetching}
            data-model-action="reload"
            onClick={async () => {
              if (
                store.getSnapshot().dirty &&
                !window.confirm("重新载入将放弃未保存的配置修改，是否继续？")
              )
                return;
              const g = store.getSnapshot().generation;
              const fresh = await query.refetch();
              if (
                fresh.data &&
                !fresh.error &&
                store.getSnapshot().generation === g
              ) {
                store.update({
                  dirty: false,
                  draft: makeDraft(fresh.data),
                  base: fresh.data.revision,
                  error: "",
                  generation: g + 1,
                  probes: {},
                });
              }
            }}
          >
            重新载入
          </button>
          <button
            className="button primary"
            disabled={!dirty || saving}
            data-model-action="save"
            onClick={save}
          >
            <Save size={16} />
            {saving ? "保存中…" : "保存配置"}
          </button>
        </div>
      </div>
      {editing && (
        <ProfileEditor
          key={(editing.video ? "v:" : "t:") + editing.id}
          id={editing.id}
          disabled={saving}
          video={editing.video}
          providerDefaults={inventory.presets || {}}
          videoDefaults={inventory.video_presets || {}}
          videoProviders={Object.fromEntries(
            rows(inventory.video_providers).map((p) => [p.id, p.label]),
          )}
          providers={providers}
          profile={
            (editing.video ? draft.video_profiles : draft.profiles)?.[
              editing.id
            ]
              ? {
                  ...(editing.video ? draft.video_profiles : draft.profiles)[
                    editing.id
                  ],
                  discovered_models: probes[editing.id]?.models || [],
                  pending_key:
                    draft.secrets?.[
                      (editing.video ? "video:" : "llm:") + editing.id
                    ],
                }
              : {}
          }
          onClose={() => setEditing(null)}
          onSave={(id, p, secret) => {
            if (store.getSnapshot().saving) return;
            const kind = editing.video ? "video_profiles" : "profiles";
            if (!editing.id && Object.hasOwn(draft[kind], id))
              throw new Error("连接编号已存在。");
            change((d) => ({
              ...d,
              [kind]: { ...d[kind], [id]: p },
              ...(secret !== undefined
                ? {
                    secrets: {
                      ...d.secrets,
                      [(editing.video ? "video:" : "llm:") + id]: secret,
                    },
                  }
                : {}),
            }));
            setEditing(null);
          }}
        />
      )}
    </>
  );
}
function ProfileEditor({
  disabled = false,
  id,
  profile,
  video,
  onClose,
  onSave,
  providerDefaults,
  providers,
  videoDefaults,
  videoProviders,
}: {
  disabled?: boolean;
  providerDefaults: RecordData;
  videoDefaults: RecordData;
  videoProviders: Record<string, string>;
  providers: Record<string, string>;
  id: string;
  profile: RecordData;
  video: boolean;
  onClose: () => void;
  onSave: (id: string, p: RecordData, secret?: string | null) => void;
}) {
  const [provider, setProvider] = useState(
      profile.provider || (video ? "minimax" : "deepseek"),
    ),
    [current, setCurrent] = useState<RecordData>({
      ...((profile.provider === "comfyui_h3" && !profile.quality
        ? {}
        : video
          ? videoDefaults[profile.provider || "minimax"]
          : providerDefaults[profile.provider || "deepseek"]) || {}),
      enabled: true,
      ...profile,
    }),
    [h3Quality, setH3Quality] = useState<keyof typeof h3Defaults>(
      profile.quality ||
        (profile.provider === "comfyui_h3" ? "preview" : "production"),
    ),
    [error, setError] = useState("");
  const [custom, setCustom] = useState(!builtinProviders.has(provider));
  const [editAddress, setEditAddress] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryMessage, setDiscoveryMessage] = useState("");
  const [modelOptions, setModelOptions] = useState<string[]>(
    profile.discovered_models || [],
  );
  const autoId = useRef(
    id || `connection-${Math.random().toString(36).slice(2, 9)}`,
  );
  const form = useRef<HTMLFormElement>(null),
    cloud = video && provider === "minimax";
  const field = (name: string, label: string, options: RecordData = {}) => (
    <Field label={label} hint={options.hint}>
      <input
        name={name}
        list={name === "model" ? "provider-models" : undefined}
        type={options.type || "text"}
        step={options.step}
        defaultValue={options.value ?? current[name] ?? options.fallback ?? ""}
        required={options.required}
        min={options.min}
        max={options.max}
        pattern={options.pattern}
        readOnly={options.readOnly}
      />
    </Field>
  );
  function switchProvider(next: string) {
    const defaults = video ? videoDefaults[next] : providerDefaults[next];
    const enteredId = form.current ? new FormData(form.current).get("id") : id;
    setCurrent({
      ...defaults,
      id: enteredId,
      model: defaults?.model || current.model || "",
      enabled: true,
    });
    setProvider(next);
    setEditAddress(false);
    setModelOptions([]);
    setDiscoveryMessage("");
    setError("");
    setH3Quality(defaults?.quality || "production");
  }

  return (
    <Modal
      id="model-dialog"
      title={video ? "视频模型连接" : id ? "编辑模型连接" : "添加模型连接"}
      onClose={onClose}
    >
      <form
        ref={form}
        key={provider}
        data-model-form={video ? "video" : "profile"}
        onSubmit={(e) => {
          e.preventDefault();
          try {
            const values = Object.fromEntries(new FormData(e.currentTarget));
            const next = normalizeProfile(
              { ...values, provider },
              current,
              video,
            );
            onSave(
              String(values.id),
              next,
              values.api_key ? String(values.api_key).trim() : undefined,
            );
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <fieldset
          disabled={disabled || discovering}
          className="model-editor-fields"
        >
          <ErrorNotice error={error} />
          <datalist id="provider-models">
            {[...new Set([current.model, ...modelOptions])]
              .filter(Boolean)
              .map((m: string) => (
                <option key={m} value={m} />
              ))}
          </datalist>
          {!video && (
            <div className="connection-mode" role="group" aria-label="接入类型">
              <button
                type="button"
                className={custom ? "button" : "button primary"}
                onClick={() => {
                  setCustom(false);
                  switchProvider("deepseek");
                }}
              >
                内置服务
              </button>
              <button
                type="button"
                className={custom ? "button primary" : "button"}
                onClick={() => {
                  setCustom(true);
                  switchProvider("openai_compatible");
                }}
              >
                自定义 / 本地
              </button>
            </div>
          )}
          <div className="form-row">
            {field("id", "连接编号", {
              value: current.id ?? autoId.current,
              required: true,
              readOnly: !!id,
              pattern: "[A-Za-z0-9][A-Za-z0-9_.-]{0,63}",
            })}
            <Field label="服务类型">
              <select
                name="provider"
                value={provider}
                onChange={(e) => switchProvider(e.target.value)}
              >
                {Object.entries(video ? videoProviders : providers)
                  .filter(
                    ([v]) =>
                      video ||
                      (custom
                        ? !builtinProviders.has(v) ||
                          ["anthropic", "gemini"].includes(v)
                        : builtinProviders.has(v)),
                  )
                  .map(([v, label]) => (
                    <option key={v} value={v}>
                      {label}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
          {!video && (
            <div className="provider-intro">
              <ProviderSymbol provider={provider} />
              <div>
                <strong>{providers[provider] || provider}</strong>
                <small>{protocolName(provider)}</small>
              </div>
              {providerLinks[provider] && (
                <div className="provider-links">
                  <a
                    href={providerLinks[provider].docs}
                    target="_blank"
                    rel="noreferrer"
                  >
                    官方文档
                  </a>
                  <a
                    href={providerLinks[provider].keys}
                    target="_blank"
                    rel="noreferrer"
                  >
                    获取 Key
                  </a>
                </div>
              )}
            </div>
          )}
          {field("model", "模型标识", { required: true })}
          {video && provider === "comfyui_h3" && (
            <>
              <Field label="H3 质量档">
                <select
                  name="quality"
                  value={h3Quality}
                  onChange={(e) =>
                    setH3Quality(e.target.value as keyof typeof h3Defaults)
                  }
                >
                  <option value="production">正式质量 · 基础模型</option>
                  <option value="preview">快速预览 · 4 步 Turbo LoRA</option>
                </select>
              </Field>
              <p>
                {h3Quality === "production"
                  ? "使用基础模型，不加载预览加速 LoRA。增加采样步数会延长制作时间；人物与动作效果仍需逐镜审核。"
                  : "使用 4 步 Turbo LoRA，适合快速检查镜头与提示词。"}
              </p>
              {h3Quality === "production" && (
                <div key={h3Quality}>
                  <div className="form-row">
                    {field("steps", "采样步数", {
                      type: "number",
                      min: 20,
                      max: 50,
                      step: 1,
                      required: true,
                      value:
                        current.quality === "production"
                          ? (current.steps ?? 20)
                          : 20,
                    })}
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
                  </div>
                  <div className="form-row">
                    {field("shift_video", "视频采样偏移", {
                      type: "number",
                      min: 1,
                      max: 20,
                      step: "any",
                      required: true,
                      value:
                        current.quality === "production"
                          ? (current.shift_video ?? 12)
                          : 12,
                    })}
                    {field("shift_audio", "音频采样偏移", {
                      type: "number",
                      min: 1,
                      max: 10,
                      step: "any",
                      required: true,
                      value:
                        current.quality === "production"
                          ? (current.shift_audio ?? 3)
                          : 3,
                    })}
                  </div>
                </div>
              )}
              <small>
                输出尺寸和镜头时长沿用制作配置；此处选择服务已支持的模型工作流和采样参数。连接预检不会生成视频。
              </small>
            </>
          )}
          {(!video || cloud) && (
            <>
              {!video && !custom && !editAddress ? (
                <Field label="接口地址">
                  <input
                    name="base_url"
                    type="hidden"
                    value={current.base_url || ""}
                  />
                  <div className="builtin-endpoint">
                    <code>{current.base_url}</code>
                    <button
                      type="button"
                      className="button quiet"
                      onClick={() => setEditAddress(true)}
                    >
                      自定义地址
                    </button>
                  </div>
                </Field>
              ) : (
                field("base_url", "接口地址", {
                  type: "url",
                  required: true,
                  hint: "服务地址或完整调用地址均可；保留自定义代理前缀。",
                })
              )}
              <CredentialField
                saved={
                  !!id && !!(current.api_key_env || current.api_key_credential)
                }
                value={
                  provider === profile.provider ? profile.pending_key || "" : ""
                }
              />
              {!video && (
                <button
                  type="button"
                  className="button"
                  disabled={discovering}
                  onClick={async () => {
                    if (!form.current) return;
                    setDiscovering(true);
                    setError("");
                    try {
                      const raw = Object.fromEntries(
                        new FormData(form.current),
                      );
                      const p = normalizeProfile(
                        {
                          ...raw,
                          model: raw.model || "__discover__",
                          provider,
                        },
                        current,
                      );
                      const result = await request("/models/discover", {
                        profile: p,
                        ...(raw.api_key
                          ? { api_key: String(raw.api_key).trim() }
                          : {}),
                      });
                      const names = rows(result.models).map((m) => m.id);
                      setModelOptions(names);
                      const input = form.current.elements.namedItem(
                        "model",
                      ) as HTMLInputElement;
                      if (!input.value && names.length) input.value = names[0];
                      setDiscoveryMessage(
                        names.length
                          ? `已读取 ${names.length} 个模型。${names.includes(input.value) ? "可在模型标识中选择。" : "当前标识不在目录中，请核对或从目录选择。"}尚未验证生成能力。`
                          : "",
                      );
                      if (!names.length)
                        setError("服务返回空模型目录，可填写实际模型标识。");
                    } catch (error) {
                      setError((error as Error).message);
                    } finally {
                      setDiscovering(false);
                    }
                  }}
                >
                  {discovering ? "读取中…" : "读取可用模型"}
                </button>
              )}
              {discoveryMessage && (
                <small className="connection-discovery" role="status">
                  {discoveryMessage}
                </small>
              )}
              <details className="connection-advanced">
                <summary>高级连接设置</summary>
                <div className="form-row">
                  {!video && (
                    <Field label="连接范围">
                      <select
                        name="network_mode"
                        defaultValue={
                          id ? current.network_mode || "auto" : "auto"
                        }
                      >
                        <option value="auto">根据 URL 自动判断</option>
                        <option value="loopback">服务器本机</option>
                        <option value="lan">局域网服务</option>
                        <option value="remote">远程 HTTPS</option>
                      </select>
                    </Field>
                  )}
                  {field("allowed_hosts", "允许访问的主机名", {
                    value: Array.isArray(current.allowed_hosts)
                      ? current.allowed_hosts.join(", ")
                      : current.allowed_hosts,
                    hint: "多个精确 hostname 用逗号分隔。",
                  })}
                </div>
                {field("api_key_env", "密钥环境变量名称", {
                  pattern: "[A-Za-z_][A-Za-z0-9_]*",
                  hint: current.api_key_credential
                    ? "此连接已有 systemd 凭据关联，不能同时使用密钥环境变量。"
                    : "只登记变量名称，真实密钥由服务端读取。",
                })}
                {current.api_key_credential && (
                  <small>保留系统凭据关联：{current.api_key_credential}</small>
                )}
                {field("request_timeout_seconds", "请求超时 / 秒", {
                  type: "number",
                  fallback: video ? 60 : 120,
                  min: 1,
                  max: 600,
                  required: true,
                })}
              </details>
            </>
          )}
          {!video && (
            <details className="connection-advanced">
              <summary>模型能力与推理设置</summary>
              <div className="form-row">
                {field("context_tokens", "上下文 Token 上限", {
                  type: "number",
                  fallback: 32768,
                  min: 1024,
                  max: 2000000,
                  required: true,
                })}
                <Field label="结构化输出">
                  <select
                    name="response_format"
                    defaultValue={current.response_format || "json_object"}
                  >
                    {[
                      "json_object",
                      "openai_json_schema",
                      "llama_json_schema",
                      "prompt",
                    ].map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <div className="form-row">
                <Field label="模型目录检查">
                  <select
                    name="model_discovery"
                    defaultValue={current.model_discovery || "required"}
                  >
                    <option value="required">检查已登记模型</option>
                    <option
                      value="skip"
                      disabled={["llama_cpp", "ollama_native"].includes(
                        provider,
                      )}
                    >
                      跳过（连接未验证）
                    </option>
                  </select>
                </Field>
                <Field label="上下文验证">
                  <select
                    name="context_validation"
                    defaultValue={
                      current.context_validation || "conservative_estimate"
                    }
                  >
                    <option value="conservative_estimate">保守估算</option>
                    <option
                      value="llama_tokenize"
                      disabled={provider !== "llama_cpp"}
                    >
                      llama.cpp tokenize
                    </option>
                  </select>
                </Field>
              </div>
              <details>
                <summary>推理参数</summary>
                <div className="form-row">
                  {field("temperature_override", "温度覆盖", {
                    type: "number",
                    min: 0,
                    max: 2,
                    step: "any",
                  })}
                  <Field label="Thinking">
                    <select
                      name="thinking"
                      defaultValue={
                        current.thinking === true
                          ? "true"
                          : current.thinking === false
                            ? "false"
                            : current.thinking || ""
                      }
                      disabled={
                        !["dashscope", "ollama_native"].includes(provider)
                      }
                    >
                      <option value="">服务默认</option>
                      <option value="true">启用</option>
                      <option value="false">关闭</option>
                    </select>
                  </Field>
                </div>
              </details>
              <label className="check">
                <input
                  type="checkbox"
                  name="vision"
                  defaultChecked={current.vision}
                />
                支持图像输入
              </label>
            </details>
          )}
          {cloud && (
            <>
              <div className="form-row">
                {field("download_hosts", "允许下载的主机名", {
                  value: Array.isArray(current.download_hosts)
                    ? current.download_hosts.join(", ")
                    : current.download_hosts,
                })}
                <Field label="单次视频时长">
                  <select name="duration" defaultValue={current.duration || 6}>
                    <option value="6">6 秒</option>
                    <option value="10">10 秒</option>
                  </select>
                </Field>
              </div>
              <div className="form-row">
                <Field label="分辨率">
                  <select
                    name="resolution"
                    defaultValue={current.resolution || "768P"}
                  >
                    {["512P", "768P", "1080P"].map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </select>
                </Field>
                {field("poll_interval_seconds", "查询间隔 / 秒", {
                  type: "number",
                  fallback: 5,
                  min: 1,
                  max: 60,
                  required: true,
                })}
              </div>
              {field("max_wait_seconds", "任务等待上限 / 秒", {
                type: "number",
                fallback: 1800,
                min: 1,
                max: 7200,
                required: true,
              })}
              <div className="form-row">
                {field("unit_cost", "单次估价", {
                  value:
                    current.pricing?.unit_cost_micros == null
                      ? ""
                      : String(current.pricing.unit_cost_micros / 1e6),
                  hint: "未知时留空，项目逐批授权。",
                })}
                {field("currency", "币种", {
                  value: current.pricing?.currency || "CNY",
                  pattern: "[A-Z]{3}",
                })}
              </div>
              {field("price_source", "计价依据", {
                value: current.pricing?.source || "",
              })}
            </>
          )}
          <label className="check">
            <input
              type="checkbox"
              name="enabled"
              defaultChecked={current.enabled !== false}
            />
            启用此连接
          </label>
          <div className="dialog-actions">
            <button type="button" className="button" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="button primary">
              加入待保存修改
            </button>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
