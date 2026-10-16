import { MediaSettings } from "./MediaSettings";
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
  delete out.id;
  if (video) {
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
    base_url: values.base_url.trim(),
    network_mode: values.network_mode,
    allowed_hosts: split(values.allowed_hosts),
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
  const [draft, setDraft] = useState<RecordData | null>(null),
    [base, setBase] = useState(""),
    [dirty, setDirty] = useState(false),
    [saving, setSaving] = useState(false),
    [error, setError] = useState(""),
    [editing, setEditing] = useState<{ id: string; video: boolean } | null>(
      null,
    ),
    [probes, setProbes] = useState<RecordData>({}),
    [probing, setProbing] = useState("");
  const [setupMode, setSetupMode] = useState("api");
  const setupInitialized = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    if (query.data && !dirty) {
      if (!setupInitialized.current) {
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
        setSetupMode(
          kinds.size > 1 ? "mixed" : kinds.has("local") ? "local" : "api",
        );
        setupInitialized.current = true;
      }
      setDraft(makeDraft(query.data));
      setBase(query.data.revision);
      setProbes({});
    }
  }, [query.data, dirty]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  function change(fn: (old: RecordData) => RecordData) {
    generation.current++;
    setDraft((old) => fn(old!));
    setDirty(true);
    setProbes({});
  }
  async function save() {
    if (!draft) return;
    setSaving(true);
    setError("");
    try {
      const roles = Object.fromEntries(
        Object.entries(draft.roles).filter(([, value]) => value !== ""),
      );
      if (roles.executive_producer === "") delete roles.executive_producer;
      const result = await request("/models", {
        ...draft,
        roles,
        expected_revision: base,
      });
      client.setQueryData(keys.models, result);
      setDraft(makeDraft(result));
      setBase(result.revision);
      setDirty(false);
      notify("配置已保存，后续执行批次使用新的分工。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  async function probe(id: string, video: boolean) {
    if (dirty || probing) return;
    const g = generation.current,
      key = (video ? "video:" : "") + id;
    setProbing(key);
    try {
      const result = await request("/models/test", {
        profile_id: id,
        ...(video ? { kind: "video" } : {}),
      });
      if (g === generation.current)
        setProbes((old) => ({ ...old, [key]: result }));
    } catch (e) {
      if (g === generation.current)
        setProbes((old) => ({
          ...old,
          [key]: { status: "error", message: (e as Error).message },
        }));
    } finally {
      setProbing("");
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
        <Loading />
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
              <div className="model-symbol">
                <Network size={22} />
              </div>
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
            <code className="endpoint">
              {p.base_url || "服务器托管的生成工作流"}
            </code>
            {!video && p.tls_client_cert_env ? (
              <small>双向 TLS · 客户端身份由控制服务器管理</small>
            ) : !video && p.api_key_credential ? (
              <small>
                系统凭据：{p.api_key_credential} ·{" "}
                {credentialMounted ? "已挂载" : "尚未挂载或状态待同步"}
              </small>
            ) : p.api_key_env ? (
              <small>密钥变量：{p.api_key_env}</small>
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
              </div>
            )}
            <div className="button-row">
              <button
                className="button"
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
          模型、Qwen TTS
          与视频服务在已配置的服务器运行。这里登记接口和环境变量名称；连接测试不会生成素材。
        </p>
      </div>
      <Panel
        title="接入向导"
        description="连接使用你自己的模型或购买的 API。密钥保存在 config/runtime.env，修改后重启 Web 和 worker。"
      >
        <Field label="使用方式">
          <select
            value={setupMode}
            onChange={(e) => setSetupMode(e.target.value)}
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
      <div className="config-savebar">
        <div>
          <strong>{dirty ? "有尚未保存的配置修改" : "当前配置已同步"}</strong>
          <p>
            {dirty
              ? "保存前不会修改服务器的角色分工。"
              : "新批次使用已保存配置，历史实际模型保持原记录。"}
          </p>
        </div>
        <div className="button-row">
          <button
            className="button"
            disabled={saving}
            data-model-action="reload"
            onClick={async () => {
              const fresh = await query.refetch();
              if (fresh.data) {
                generation.current++;
                setDirty(false);
                setDraft(makeDraft(fresh.data));
                setBase(fresh.data.revision);
                setError("");
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
      <MediaSettings
        mode={setupMode}
        draft={draft}
        inventory={inventory}
        onChange={change}
        notify={notify}
      />
      {editing && (
        <ProfileEditor
          key={(editing.video ? "v:" : "t:") + editing.id}
          id={editing.id}
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
                }
              : {}
          }
          onClose={() => setEditing(null)}
          onSave={(id, p) => {
            const kind = editing.video ? "video_profiles" : "profiles";
            if (!editing.id && Object.hasOwn(draft[kind], id))
              throw new Error("连接编号已存在。");
            change((d) => ({ ...d, [kind]: { ...d[kind], [id]: p } }));
            setEditing(null);
          }}
        />
      )}
    </>
  );
}
function ProfileEditor({
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
  providerDefaults: RecordData;
  videoDefaults: RecordData;
  videoProviders: Record<string, string>;
  providers: Record<string, string>;
  id: string;
  profile: RecordData;
  video: boolean;
  onClose: () => void;
  onSave: (id: string, p: RecordData) => void;
}) {
  const [provider, setProvider] = useState(
      profile.provider || (video ? "minimax" : "deepseek"),
    ),
    [current, setCurrent] = useState<RecordData>({
      ...((video
        ? videoDefaults[profile.provider || "minimax"]
        : providerDefaults[profile.provider || "deepseek"]) || {}),
      enabled: true,
      ...profile,
    }),
    [error, setError] = useState("");
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
            onSave(String(values.id), next);
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <ErrorNotice error={error} />
        <datalist id="provider-models">
          {[current.model, ...(profile.discovered_models || [])]
            .filter(Boolean)
            .map((m: string) => (
              <option key={m} value={m} />
            ))}
        </datalist>
        <div className="form-row">
          {field("id", "连接编号", {
            value: current.id ?? id,
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
              {Object.entries(video ? videoProviders : providers).map(
                ([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ),
              )}
            </select>
          </Field>
        </div>
        {field("model", "模型标识", { required: true })}
        {(!video || cloud) && (
          <>
            {field("base_url", "接口地址", {
              type: "url",
              required: true,
              hint: "完整服务前缀；保留 /v1 或自定义代理路径。",
            })}
            <div className="form-row">
              {!video && (
                <Field label="连接范围">
                  <select
                    name="network_mode"
                    defaultValue={current.network_mode || "loopback"}
                  >
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
          </>
        )}
        {!video && (
          <>
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
                    disabled={["llama_cpp", "ollama_native"].includes(provider)}
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
          </>
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
      </form>
    </Modal>
  );
}
