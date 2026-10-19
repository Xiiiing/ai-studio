import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { date, request, roleName, rows, type RecordData } from "./api";
import { Empty, ErrorNotice, Field, Loading, Panel } from "./ui";
import "./prompt-workbench.css";

const names: Record<string, string> = {
  role: "岗位职责",
  project: "项目要求",
  production: "制作规格",
  model: "模型适配说明",
  references: "参考资料说明",
  repair: "返修要求",
};
const empty = {
  project: "",
  production: "",
  model: "",
  references: "",
  repair: "",
};
const newId = () => crypto.randomUUID().replaceAll("-", "");
const states: Record<string, string> = {
  queued: "等待执行",
  running: "正在试运行",
  succeeded: "模型已返回（效果待审）",
  unknown: "结果未知，已停止新增试运行",
  closed: "已结束",
};

export function PromptWorkbench({ project }: { project: RecordData }) {
  return <Workbench key={project.id} project={project} />;
}
function Workbench({ project }: { project: RecordData }) {
  const client = useQueryClient();
  const path = `/projects/${project.id}/prompts`;
  const query = useQuery({
    queryKey: ["prompts", project.id],
    queryFn: () => request(path),
    refetchInterval: 5000,
  });
  const calls = useQuery({
    queryKey: ["prompt-calls", project.id],
    queryFn: () => request(path + "/calls"),
  });
  const [key, setKey] = useState("");
  const [editRevision, setEditRevision] = useState<number | null>(null);
  const [defaultRevisions, setDefaultRevisions] = useState<RecordData>({});
  const [editionId, setEditionId] = useState("");
  const [baselineId, setBaselineId] = useState("");
  const [content, setContent] = useState<RecordData>({ role: "", ...empty });
  const [overrides, setOverrides] = useState<RecordData>(empty);
  const [label, setLabel] = useState("");
  const [input, setInput] = useState("{}");
  const [schemaIndex, setSchemaIndex] = useState(0);
  const [preview, setPreview] = useState<RecordData | null>(null);
  const [validation, setValidation] = useState<RecordData | null>(null);
  const [historical, setHistorical] = useState<RecordData | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false),
    [trialConsent, setTrialConsent] = useState(false);
  const [maxTokens, setMaxTokens] = useState(4096);
  // A failed response must retain its operation identity until explicit discard.
  const [pending, setPending] = useState<{
    operation: string;
    body: RecordData;
  } | null>(null);
  const catalog = rows(query.data?.catalog),
    editions = rows(query.data?.editions);
  const current = catalog.find((r) => r.key === key);
  const versions = editions.filter((r) => r.key === key);
  const saved = editions.find((r) => r.id === editionId);
  const trialVerified = (query.data?.verified_editions || []).includes(
    editionId,
  );
  const dirty =
    !saved || JSON.stringify(saved.sections) !== JSON.stringify(content);
  const snapshot = query.data?.snapshot;
  useEffect(() => {
    if (!query.data || key) return;
    const first = rows(query.data.catalog)[0];
    if (first) {
      setKey(first.key);
      setContent(first.sections);
    }
    setOverrides(query.data.snapshot?.overrides || empty);
    setEditRevision(query.data.revision);
    setDefaultRevisions(query.data.defaults || {});
  }, [query.data, key]);
  function choose(newKey: string, id = "") {
    const selected = editions.find((r) => r.id === id);
    setKey(newKey);
    setBaselineId("");
    setEditionId(id);
    setContent(
      selected?.sections ||
        catalog.find((r) => r.key === newKey)?.sections || {
          role: "",
          ...empty,
        },
    );
    setPreview(null);
    setValidation(null);
    setConfirmed(false);
    setError("");
    setSchemaIndex(0);
  }
  function invalidatePreview() {
    setPreview(null);
    setValidation(null);
    setConfirmed(false);
  }
  function previewBody(savedOnly = false) {
    const body: RecordData = {
      key,
      overrides,
      input: JSON.parse(input),
      schema_index: schemaIndex,
    };
    if (!savedOnly) body.baseline_id = baselineId;
    if (savedOnly || !dirty) body.edition_id = editionId;
    else body.sections = content;
    return body;
  }
  async function perform(operation: string, body: RecordData, retry = false) {
    if (busy) return;
    setBusy(true);
    setError("");
    const item =
      retry && pending
        ? pending
        : { operation, body: { ...body, request_id: newId() } };
    const isPreview = operation === "preview";
    if (!isPreview) setPending(item);
    try {
      const value = await request(
        `${path}/${item.operation}`,
        isPreview ? body : item.body,
      );
      if (item.operation === "save") {
        setEditionId(value.id);
        setContent(value.sections);
        setPreview(null);
        setValidation(null);
      }
      if (item.operation === "preview") setPreview(value);
      if (item.operation === "validate") setValidation(value);
      if (item.operation === "apply") {
        setOverrides(value.snapshot.overrides);
        setEditRevision(value.revision);
      }
      if (item.operation === "activate") {
        setDefaultRevisions((previous) => ({
          ...previous,
          [value.key]: value,
        }));
        setConfirmed(false);
      }
      setPending(null);
      await client.invalidateQueries({ queryKey: ["prompts", project.id] });
      if (item.operation === "apply") {
        await client.invalidateQueries({ queryKey: ["workspace", project.id] });
        await client.invalidateQueries({ queryKey: ["drama", project.id] });
        await client.invalidateQueries({
          queryKey: ["long-episode", project.id],
        });
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function synchronize() {
    setBusy(true);
    try {
      const current = await client.fetchQuery({
        queryKey: ["prompts", project.id],
        queryFn: () => request(path),
      });
      setEditRevision(current.revision);
      setDefaultRevisions(current.defaults || {});
      setOverrides(current.snapshot?.overrides || empty);
      setPending(null);
      setError("");
      invalidatePreview();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function parseAndRun(operation: string, body: () => RecordData) {
    try {
      void perform(operation, body());
    } catch (e) {
      setError("输入 JSON 无效：" + (e as Error).message);
    }
  }
  if (query.isPending) return <Loading />;
  if (query.error && !query.data) return <ErrorNotice error={query.error} />;
  const disabled = busy || !!pending;
  return (
    <div className="stack prompt-workbench" data-prompt-workbench>
      <Panel
        title="提示词工作台"
        description="编辑岗位模板，预览实际装配，显式选择项目使用的版本。"
      >
        <p className="muted">
          公共默认只影响新项目；此项目当前
          {snapshot ? "固定了独立快照" : "沿用原提示词"}
          。已有素材与历史调用保持原样。当前支持注册表中的岗位合约；全剧规划等内置任务指令可在下方查看实际调用。
        </p>
        <ErrorNotice error={error || query.error} />
        {pending && !busy && (
          <div className="prompt-actions" role="status">
            <span>上次操作结果尚未同步，可重试同一请求。</span>
            <button
              className="button"
              onClick={() => perform(pending.operation, pending.body, true)}
            >
              重试上次操作
            </button>
            <button className="button" onClick={synchronize}>
              刷新后重新准备
            </button>
          </div>
        )}
        {editRevision !== null && editRevision !== query.data?.revision && (
          <p role="status">
            项目已更新。
            <button className="button" disabled={busy} onClick={synchronize}>
              同步项目版本
            </button>
          </p>
        )}
        <div className="prompt-grid">
          <Field label="岗位与任务合约">
            <select
              value={key}
              disabled={disabled}
              onChange={(e) => choose(e.target.value)}
            >
              {catalog.map((r) => (
                <option key={r.key} value={r.key}>
                  {roleName(r.role)} · 合约 {r.base_version}
                </option>
              ))}
            </select>
          </Field>
          <Field label="编辑版本">
            <select
              value={editionId}
              disabled={disabled}
              onChange={(e) => choose(key, e.target.value)}
            >
              <option value="">从内置模板新建</option>
              {versions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label} · {date(r.created_at)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="版本比较基线">
          <select
            value={baselineId}
            disabled={disabled}
            onChange={(e) => {
              setBaselineId(e.target.value);
              setPreview(null);
            }}
          >
            <option value="">内置模板</option>
            {versions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
        <p>{current?.responsibility}</p>
        <p className="muted">
          此项目：
          {editions.find((r) => r.id === snapshot?.versions?.[key]?.id)
            ?.label ||
            snapshot?.versions?.[key]?.label ||
            "内置模板"}
          ；新项目默认：
          {editions.find(
            (r) => r.id === query.data?.defaults?.[key]?.edition_id,
          )?.label || "内置模板"}
        </p>
        <p className="muted">
          可用参数：
          {(query.data?.parameters || [])
            .map((p: string) => `{{${p}}}`)
            .join("、")}
          。制作规格及项目已确认要求会由系统继续装配。
        </p>
        {Object.entries(names).map(([name, title]) => (
          <details
            key={name}
            open={name === "role" ? true : undefined}
            className="prompt-section"
          >
            <summary>
              {title}
              {content[name] ? " · 已填写" : ""}
            </summary>
            <Field label={title}>
              <textarea
                value={content[name] || ""}
                maxLength={name === "role" ? 16000 : 4000}
                rows={name === "role" ? 12 : 4}
                disabled={disabled}
                onChange={(e) => {
                  setContent({ ...content, [name]: e.target.value });
                  invalidatePreview();
                }}
              />
            </Field>
          </details>
        ))}
        <div className="prompt-grid">
          <Field label="新版本名称">
            <input
              value={label}
              maxLength={80}
              disabled={disabled}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="例如：强化动作因果 v2"
            />
          </Field>
          <Field label="输出合约序号">
            <select
              value={schemaIndex}
              disabled={disabled}
              onChange={(e) => {
                setSchemaIndex(Number(e.target.value));
                invalidatePreview();
              }}
            >
              <option value={0}>主要合约</option>
              {key === "independent_critic@1.0.0" && (
                <option value={1}>剧本审核合约</option>
              )}
            </select>
          </Field>
        </div>
        <div className="prompt-actions">
          <button
            className="button"
            disabled={disabled || !label.trim()}
            onClick={() => perform("save", { key, label, sections: content })}
          >
            保存为新版本
          </button>
          <button
            className="button"
            disabled={disabled}
            onClick={() => parseAndRun("preview", () => previewBody())}
          >
            预览装配与差异
          </button>
          <button
            className="button"
            disabled={disabled || dirty}
            onClick={() =>
              parseAndRun("validate", () => ({
                edition_id: editionId,
                input: JSON.parse(input),
                schema_index: schemaIndex,
              }))
            }
          >
            验证已保存版本
          </button>
        </div>
        <Field label="试运行任务输入（JSON）">
          <textarea
            value={input}
            rows={6}
            maxLength={80000}
            disabled={disabled}
            onChange={(e) => {
              setInput(e.target.value);
              invalidatePreview();
            }}
          />
        </Field>
        <p className="muted">
          输入应符合当前岗位的任务合约，可从下方实际调用载入。预览和装配验证不调用模型；验证仅证明结构与装配有效，不能证明作品改善。
        </p>
        {validation && (
          <p role="status">
            已通过结构与装配验证 · {validation.id.slice(0, 12)} ·
            作品效果尚未建立
          </p>
        )}
        {preview && (
          <div className="stack">
            <p>
              预览模型：{preview.model?.model || "未配置"} ·{" "}
              {preview.model?.system_prompt_mode || "system"}；变换：
              {(preview.transforms || []).join(" → ") || "无"}
            </p>
            <details open>
              <summary>最终角色提示词</summary>
              <pre>{preview.system}</pre>
            </details>
            <details>
              <summary>实际系统规则（含模型协议）</summary>
              <pre>{preview.wire_system || "请先配置模型"}</pre>
            </details>
            <details>
              <summary>段落来源与调用身份</summary>
              <pre>{JSON.stringify(preview.prompt, null, 2)}</pre>
            </details>
            <details>
              <summary>六段模板版本差异</summary>
              <pre>{preview.diff || "文本相同"}</pre>
            </details>
            <details>
              <summary>输入与输出合约</summary>
              <pre>
                {JSON.stringify(
                  {
                    input: preview.input_schema,
                    output: preview.output_schema,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          </div>
        )}
      </Panel>
      <Panel
        title="项目覆盖与版本应用"
        description="只改变后续明确执行的任务；应用本身不会重做素材。"
      >
        <details>
          <summary>仅此项目的附加要求</summary>
          {Object.keys(empty).map((name) => (
            <Field key={name} label={`项目覆盖：${names[name]}`}>
              <textarea
                rows={3}
                maxLength={4000}
                disabled={disabled}
                value={overrides[name] || ""}
                onChange={(e) => {
                  setOverrides({ ...overrides, [name]: e.target.value });
                  invalidatePreview();
                }}
              />
            </Field>
          ))}
        </details>
        <p className="muted">
          先暂停项目并等待全部在途任务结束，再应用或回退。子剧集与制作单元保持各自快照，需分别应用。
        </p>
        <div className="prompt-actions">
          <button
            className="button primary"
            disabled={disabled || dirty}
            onClick={() =>
              perform("apply", {
                expected_revision: editRevision,
                versions: {
                  ...Object.fromEntries(
                    Object.entries(snapshot?.versions || {}).map(([k, v]) => [
                      k,
                      (v as RecordData).id,
                    ]),
                  ),
                  [key]: editionId,
                },
                overrides,
              })
            }
          >
            将此版本应用到项目
          </button>
          <button
            className="button"
            disabled={disabled}
            onClick={() =>
              perform("apply", {
                expected_revision: editRevision,
                versions: {
                  ...Object.fromEntries(
                    Object.entries(snapshot?.versions || {}).map(([k, v]) => [
                      k,
                      (v as RecordData).id,
                    ]),
                  ),
                  [key]: "",
                },
                overrides,
              })
            }
          >
            此岗位回退内置模板
          </button>
          <button
            className="button"
            disabled={disabled}
            onClick={() =>
              perform("apply", {
                expected_revision: editRevision,
                versions: Object.fromEntries(
                  Object.entries(query.data?.defaults || {}).map(([k, v]) => [
                    k,
                    (v as RecordData).edition_id,
                  ]),
                ),
                overrides,
              })
            }
          >
            显式应用当前公共默认
          </button>
        </div>
        <details>
          <summary>公共默认版本（高级）</summary>
          <p>
            装配验证及本版本试运行成功后，经人工确认可用于后续新项目。当前项目需另外应用；回退同样保留记录。
          </p>
          <label className="check">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={disabled}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            我已检查此版本，确认更改新项目的默认提示词
          </label>
          <div className="prompt-actions">
            <button
              className="button"
              disabled={
                disabled || dirty || !validation || !trialVerified || !confirmed
              }
              onClick={() =>
                perform("activate", {
                  key,
                  edition_id: editionId,
                  expected_revision: defaultRevisions[key]?.revision || 0,
                  validation_id: validation?.id,
                  confirmed,
                })
              }
            >
              启用为公共默认
            </button>
            <button
              className="button"
              disabled={disabled || !confirmed}
              onClick={() =>
                perform("activate", {
                  key,
                  edition_id: "",
                  expected_revision: defaultRevisions[key]?.revision || 0,
                  validation_id: "",
                  confirmed,
                })
              }
            >
              公共默认回退内置模板
            </button>
          </div>
        </details>
      </Panel>
      <Panel
        title="隔离试运行"
        description="保存版本后，使用当前任务输入执行一次既有内网模型请求，结果仅留在此处。"
      >
        <p className="muted">
          当前只开放既有内网推理，费用未知的供应商调用暂不可用。最多一次生成请求、128–16384
          输出
          tokens，不生成媒体。结果未知时停止新增试运行，恢复进程不会自动重投。
        </p>
        <Field label="本次输出上限（tokens）">
          <input
            type="number"
            min={128}
            max={16384}
            value={maxTokens}
            disabled={disabled}
            onChange={(e) => setMaxTokens(Number(e.target.value))}
          />
        </Field>
        <label className="check">
          <input
            type="checkbox"
            checked={trialConsent}
            disabled={disabled}
            onChange={(e) => setTrialConsent(e.target.checked)}
          />
          授权这一次内网模型请求及上述输出额度
        </label>
        <button
          className="button"
          disabled={
            disabled ||
            dirty ||
            !trialConsent ||
            !preview?.model?.trial_available
          }
          onClick={() =>
            parseAndRun("trial", () => ({
              preview: previewBody(true),
              expected_revision: editRevision,
              max_tokens: maxTokens,
              model_config_revision: preview?.model?.config_revision,
              confirmed: trialConsent,
            }))
          }
        >
          启动一次试运行
        </button>
        {rows(query.data?.trials).map((r) => (
          <article key={r.id} className="prompt-trial">
            <strong>{states[r.state] || r.state}</strong>
            <p>
              {date(r.created_at)} · {r.model?.model} · {r.max_tokens} tokens
            </p>
            {r.error && <p role="status">{r.error}</p>}
            {r.rendered && (
              <details>
                <summary>此次试运行的固定提示词与输入</summary>
                <pre>{JSON.stringify(r.rendered, null, 2)}</pre>
              </details>
            )}
            {r.result && (
              <details>
                <summary>模型返回（尚未采用）</summary>
                <pre>{JSON.stringify(r.result, null, 2)}</pre>
              </details>
            )}
            {["unknown", "queued"].includes(r.state) && (
              <button
                className="button"
                disabled={disabled}
                onClick={() =>
                  perform("settle", { trial_id: r.id, confirmed: true })
                }
              >
                结束此试运行，保留记录
              </button>
            )}
          </article>
        ))}
      </Panel>
      <Panel
        title="实际调用的提示词"
        description="查看调用当时保存的最终提示词、输入、合约及段落来源。"
      >
        <ErrorNotice error={calls.error} />
        {!rows(calls.data?.calls).length && (
          <Empty title="暂无装配记录">
            升级后发生的调用会在此显示；历史回执保持原样，可继续在质量比较台查看。
          </Empty>
        )}
        <Field label="选择实际调用">
          <select
            defaultValue=""
            onChange={async (e) => {
              setError("");
              if (!e.target.value) {
                setHistorical(null);
                return;
              }
              try {
                setHistorical(await request(path + "/calls/" + e.target.value));
              } catch (err) {
                setError((err as Error).message);
              }
            }}
          >
            <option value="">请选择调用</option>
            {rows(calls.data?.calls).map((r) => (
              <option key={r.id} value={r.id}>
                {roleName(r.role)} · {r.call_id} · {r.status}
              </option>
            ))}
          </select>
        </Field>
        {historical && (
          <>
            <button
              className="button"
              disabled={
                disabled ||
                !catalog.some(
                  (r) =>
                    r.key ===
                    `${historical.role}@${historical.prompt?.version}`,
                )
              }
              onClick={() => {
                choose(`${historical.role}@${historical.prompt?.version}`);
                setInput(JSON.stringify(historical.input, null, 2));
              }}
            >
              载入此调用的输入
            </button>
            <details open>
              <summary>调用时的装配证据</summary>
              <pre>{JSON.stringify(historical, null, 2)}</pre>
            </details>
          </>
        )}
      </Panel>
    </div>
  );
}
