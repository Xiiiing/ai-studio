import { StudioSelect } from "./StudioSelect";
import { ServiceCategories } from "./ServiceCategories";
import { useRef, useState } from "react";
import { Check, Plus, Search, ArrowLeft, Trash2 } from "lucide-react";
import {
  builtinProviders,
  CredentialField,
  providerLinks,
  protocolName,
  ProviderSymbol,
} from "./ConnectionFields";
import {
  normalizeProfile,
  makeDraft,
  newProfileId,
  modelUses,
  providerLabel,
  type Service,
  type ModelKind,
} from "./ModelConfig";
import { request, rows, type RecordData } from "./api";
import { Modal, Field, ErrorNotice } from "./ui";

type Entry = { id: string; profile: RecordData };
export function ServiceEditor({
  inventory,
  service,
  onClose,
  onKindChange,
  onSave,
}: {
  inventory: RecordData;
  service?: Service;
  onClose: () => void;
  onKindChange?: (kind: ModelKind) => void;
  onSave: (
    draft: RecordData,
    revision: string,
    selected: string,
  ) => Promise<void>;
}) {
  const original = service?.profiles[0]?.[1];
  const [provider, setProvider] = useState(original?.provider || "");
  const [entries, setEntries] = useState<Entry[]>(
    service?.profiles.map(([id, profile]) => ({
      id,
      profile: { ...profile },
    })) || [],
  );
  const [catalog, setCatalog] = useState<RecordData[]>([]);
  const [search, setSearch] = useState("");
  const [manual, setManual] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [pickerSearch, setPickerSearch] = useState("");
  const form = useRef<HTMLFormElement>(null);
  const epoch = useRef(0);
  const pending = useRef(false);
  const snapshot = useRef(makeDraft(inventory)).current;
  const revision = useRef(inventory.revision).current;
  const defaults: RecordData = {
    provider,
    enabled: true,
    context_tokens: 32768,
    request_timeout_seconds: 120,
    response_format: "json_object",
    model_discovery: "required",
    ...inventory.presets?.[provider],
    ...original,
  };
  const providerName = providerLabel(inventory, provider);
  const cloud = builtinProviders.has(provider);
  function choose(next: string) {
    setProvider(next);
    setError("");
    setMessage("");
    setCatalog([]);
    const preset = inventory.presets?.[next];
    setEntries(
      preset?.model
        ? [{ id: newProfileId(snapshot.profiles), profile: { ...preset } }]
        : [],
    );
  }
  function readConnection(model = "__discover__") {
    const values = Object.fromEntries(new FormData(form.current!));
    return {
      values,
      profile: normalizeProfile(
        {
          ...defaults,
          ...values,
          provider,
          model,
          enabled: "on",
          vision: defaults.vision ? "on" : "",
          network_mode: values.network_mode || "auto",
          allowed_hosts: values.allowed_hosts || "",
        },
        defaults,
      ),
    };
  }
  function add(model: string, meta?: RecordData) {
    model = model.trim();
    if (!model || entries.some((e) => e.profile.model === model)) return;
    const base = {
      ...defaults,
      model,
      vision: meta?.architecture?.input_modalities?.includes("image") === true,
    };
    setEntries((old) => [
      ...old,
      {
        id: newProfileId({
          ...snapshot.profiles,
          ...Object.fromEntries(old.map((e) => [e.id, e.profile])),
        }),
        profile: base,
      },
    ]);
    setManual("");
  }
  function remove(entry: Entry) {
    if (modelUses(snapshot, "llm", entry.id).length) {
      setError("此模型正在用于制作分工，请先在「制作分工」更换模型。");
      return;
    }
    setEntries((old) => old.filter((e) => e.id !== entry.id));
  }
  async function discover() {
    if (pending.current) return;
    pending.current = true;
    setBusy("discover");
    setError("");
    setMessage("");
    const started = epoch.current;
    try {
      const { values, profile } = readConnection();
      const response = await request("/models/discover", {
        profile,
        ...(values.api_key ? { api_key: String(values.api_key).trim() } : {}),
      });
      if (started !== epoch.current) return;
      const models = rows(response.models).filter(
        (m) => typeof m.id === "string" && m.id.trim(),
      );
      setCatalog(models);
      setMessage(
        models.length
          ? `读取到 ${models.length} 个模型，勾选需要使用的模型。目录读取不验证生成能力。`
          : "目录为空，可手动添加模型 ID。",
      );
    } catch (e) {
      if (started === epoch.current)
        setError(`${(e as Error).message}。可继续手动添加模型 ID。`);
    } finally {
      pending.current = false;
      setBusy("");
    }
  }
  async function save() {
    if (pending.current) return;
    setError("");
    if (!entries.length) {
      setError("请至少添加一个模型。");
      return;
    }
    pending.current = true;
    setBusy("save");
    try {
      const { values, profile: common } = readConnection();
      const draft = structuredClone(snapshot);
      for (const [id] of service?.profiles || []) delete draft.profiles[id];
      const secrets: RecordData = {};
      for (const entry of entries) {
        if (
          entry.profile.enabled === false &&
          modelUses(snapshot, "llm", entry.id).length
        )
          throw new Error("正在使用的模型不能停用，请先调整制作分工。");
        const old = {
          ...entry.profile,
          model: String(entry.profile.model).trim(),
        };
        if (!old.model) throw new Error("请填写模型 ID。");
        const next: RecordData = { ...old };
        for (const field of [
          "provider",
          "base_url",
          "network_mode",
          "allowed_hosts",
          "api_key_env",
          "api_key_credential",
          "tls_ca_file_env",
          "tls_client_cert_env",
          "tls_client_key_env",
          "request_timeout_seconds",
        ]) {
          // An unchanged service timeout must not overwrite a model's custom
          // timeout. Explicitly changing the connection timeout applies to all.
          if (
            field === "request_timeout_seconds" &&
            String(values[field]) === String(defaults[field])
          )
            continue;
          if (common[field] !== undefined) next[field] = common[field];
          else delete next[field];
        }
        // First-class IDs stay stable so existing projects/roles keep their model.
        draft.profiles[entry.id] = next;
        if (values.api_key)
          secrets["llm:" + entry.id] = String(values.api_key).trim();
      }
      if (Object.keys(secrets).length) draft.secrets = secrets;
      await onSave(draft, revision, "llm:" + entries[0].id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy("");
    }
  }
  async function removeService() {
    if (!service || pending.current) return;
    pending.current = true;
    setBusy("remove");
    setError("");
    try {
      const draft = structuredClone(snapshot);
      if (
        service.profiles.some(([id]) => modelUses(snapshot, "llm", id).length)
      )
        throw new Error("请先在制作分工中更换该服务使用的模型。");
      for (const [id] of service.profiles) delete draft.profiles[id];
      await onSave(draft, revision, "removed-service");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy("");
    }
  }
  const serviceInUse = service?.profiles.some(
    ([id]) => modelUses(snapshot, "llm", id).length,
  );
  const providerOptions = rows(inventory.providers);
  return (
    <Modal
      id="service-dialog"
      title={provider ? (service ? "管理服务" : "连接服务") : "添加服务"}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      {!provider ? (
        <div className="service-picker">
          {onKindChange && (
            <ServiceCategories kind={"llm"} onChange={onKindChange} />
          )}
          <p>选择服务商，或连接你已部署的模型服务。</p>
          <Field label="搜索服务商">
            <input
              type="search"
              value={pickerSearch}
              onChange={(e) => setPickerSearch(e.target.value)}
              placeholder="DeepSeek、千问、Ollama…"
            />
          </Field>
          {[
            ["云端服务", true],
            ["本地与自定义", false],
          ].map(([title, isCloud]) => (
            <section key={String(title)}>
              <h3>{title}</h3>
              <div className="provider-picker-grid">
                {providerOptions
                  .filter(
                    (p) =>
                      builtinProviders.has(p.id) === isCloud &&
                      `${p.id} ${p.label}`
                        .toLowerCase()
                        .includes(pickerSearch.toLowerCase()),
                  )
                  .map((p) => (
                    <button
                      type="button"
                      className="provider-pick"
                      key={p.id}
                      onClick={() => choose(p.id)}
                    >
                      <ProviderSymbol provider={p.id} />
                      <span>{p.label}</span>
                      <Plus size={15} />
                    </button>
                  ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <form
          ref={form}
          className="service-form"
          onChange={(e) => {
            if (
              [
                "base_url",
                "api_key",
                "api_key_env",
                "network_mode",
                "allowed_hosts",
              ].includes((e.target as unknown as HTMLInputElement).name)
            ) {
              epoch.current++;
              setCatalog([]);
              setMessage("");
            }
          }}
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <fieldset disabled={!!busy} className="model-editor-fields">
            <div className="service-editor-brand">
              <ProviderSymbol provider={provider} />
              <div>
                <strong>{providerName}</strong>
                <small>{protocolName(provider)}</small>
              </div>
              {!service && (
                <button
                  type="button"
                  className="button quiet"
                  onClick={() => {
                    setProvider("");
                    epoch.current++;
                  }}
                >
                  <ArrowLeft size={14} />
                  重选
                </button>
              )}
            </div>
            <section className="service-form-section">
              <h3>
                <span>1</span> 连接凭据
              </h3>
              {!cloud && (
                <Field
                  label="服务 URL"
                  hint="填写模型服务器地址；localhost 指 AI Studio 后端所在机器。"
                >
                  <input
                    name="base_url"
                    type="url"
                    defaultValue={defaults.base_url || ""}
                    required
                    placeholder="http://192.168.1.10:8000/v1"
                  />
                </Field>
              )}
              <CredentialField
                saved={
                  !!(original?.api_key_env || original?.api_key_credential)
                }
              />
              {providerLinks[provider] && (
                <div className="service-links">
                  <a
                    href={providerLinks[provider].keys}
                    target="_blank"
                    rel="noreferrer"
                  >
                    获取 API Key ↗
                  </a>
                  <a
                    href={providerLinks[provider].docs}
                    target="_blank"
                    rel="noreferrer"
                  >
                    官方文档 ↗
                  </a>
                </div>
              )}
              <details className="service-advanced">
                <summary>高级连接设置{cloud ? " · 自定义地址" : ""}</summary>
                {cloud && (
                  <Field label="服务 URL">
                    <input
                      name="base_url"
                      type="url"
                      required
                      defaultValue={defaults.base_url || ""}
                    />
                  </Field>
                )}
                <div className="form-row">
                  <Field label="连接范围">
                    <StudioSelect
                      name="network_mode"
                      defaultValue={
                        service ? defaults.network_mode || "auto" : "auto"
                      }
                    >
                      <option value="auto">按 URL 自动判断</option>
                      <option value="loopback">服务器本机</option>
                      <option value="lan">局域网</option>
                      <option value="remote">远程 HTTPS</option>
                    </StudioSelect>
                  </Field>
                  <Field label="请求超时 / 秒">
                    <input
                      name="request_timeout_seconds"
                      type="number"
                      min="1"
                      max="600"
                      defaultValue={defaults.request_timeout_seconds || 120}
                      required
                    />
                  </Field>
                </div>
                <Field
                  label="额外允许主机"
                  hint="逗号分隔；URL 的主机自动加入。"
                >
                  <input
                    name="allowed_hosts"
                    defaultValue={(defaults.allowed_hosts || []).join(", ")}
                  />
                </Field>
                <Field
                  label="服务端密钥变量"
                  hint="已有系统凭据可保留；直接填写 Key 时自动使用私有存储。"
                >
                  <input
                    name="api_key_env"
                    defaultValue={defaults.api_key_env || ""}
                    pattern="[A-Za-z_][A-Za-z0-9_]*"
                  />
                </Field>
                {original?.api_key_credential && (
                  <small>
                    系统凭据：{original.api_key_credential}（保留关联）
                  </small>
                )}
                {original?.tls_client_cert_env && (
                  <small>
                    已关联双向 TLS 身份；更换服务来源时不沿用此身份。
                  </small>
                )}
                {service && (
                  <div className="remove-service">
                    <button
                      type="button"
                      className="button quiet"
                      disabled={
                        !!busy ||
                        serviceInUse ||
                        (!inventory.setup_incomplete &&
                          Object.keys(snapshot.profiles).length ===
                            service.profiles.length)
                      }
                      onClick={removeService}
                    >
                      移除服务
                    </button>
                    <small>
                      {serviceInUse
                        ? "请先更换该服务的制作分工"
                        : "仅可移除未分配任务的服务"}
                    </small>
                  </div>
                )}
              </details>
            </section>
            <section className="service-form-section">
              <div className="row-between">
                <h3>
                  <span>2</span> 选择模型
                </h3>
                <button
                  className="button"
                  type="button"
                  onClick={discover}
                  disabled={!!busy}
                >
                  {busy === "discover" ? "读取中…" : "获取模型"}
                </button>
              </div>
              {message && (
                <p className="service-discovery-message" role="status">
                  {message}
                </p>
              )}
              {catalog.length > 0 && (
                <div className="service-catalog">
                  <Field label="搜索模型">
                    <div className="service-search">
                      <Search size={15} />
                      <input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="按模型 ID 搜索"
                        type="search"
                      />
                    </div>
                  </Field>
                  <div className="catalog-options">
                    {catalog
                      .filter((m) =>
                        m.id.toLowerCase().includes(search.toLowerCase()),
                      )
                      .map((m) => {
                        const entry = entries.find(
                          (e) => e.profile.model === m.id,
                        );
                        return (
                          <label className="catalog-option" key={m.id}>
                            <input
                              type="checkbox"
                              aria-label={m.id}
                              checked={!!entry}
                              onChange={() =>
                                entry ? remove(entry) : add(m.id, m)
                              }
                            />
                            <span>{m.id}</span>
                            {m.architecture?.input_modalities?.includes(
                              "image",
                            ) && <small>图像输入</small>}
                          </label>
                        );
                      })}
                  </div>
                </div>
              )}
              <div className="manual-model">
                <Field label="手动添加模型 ID">
                  <input
                    value={manual}
                    onChange={(e) => setManual(e.target.value)}
                    placeholder="输入实际模型 ID"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        add(manual);
                      }
                    }}
                  />
                </Field>
                <button
                  type="button"
                  className="button"
                  disabled={!manual.trim()}
                  onClick={() => add(manual)}
                >
                  添加
                </button>
              </div>
              <div className="selected-models" aria-label="已选模型">
                {entries.map((entry) => (
                  <div className="selected-model" key={entry.id}>
                    <div className="selected-model-title">
                      <Check size={16} />
                      <strong>{entry.profile.model}</strong>
                      <button
                        className="icon-button"
                        type="button"
                        aria-label={"移除 " + entry.profile.model}
                        onClick={() => remove(entry)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                    <ModelOptions
                      entry={entry}
                      onChange={(profile) =>
                        setEntries((old) =>
                          old.map((e) =>
                            e.id === entry.id ? { ...e, profile } : e,
                          ),
                        )
                      }
                    />
                  </div>
                ))}
              </div>
              {!entries.length && (
                <p className="muted">
                  获取目录后勾选模型，或手动添加。一个服务可以使用多个模型。
                </p>
              )}
            </section>
          </fieldset>
          <ErrorNotice error={error} reserveSpace />
          <div className="service-dialog-actions">
            <span>
              {entries.length
                ? `已选 ${entries.length} 个模型`
                : "尚未选择模型"}
            </span>
            <div>
              <button
                type="button"
                className="button"
                disabled={!!busy}
                onClick={onClose}
              >
                取消
              </button>
              <button
                type="submit"
                className="button primary"
                disabled={!!busy}
              >
                {busy === "save" ? "保存中…" : "保存连接"}
              </button>
            </div>
          </div>
        </form>
      )}
    </Modal>
  );
}
function ModelOptions({
  entry,
  onChange,
}: {
  entry: Entry;
  onChange: (profile: RecordData) => void;
}) {
  const p = entry.profile;
  const set = (key: string, value: any) => onChange({ ...p, [key]: value });
  return (
    <details className="model-options">
      <summary>
        模型设置 · {p.vision ? "文本与图像输入" : "文本输入"} ·{" "}
        {(p.context_tokens || 32768).toLocaleString()} tokens
      </summary>
      <Field label="模型 ID">
        <input
          value={p.model}
          required
          onChange={(e) => set("model", e.target.value)}
        />
      </Field>
      <label className="check">
        <input
          type="checkbox"
          checked={p.vision === true}
          onChange={(e) => set("vision", e.target.checked)}
        />
        支持图像输入
      </label>
      <small>请按模型实际能力设置；目录未声明时不会根据名称推断。</small>
      <div className="form-row">
        <Field label="上下文 Token 上限">
          <input
            type="number"
            min="1024"
            max="2000000"
            required
            value={p.context_tokens || 32768}
            onChange={(e) => set("context_tokens", Number(e.target.value))}
          />
        </Field>
        <Field label="结构化输出">
          <StudioSelect
            value={p.response_format || "json_object"}
            onChange={(e) => set("response_format", e.target.value)}
          >
            {[
              "json_object",
              "openai_json_schema",
              "llama_json_schema",
              "prompt",
            ].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </StudioSelect>
        </Field>
      </div>
      <div className="form-row">
        <Field label="模型目录检查">
          <StudioSelect
            value={p.model_discovery || "required"}
            onChange={(e) => set("model_discovery", e.target.value)}
          >
            <option value="required">检查已登记模型</option>
            <option
              value="skip"
              disabled={["llama_cpp", "ollama_native"].includes(p.provider)}
            >
              跳过（连接未验证）
            </option>
          </StudioSelect>
        </Field>
        <Field label="上下文验证">
          <StudioSelect
            value={p.context_validation || "conservative_estimate"}
            onChange={(e) => set("context_validation", e.target.value)}
          >
            <option value="conservative_estimate">保守估算</option>
            <option
              value="llama_tokenize"
              disabled={p.provider !== "llama_cpp"}
            >
              llama.cpp tokenize
            </option>
          </StudioSelect>
        </Field>
      </div>
      <div className="form-row">
        <Field label="温度覆盖">
          <input
            type="number"
            min="0"
            max="2"
            step="any"
            value={p.temperature_override ?? ""}
            onChange={(e) => {
              const next = { ...p };
              if (e.target.value === "") delete next.temperature_override;
              else next.temperature_override = Number(e.target.value);
              onChange(next);
            }}
          />
        </Field>
        {["dashscope", "ollama_native"].includes(p.provider) && (
          <Field label="Thinking">
            <StudioSelect
              value={p.thinking === undefined ? "" : String(p.thinking)}
              onChange={(e) => {
                const next = { ...p };
                if (e.target.value === "") delete next.thinking;
                else next.thinking = e.target.value === "true";
                onChange(next);
              }}
            >
              <option value="">服务默认</option>
              <option value="true">启用</option>
              <option value="false">关闭</option>
            </StudioSelect>
          </Field>
        )}
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={p.enabled !== false}
          onChange={(e) => set("enabled", e.target.checked)}
        />
        启用模型
      </label>
    </details>
  );
}
