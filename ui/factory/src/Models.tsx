import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Search,
  ArrowLeft,
  Settings2,
  ChevronRight,
  ExternalLink,
} from "lucide-react";
import { request, keys, rows, roleName, type RecordData } from "./api";
import { Heading, ErrorNotice, Loading, Field, Panel, Empty } from "./ui";
import {
  protocolName,
  ProviderSymbol,
  providerLinks,
} from "./ConnectionFields";
import {
  makeDraft,
  services,
  serviceHost,
  providerLabel,
  modelUses,
  kindLabels,
  profileField,
  mediaRoles,
  type ModelKind,
  type Service,
} from "./ModelConfig";
import { useModelDraft } from "./ModelDraft";
import { ServiceEditor } from "./ServiceEditor";
import { MediaServiceEditor } from "./MediaServiceEditor";
import { WorkflowTools } from "./WorkflowTools";
import "./model-settings.css";
export { normalizeProfile } from "./ModelConfig";

type Editor = { kind: ModelKind; service?: Service; inventory: RecordData };
function servicePurpose(service: Service, draft: RecordData) {
  if (service.kind !== "llm") return kindLabels[service.kind] + "制作";
  const roles = service.profiles.flatMap(([id]) => modelUses(draft, "llm", id));
  const labels = [];
  if (
    roles.some(
      (role) =>
        !["visual_critic", "story_critic", "independent_critic"].includes(role),
    )
  )
    labels.push("文本创作");
  if (
    roles.some((role) => ["story_critic", "independent_critic"].includes(role))
  )
    labels.push("文本审核");
  if (roles.includes("visual_critic")) labels.push("视觉审核");
  return labels.join(" / ") || "未分配用途";
}
export function Models({ notify }: { notify: (v: string) => void }) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.models,
    queryFn: () => request("/models"),
    refetchInterval: 60000,
  });
  const { store, draft, dirty, base, saving, error, probes, probing } =
    useModelDraft(client);
  const [tab, setTab] = useState("services");
  const [selected, setSelected] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Editor | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const lock = useRef(false);
  useEffect(() => {
    if (!query.data || store.getSnapshot().dirty || store.getSnapshot().saving)
      return;
    const current = store.getSnapshot();
    store.update({
      draft: makeDraft(query.data),
      base: query.data.revision,
      ...(current.base !== query.data.revision
        ? { probes: {}, generation: current.generation + 1 }
        : {}),
    });
  }, [query.data, dirty, saving, store]);
  async function commit(
    payload: RecordData,
    revision: string,
    select?: string,
  ) {
    if (lock.current) throw new Error("配置正在保存，请稍候。");
    lock.current = true;
    store.update({
      saving: true,
      error: "",
      probes: {},
      generation: store.getSnapshot().generation + 1,
    });
    try {
      const result = await request("/models", {
        ...payload,
        roles: Object.fromEntries(
          Object.entries(payload.roles).filter(([, v]) => v !== ""),
        ),
        expected_revision: revision,
      });
      client.setQueryData(keys.models, result);
      store.update({
        draft: makeDraft(result),
        base: result.revision,
        dirty: false,
      });
      if (select) {
        setSelected(select);
        setShowDetails(true);
        setEditing(null);
      }
      notify("配置已保存，后续执行批次使用新配置。");
    } catch (e) {
      void client.invalidateQueries({ queryKey: keys.models });
      throw e;
    } finally {
      lock.current = false;
      store.update({ saving: false });
    }
  }
  function change(fn: (draft: RecordData) => RecordData) {
    const current = store.getSnapshot();
    if (!current.draft || current.saving) return;
    store.update({
      draft: fn(current.draft),
      dirty: true,
      error: "",
      probes: {},
      generation: current.generation + 1,
    });
  }
  async function probe(kind: ModelKind, id: string) {
    const current = store.getSnapshot();
    if (current.saving || current.dirty || current.probing) return;
    const key = kind + ":" + id,
      generation = current.generation;
    store.update({ probing: key });
    try {
      const result = await request("/models/test", {
        profile_id: id,
        ...(kind !== "llm" ? { kind } : {}),
      });
      if (store.getSnapshot().generation === generation)
        store.update({
          probes: { ...store.getSnapshot().probes, [key]: result },
        });
    } catch (e) {
      if (store.getSnapshot().generation === generation)
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
  if (!inventory || !draft)
    return (
      <>
        <Heading eyebrow="MODEL SETTINGS" title="模型与连接" />
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
  const all = services(draft),
    current = all.find((s) => s.id === selected) || all[0];
  const filtered = all.filter((s) =>
    `${servicePurpose(s, draft)} ${providerLabel(inventory, s.provider)} ${serviceHost(s.profiles[0][1])} ${s.profiles.map(([, p]) => p.model).join(" ")}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const open = (kind: ModelKind, service?: Service) => {
    if (!dirty && !saving)
      setEditing({ kind, service, inventory: structuredClone(inventory) });
  };
  return (
    <div className="model-settings">
      <Heading
        eyebrow="MODEL SETTINGS"
        title="模型与连接"
        description="先连接服务，再选择各项制作使用的模型。"
      />
      <div className="model-settings-tabs" role="tablist" aria-label="模型设置">
        {[
          ["services", "服务连接"],
          ["roles", "制作分工"],
          ["tools", "高级工具"],
        ].map(([id, name]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            aria-controls={"model-tab-" + id}
            id={"model-tab-button-" + id}
            onClick={() => setTab(id)}
          >
            {name}
            {id === "roles" && dirty && <span className="unsaved-dot" />}
          </button>
        ))}
      </div>
      <ErrorNotice error={query.error} />
      {dirty && tab !== "roles" && (
        <div className="model-draft-notice">
          制作分工有未保存的修改。
          <button className="button quiet" onClick={() => setTab("roles")}>
            返回保存分工
            <ChevronRight size={15} />
          </button>
        </div>
      )}
      {tab === "services" && (
        <div
          role="tabpanel"
          aria-labelledby="model-tab-button-services"
          id="model-tab-services"
          className={
            "service-workspace " + (showDetails ? "show-service-detail" : "")
          }
        >
          <aside className="service-sidebar">
            <div className="service-sidebar-heading">
              <strong>
                我的服务 <span>{all.length}</span>
              </strong>
              <button
                className="button primary compact"
                data-model-action="add"
                disabled={saving || dirty}
                onClick={() => open("llm")}
              >
                <Plus size={15} />
                添加服务
              </button>
            </div>
            <Field label="搜索服务">
              <div className="service-search">
                <Search size={16} />
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="服务商、地址或模型"
                />
              </div>
            </Field>
            <nav className="service-list" aria-label="已连接的服务">
              {(["llm", "image", "video", "audio"] as ModelKind[]).map(
                (kind) => (
                  <div key={kind}>
                    <div className="service-list-label">
                      {kindLabels[kind]}
                      <button
                        className="icon-button"
                        aria-label={"添加" + kindLabels[kind] + "服务"}
                        disabled={saving || dirty}
                        onClick={() => open(kind)}
                      >
                        <Plus size={15} />
                      </button>
                    </div>
                    {filtered
                      .filter((s) => s.kind === kind)
                      .map((s) => (
                        <button
                          key={s.id}
                          className={
                            "service-list-item " +
                            (current?.id === s.id ? "active" : "")
                          }
                          onClick={() => {
                            setSelected(s.id);
                            setShowDetails(true);
                          }}
                          aria-current={
                            current?.id === s.id ? "true" : undefined
                          }
                          data-service={s.id}
                        >
                          <ProviderSymbol provider={s.provider} />
                          <span>
                            <strong>{servicePurpose(s, draft)}</strong>
                            <small
                              title={s.profiles
                                .map(([, p]) => p.model)
                                .join("、")}
                            >
                              {providerLabel(inventory, s.provider)} ·{" "}
                              {s.profiles.map(([, p]) => p.model).join("、")}
                            </small>
                            <small title={s.profiles[0][1].base_url}>
                              {serviceHost(s.profiles[0][1])}
                            </small>
                          </span>
                          <b>{s.profiles.length}</b>
                        </button>
                      ))}
                  </div>
                ),
              )}
            </nav>
            {search && !filtered.length && (
              <p className="muted">没有匹配的服务</p>
            )}
          </aside>
          <section className="service-detail">
            <button
              className="button quiet service-back"
              onClick={() => setShowDetails(false)}
            >
              <ArrowLeft size={16} />
              服务列表
            </button>
            {current ? (
              <ServiceDetails
                service={current}
                inventory={inventory}
                draft={draft}
                disabled={saving || dirty}
                probes={probes}
                probing={probing}
                onEdit={() => open(current.kind, current)}
                onProbe={(id) => probe(current.kind, id)}
              />
            ) : (
              <Empty
                title="连接你的第一个模型服务"
                action={
                  <button
                    className="button primary"
                    onClick={() => open("llm")}
                  >
                    <Plus size={15} />
                    添加服务
                  </button>
                }
              >
                云端服务填写 Key；本地与自定义服务填写 URL 和可选 Key。
              </Empty>
            )}
          </section>
        </div>
      )}
      {tab === "roles" && (
        <section
          role="tabpanel"
          aria-labelledby="model-tab-button-roles"
          id="model-tab-roles"
        >
          <Assignments
            inventory={inventory}
            draft={draft}
            disabled={saving}
            onChange={change}
          />
          <ErrorNotice error={error} />
          {dirty && inventory.revision !== base && (
            <ErrorNotice error="其他终端已更新配置。你的分工修改仍保留，请先核对最新配置。" />
          )}
          <div className="role-savebar">
            <span>{dirty ? "分工已修改，保存后生效" : "制作分工已保存"}</span>
            <div>
              <button
                className="button"
                disabled={!dirty || saving}
                onClick={() =>
                  store.update({
                    draft: makeDraft(inventory),
                    base: inventory.revision,
                    dirty: false,
                    error: "",
                    probes: {},
                    generation: store.getSnapshot().generation + 1,
                  })
                }
              >
                放弃修改
              </button>
              <button
                className="button primary"
                data-model-action="save-roles"
                disabled={!dirty || saving}
                onClick={async () => {
                  const current = store.getSnapshot();
                  if (!current.draft || current.saving) return;
                  try {
                    await commit(current.draft, current.base);
                  } catch (e) {
                    store.update({ error: (e as Error).message });
                  }
                }}
              >
                {saving ? "保存中…" : "保存分工"}
              </button>
            </div>
          </div>
        </section>
      )}
      {tab === "tools" && (
        <section
          role="tabpanel"
          aria-labelledby="model-tab-button-tools"
          id="model-tab-tools"
        >
          <WorkflowTools notify={notify} />
        </section>
      )}
      {editing?.kind === "llm" && (
        <ServiceEditor
          inventory={editing.inventory}
          service={editing.service}
          onKindChange={(kind) =>
            setEditing({ kind, inventory: editing.inventory })
          }
          onClose={() => setEditing(null)}
          onSave={commit}
        />
      )}
      {editing && editing.kind !== "llm" && (
        <MediaServiceEditor
          inventory={editing.inventory}
          service={editing.service}
          kind={editing.kind}
          onKindChange={(kind) =>
            setEditing({ kind, inventory: editing.inventory })
          }
          onClose={() => setEditing(null)}
          onSave={commit}
        />
      )}
    </div>
  );
}
function ServiceDetails({
  service,
  inventory,
  draft,
  disabled,
  probes,
  probing,
  onEdit,
  onProbe,
}: {
  service: Service;
  inventory: RecordData;
  draft: RecordData;
  disabled: boolean;
  probes: RecordData;
  probing: string;
  onEdit: () => void;
  onProbe: (id: string) => void;
}) {
  const p = service.profiles[0][1],
    links = providerLinks[service.provider];
  return (
    <>
      <header className="service-detail-heading">
        <div className="service-editor-brand">
          <ProviderSymbol provider={service.provider} />
          <div>
            <h2>{providerLabel(inventory, service.provider)}</h2>
            <small>{servicePurpose(service, draft)}</small>
          </div>
        </div>
        <button
          className="button"
          disabled={disabled}
          data-model-action="manage"
          onClick={onEdit}
        >
          <Settings2 size={15} />
          管理服务
        </button>
      </header>
      <div className="service-connection-summary">
        <span>
          {service.kind === "llm"
            ? protocolName(service.provider)
            : service.provider === "comfyui_h3"
              ? "ComfyUI H3 工作流"
              : "制作服务"}
        </span>
        <code>{p.base_url || "沿用制作服务器的运行环境"}</code>
        <div>
          <small>
            {p.tls_client_cert_env
              ? "双向 TLS 身份已关联"
              : p.api_key_credential
                ? "系统凭据已关联"
                : p.api_key_env
                  ? "服务端密钥已关联"
                  : "未关联 API Key"}
          </small>
          {links && (
            <a href={links.docs} target="_blank" rel="noreferrer">
              官方文档
              <ExternalLink size={12} />
            </a>
          )}
        </div>
      </div>
      <div className="service-models-heading">
        <h3>
          已添加模型 <span>{service.profiles.length}</span>
        </h3>
        <button className="button quiet" disabled={disabled} onClick={onEdit}>
          <Plus size={15} />
          管理模型
        </button>
      </div>
      <div className="service-model-list">
        {service.profiles.map(([id, profile]) => {
          const used = modelUses(draft, service.kind, id),
            result = probes[service.kind + ":" + id];
          return (
            <article className="service-model-row" key={id} data-profile={id}>
              <div className="service-model-info">
                <strong className="model-name">{profile.model}</strong>
                <div className="service-model-tags">
                  <span>
                    {profile.enabled === false
                      ? "已停用"
                      : service.kind === "llm"
                        ? profile.vision
                          ? "文本 · 图像输入"
                          : "文本输入"
                        : kindLabels[service.kind]}
                  </span>
                  {profile.context_tokens && (
                    <span>
                      {profile.context_tokens.toLocaleString()} tokens
                    </span>
                  )}
                  {service.provider === "comfyui_h3" && (
                    <span>
                      {profile.quality === "production"
                        ? `正式质量 · ${profile.steps || 20} 步`
                        : "快速预览 · 4 步"}
                    </span>
                  )}
                </div>
                <small className="service-model-uses">
                  {used.length
                    ? used.map(roleName).join("、")
                    : "尚未分配制作任务"}
                </small>
              </div>
              <div className="service-model-actions">
                <button
                  className="button quiet"
                  disabled={disabled || !!probing || profile.enabled === false}
                  data-model-action="test"
                  data-profile={id}
                  onClick={() => onProbe(id)}
                >
                  {probing === service.kind + ":" + id ? "检查中…" : "检查连接"}
                </button>
                <button
                  className="icon-button"
                  aria-label={"设置 " + profile.model}
                  data-model-action="edit"
                  data-profile={id}
                  disabled={disabled}
                  onClick={onEdit}
                >
                  <Settings2 size={16} />
                </button>
              </div>
              {result && (
                <div
                  className={
                    "service-probe-result " +
                    (result.status === "error" ? "failed" : "")
                  }
                  role="status"
                >
                  {result.message ||
                    result.error?.message ||
                    "已完成配置检查，未验证生成。"}
                  {result.input_modalities && (
                    <small>
                      目录声明输入：{result.input_modalities.join("、")}
                      ；未验证生成。
                    </small>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
      <p className="service-detail-footnote">
        连接检查不会生成素材。模型目录可用，不代表生成或视觉能力已验证。
      </p>
    </>
  );
}
const criticRoles = new Set(["story_critic", "independent_critic"]);
function Assignments({
  inventory,
  draft,
  disabled,
  onChange,
}: {
  inventory: RecordData;
  draft: RecordData;
  disabled: boolean;
  onChange: (fn: (draft: RecordData) => RecordData) => void;
}) {
  const roles = rows(inventory.roles).map<RecordData>((r) => ({
    ...r,
    requires_vision: r.requires_vision || r.role === "visual_critic",
  }));
  const groups = [
    {
      id: "creation",
      title: "文本创作",
      description: "选题、故事、编剧、分镜与提示词",
      members: roles.filter(
        (r) => !r.requires_vision && !criticRoles.has(r.role) && !r.optional,
      ),
    },
    {
      id: "review",
      title: "文本审稿",
      description: "故事审稿与独立审稿，可选择独立模型",
      members: roles.filter((r) => criticRoles.has(r.role)),
    },
    {
      id: "vision",
      title: "视觉审核",
      description: "首帧和视频画面检查，需要图像输入能力",
      members: roles.filter((r) => r.requires_vision),
    },
  ];
  function options(kind: ModelKind, vision = false) {
    return Object.entries(draft[profileField(kind)] || {}).map(
      ([id, p]: [string, any]) => (
        <option
          key={id}
          value={id}
          disabled={p.enabled === false || (vision && !p.vision)}
        >
          {p.model} · {providerLabel(inventory, p.provider)}
          {Object.values(draft[profileField(kind)] || {}).filter(
            (other: any) =>
              other.model === p.model && other.provider === p.provider,
          ).length > 1
            ? " · " + serviceHost(p)
            : ""}
          {vision && !p.vision ? "（无图像输入）" : ""}
        </option>
      ),
    );
  }
  return (
    <div className="assignment-settings">
      <Panel
        title="创作与审核"
        description="按用途选择模型。已有的逐角色分工会保留；选择一组模型时才会统一这一组。"
      >
        {groups
          .filter((g) => g.members.length)
          .map((group) => {
            const selected = [
              ...new Set(group.members.map((r) => draft.roles[r.role] || "")),
            ];
            return (
              <div className="assignment-row" key={group.id}>
                <div>
                  <h3>{group.title}</h3>
                  <p>{group.description}</p>
                </div>
                <Field label={group.title + "模型"}>
                  <select
                    value={selected.length === 1 ? selected[0] : "__mixed__"}
                    disabled={disabled}
                    onChange={(e) => {
                      const value = e.target.value;
                      onChange((d) => ({
                        ...d,
                        roles: {
                          ...d.roles,
                          ...Object.fromEntries(
                            group.members.map((r) => [r.role, value]),
                          ),
                        },
                      }));
                    }}
                  >
                    <option value="" disabled>
                      选择模型
                    </option>
                    <option value="__mixed__" disabled>
                      已按角色分别配置
                    </option>
                    {options("llm", group.id === "vision")}
                  </select>
                </Field>
              </div>
            );
          })}
        <details className="assignment-advanced">
          <summary>按角色单独配置 · 总制片代理</summary>
          <div className="assignment-role-grid">
            {roles.map((meta) => (
              <Field label={meta.label || roleName(meta.role)} key={meta.role}>
                <select
                  id={"role-" + meta.role}
                  value={draft.roles[meta.role] || ""}
                  disabled={disabled}
                  onChange={(e) => {
                    const value = e.target.value;
                    onChange((d) => ({
                      ...d,
                      roles: { ...d.roles, [meta.role]: value },
                    }));
                  }}
                >
                  {meta.optional ? (
                    <option value="">不启用（转人工）</option>
                  ) : (
                    <option value="" disabled>
                      选择模型
                    </option>
                  )}
                  {options("llm", meta.requires_vision)}
                </select>
              </Field>
            ))}
          </div>
        </details>
      </Panel>
      <Panel
        title="媒体制作"
        description="图像、视频与配音由已连接的制作服务执行。"
      >
        {(["image", "video", "audio"] as const)
          .filter((kind) => draft[profileField(kind)])
          .map((kind) => (
            <div className="assignment-row" key={kind}>
              <div>
                <h3>{kindLabels[kind]}</h3>
                <p>
                  {kind === "audio"
                    ? "云端语音或制作服务器 Qwen3-TTS"
                    : kind === "video"
                      ? "图生视频与镜头制作"
                      : "素材、角色图与镜头首帧"}
                </p>
              </div>
              <Field label={kindLabels[kind] + "制作模型"}>
                <select
                  id={"role-" + kind + "-" + mediaRoles[kind]}
                  value={draft[kind + "_roles"]?.[mediaRoles[kind]] || ""}
                  disabled={disabled}
                  onChange={(e) => {
                    const value = e.target.value;
                    onChange((d) => ({
                      ...d,
                      [kind + "_roles"]: {
                        ...d[kind + "_roles"],
                        [mediaRoles[kind]]: value,
                      },
                    }));
                  }}
                >
                  <option value="" disabled>
                    选择模型
                  </option>
                  {options(kind)}
                </select>
              </Field>
            </div>
          ))}
      </Panel>
    </div>
  );
}
