import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { PageHeader } from "./PageHeader";
import {
  Panel,
  Field,
  Modal,
  ErrorNotice,
  Loading,
  Empty,
  Readable,
} from "./ui";
import {
  request,
  rows,
  mediaUrl,
  names,
  roleName,
  date,
  type RecordData,
} from "./api";
import "./dramas.css";

export const dramaDefaults = { seconds: 300, maxSeconds: 900 };
const statusNames: Record<string, string> = {
  waiting: "等待确认",
  pending: "等待执行",
  completed: "已完成",
  running: "执行中",
  draft: "未生成",
  ready: "规划已确认",
  awaiting_plan: "待确认全剧规划",
  waiting_dependency: "等待前集结果",
  unit_ready: "单元已完成",
  ...names,
};

export function CreateDrama({
  onClose,
  onCreated,
  onLegacy,
}: {
  onClose: () => void;
  onCreated: (p: RecordData) => void;
  onLegacy: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [source, setSource] = useState(""),
    [sourceName, setSourceName] = useState(""),
    [mode, setMode] = useState("brief");
  const requestId = useRef(crypto.randomUUID());
  async function importText(file?: File) {
    if (!file) return;
    try {
      if (file.size > 240000 || !/\.(txt|md|markdown)$/i.test(file.name))
        throw new Error("请选择不超过240KB的TXT或Markdown文件。");
      const decoded = new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
      if (decoded.length > 60000)
        throw new Error("剧本最多60000字，请按部拆分。");
      setSource(decoded);
      setSourceName(file.name);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <Modal id="create-drama" title="新建一部剧" onClose={onClose}>
      <form
        className="drama-form"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          setError("");
          const data = new FormData(event.currentTarget);
          try {
            const p = await request("/dramas", {
              title: data.get("title"),
              brief: data.get("brief"),
              genre: data.get("genre"),
              visual_style: data.get("style"),
              episode_count: Number(data.get("count")),
              duration_seconds: Number(data.get("seconds")),
              format: data.get("format"),
              source_text: mode === "script" ? source : "",
              source_name: mode === "script" ? sourceName : "",
              request_id: requestId.current,
            });
            onCreated(p);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Field label="剧名">
          <input name="title" required maxLength={100} />
        </Field>
        <Field label="创作起点">
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="brief">从梗概创作</option>
            <option value="script">已有剧本</option>
          </select>
        </Field>
        <Field label="创作方向">
          <textarea
            name="brief"
            required={mode === "brief"}
            minLength={5}
            maxLength={6000}
            rows={4}
            placeholder="故事梗概、人物关系和希望表达的主题"
          />
        </Field>
        {mode === "script" && (
          <>
            <Field label="导入剧本">
              <input
                type="file"
                accept=".txt,.md,.markdown,text/plain,text/markdown"
                onChange={(e) => void importText(e.target.files?.[0])}
              />
            </Field>
            <Field label="剧本正文">
              <textarea
                value={source}
                required
                minLength={5}
                maxLength={60000}
                rows={8}
                onChange={(e) => setSource(e.target.value)}
              />
            </Field>
          </>
        )}
        <div className="drama-fields">
          <Field label="总集数">
            <input
              name="count"
              type="number"
              required
              min={1}
              max={100}
              defaultValue={6}
            />
          </Field>
          <Field label="每集时长（秒）">
            <input
              name="seconds"
              type="number"
              required
              min={15}
              max={dramaDefaults.maxSeconds}
              defaultValue={dramaDefaults.seconds}
            />
          </Field>
          <Field label="题材">
            <input name="genre" maxLength={100} defaultValue="剧情" />
          </Field>
          <Field label="制作路线">
            <select name="format">
              <option value="animation">H3 视频剧</option>
              <option value="motion_comic">动态漫</option>
              <option value="live_action">H3 真人风格</option>
            </select>
          </Field>
        </div>
        <Field label="全剧画风">
          <textarea
            name="style"
            maxLength={2000}
            defaultValue="统一人物造型、色彩与场景画风"
            rows={2}
          />
        </Field>
        <p className="muted">
          先生成并确认全剧设定与分集大纲，再选择集数自动制作，成片由你验收。
        </p>
        <ErrorNotice error={error} />
        <div className="drama-actions">
          <button type="submit" className="button primary" disabled={busy}>
            {busy ? "正在建立…" : "建立整剧项目"}
          </button>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={onLegacy}
          >
            使用原单集入口
          </button>
        </div>
      </form>
    </Modal>
  );
}

function PlanEditor({
  document,
  busy,
  confirm,
}: {
  document: RecordData;
  busy: boolean;
  confirm: (value: RecordData) => void;
}) {
  const [draft, setDraft] = useState<RecordData>(() =>
    structuredClone(document),
  );
  function update(key: string, value: unknown) {
    setDraft({ ...draft, [key]: value });
  }
  return (
    <form
      className="drama-form"
      onSubmit={(e) => {
        e.preventDefault();
        confirm(
          draft.relationships
            ? {
                ...draft,
                relationships: draft.relationships
                  .map((v: string) => v.trim())
                  .filter(Boolean),
              }
            : draft,
        );
      }}
    >
      <Field label="全剧主线">
        <textarea
          rows={5}
          value={draft.storyline}
          onChange={(e) => update("storyline", e.target.value)}
        />
      </Field>
      <Field label="人物关系（每行一项）">
        <textarea
          rows={4}
          value={(draft.relationships || []).join("\n")}
          onChange={(e) => update("relationships", e.target.value.split("\n"))}
        />
      </Field>
      <Field label="世界与背景">
        <textarea
          rows={4}
          value={draft.world}
          onChange={(e) => update("world", e.target.value)}
        />
      </Field>
      <Field label="画风规则">
        <textarea
          rows={3}
          value={draft.visual_rules}
          onChange={(e) => update("visual_rules", e.target.value)}
        />
      </Field>
      <div className="drama-cards">
        {rows(draft.characters).map((c, i) => (
          <div className="drama-card" key={c.character_id}>
            <strong>{c.name}</strong>
            {[
              ["name", "姓名"],
              ["identity_constraints", "身份特征"],
              ["default_wardrobe", "默认服装"],
            ].map(([key, label]) => (
              <Field key={key} label={`${c.name}${label}`}>
                <textarea
                  rows={key === "name" ? 1 : 3}
                  value={c[key]}
                  onChange={(e) =>
                    update(
                      "characters",
                      draft.characters.map((v: RecordData, index: number) =>
                        index === i ? { ...v, [key]: e.target.value } : v,
                      ),
                    )
                  }
                />
              </Field>
            ))}
            <Field label={`${c.name}声线`}>
              <select
                value={c.voice_id}
                onChange={(e) =>
                  update(
                    "characters",
                    draft.characters.map((v: RecordData, index: number) =>
                      index === i ? { ...v, voice_id: e.target.value } : v,
                    ),
                  )
                }
              >
                <option value="female">女声</option>
                <option value="male">男声</option>
                <option value="narrator">旁白</option>
              </select>
            </Field>
          </div>
        ))}
      </div>
      {["scenes", "props"].map((kind) => (
        <div key={kind}>
          <h3>{kind === "scenes" ? "共享场景" : "共享道具"}</h3>
          {rows(draft[kind]).map((entity, i) => (
            <Field key={entity.entity_id} label={entity.name}>
              <textarea
                rows={3}
                value={entity.description}
                onChange={(e) =>
                  update(
                    kind,
                    draft[kind].map((v: RecordData, index: number) =>
                      index === i ? { ...v, description: e.target.value } : v,
                    ),
                  )
                }
              />
            </Field>
          ))}
        </div>
      ))}
      {rows(draft.episodes).map((e, i) => (
        <Field key={e.number} label={`第${e.number}集 · ${e.title}`}>
          <textarea
            rows={4}
            value={e.synopsis}
            onChange={(event) =>
              update(
                "episodes",
                draft.episodes.map((v: RecordData, index: number) =>
                  index === i ? { ...v, synopsis: event.target.value } : v,
                ),
              )
            }
          />
        </Field>
      ))}
      {!!draft.uncertainties?.length && (
        <div>
          <strong>待确认信息</strong>
          <ul>
            {draft.uncertainties.map((v: string, i: number) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </div>
      )}
      <button className="button primary" disabled={busy} type="submit">
        确认全剧规划
      </button>
      <small>修改规划后会重新独立审核，审核通过后再确认。</small>
    </form>
  );
}

const dramaTabs = [
  ["overview", "项目总览"],
  ["episodes", "剧集"],
  ["plan", "全剧规划"],
  ["assets", "共享设定"],
  ["workflow", "制作流程与团队"],
  ["batches", "生成批次"],
  ["results", "成果"],
];

const flowLabels: Record<string, string> = {
  basis: "工作依据",
  scope: "记录范围",
  outlines: "分集大纲数",
  characters: "人物",
  batches: "批次数",
  episodes: "剧集",
  awaiting: "待验收集数",
  approved: "已通过集数",
  units: "制作单元数",
  completed: "已完成单元数",
  delivery_ready: "成片已生成",
  continuity: "连续性检查",
  plan_version: "规划版本",
  episode_numbers: "集号",
  duration_seconds: "时长（秒）",
  outline: "剧情大纲",
  stage: "当前阶段",
  progress: "进度（%）",
  members: "批次剧集",
  storyline: "故事主线",
  review: "审核结果",
  scenes: "场景",
  summary: "摘要",
  world: "世界设定",
  visual_rules: "画风要求",
  relationships: "人物关系",
  opening_state: "开场状态",
  ending_state: "结尾状态",
  depends_on: "依赖集号",
  uncertainties: "待确认事项",
  status: "状态",
  number: "集号",
  character_id: "角色编号",
  entity_id: "资产编号",
};
function flowValue(value: any): any {
  if (Array.isArray(value)) return value.map(flowValue);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([key, entry]) => [
        flowLabels[key] || key,
        ["stage", "status"].includes(key) && typeof entry === "string"
          ? statusNames[entry] || entry
          : flowValue(entry),
      ]),
    );
  return value;
}

function ProductionFlow({
  workflow,
  openProject,
}: {
  workflow?: RecordData;
  openProject: (id: string) => void;
}) {
  return (
    <Panel title="制作流程与团队">
      <p className="muted">
        按实际规划、审核和制作记录查看进度。打开具体任务可查看负责角色、工作输入与结果。
      </p>
      {workflow?.note && <p className="muted">{workflow.note}</p>}
      {!workflow && <p>暂无流程记录，请刷新后重试。</p>}
      {workflow?.evidence?.truncated && (
        <p>仅显示部分执行记录，其他记录仍保留在项目中。</p>
      )}
      {!!workflow?.evidence?.unreadable && (
        <p>部分执行回执无法读取，当前仅展示可核实的记录。</p>
      )}
      <ol className="drama-flow-stages">
        {rows(workflow?.stages).map((stage) => (
          <li key={stage.id}>
            <strong>{stage.title}</strong>
            <span>{statusNames[stage.status] || stage.status}</span>
            {stage.role && <span>{roleName(stage.role)}</span>}
            {stage.output && <Readable value={flowValue(stage.output)} />}
          </li>
        ))}
      </ol>
      <h3>团队工作与结果</h3>
      {!rows(workflow?.tasks).length && (
        <p>暂无执行回执。任务开始后在这里显示工作记录。</p>
      )}
      {rows(workflow?.tasks).map((task) => (
        <details className="drama-task" key={task.id}>
          <summary>
            {task.title} · {roleName(task.role || "orchestrator")} ·{" "}
            {statusNames[task.status] || task.status}
          </summary>
          {task.model && <p>执行模型：{task.model}</p>}
          {(task.finished_at || task.generated_at || task.started_at) && (
            <p>
              记录时间：
              {date(task.finished_at || task.generated_at || task.started_at)}
            </p>
          )}
          <dl>
            <dt>输入</dt>
            <dd>
              <Readable value={flowValue(task.input || "暂无输入记录")} />
            </dd>
            <dt>输出</dt>
            <dd>
              <Readable value={flowValue(task.output || "暂无输出记录")} />
            </dd>
          </dl>
          {!!task.issues?.length && (
            <div role="note">
              {task.issues.map((issue: string, i: number) => (
                <p key={i}>{issue}</p>
              ))}
            </div>
          )}
          {task.project_id && (
            <button
              className="button"
              onClick={() => openProject(task.project_id)}
            >
              打开工作台
            </button>
          )}
        </details>
      ))}
    </Panel>
  );
}

export function DramaDetail({
  id,
  onBack,
  openEpisode,
  activeTab,
  onTab,
}: {
  id: string;
  onBack: () => void;
  openEpisode: (id: string, tab?: string) => void;
  activeTab: string;
  onTab: (tab: string) => void;
}) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["drama", id],
    queryFn: () => request("/dramas/" + id),
    refetchInterval: 5000,
  });
  const [selected, setSelected] = useState<number[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [feedback, setFeedback] = useState(""),
    [reviewing, setReviewing] = useState<RecordData | null>(null),
    [gpuMinutes, setGpuMinutes] = useState(120),
    [wallMinutes, setWallMinutes] = useState(180),
    [attempts, setAttempts] = useState(3);
  const pending = useRef(false);
  const requestCache = useRef<Record<string, string>>({});
  const p = query.data;
  const tab = dramaTabs.some(([key]) => key === activeTab)
    ? activeTab
    : "overview";
  function setTab(value: string) {
    setError("");
    onTab(value);
  }
  async function operation(path: string, body: RecordData) {
    if (!p || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    const signature = JSON.stringify({ path, body, revision: p.revision });
    const requestId = (requestCache.current[signature] ||= crypto.randomUUID());
    try {
      await request("/dramas/" + id + "/" + path, {
        ...body,
        expected_revision: p.revision,
        request_id: requestId,
      });
      setReviewing(null);
      setSelected([]);
      await client.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  if (!p)
    return (
      <>
        <ErrorNotice error={query.error} />
        <Loading />
      </>
    );
  return (
    <>
      <PageHeader
        title={p.title}
        parent={
          <button
            className="icon-button"
            onClick={onBack}
            aria-label="返回项目列表"
          >
            <ArrowLeft size={20} />
          </button>
        }
        meta={
          <span>
            {p.episode_count}集 · 每集{p.duration_seconds}秒 ·{" "}
            {p.format === "motion_comic" ? "动态漫" : "H3视频剧"} ·{" "}
            {statusNames[p.status] || p.status}
          </span>
        }
      />
      <ErrorNotice error={error || query.error} />
      <nav className="drama-tabs" aria-label="整剧工作区">
        {dramaTabs.map(([key, label]) => (
          <button
            key={key}
            className={"button " + (tab === key ? "primary" : "")}
            aria-pressed={tab === key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "overview" && (
        <>
          <Panel title="项目总览">
            <p>全剧规划 → 选集制作 → 场景与镜头 → 逐集验收</p>
            <p>
              规划{p.plan_version ? `已确认 v${p.plan_version}` : "尚未确认"}
              ；已建立{rows(p.episodes).length} / {p.episode_count}集，
              {
                rows(p.episodes).filter((e) =>
                  ["approved", "awaiting_final"].includes(e.status),
                ).length
              }
              集已出片。
            </p>
            <div className="drama-actions">
              <button
                className="button primary"
                onClick={() => setTab("workflow")}
              >
                打开制作流程
              </button>
              <button className="button" onClick={() => setTab("plan")}>
                查看全剧规划
              </button>
              {!!p.plan_version && (
                <button className="button" onClick={() => setTab("episodes")}>
                  选择剧集制作
                </button>
              )}
            </div>
          </Panel>
          {p.error && (
            <Panel title="需要处理">
              <p>{p.error}</p>
              <p>
                {p.plan_review?.issues?.length || 0}
                项规划审核问题，草稿和已完成成果已保留。
              </p>
              <button className="button" onClick={() => setTab("plan")}>
                处理规划问题
              </button>
            </Panel>
          )}
          {!!rows(p.episodes).length && (
            <Panel title="剧集进度">
              <div className="drama-cards">
                {rows(p.episodes).map((e) => (
                  <article className="drama-card" key={e.id}>
                    <strong>{e.title}</strong>
                    <p>
                      {statusNames[e.status] || e.status} · {e.progress}%
                    </p>
                    <p>{e.error || e.outline?.synopsis}</p>
                    <button
                      className="button"
                      onClick={() =>
                        openEpisode(
                          e.id,
                          e.duration_seconds > 90 ? "workflow" : "team",
                        )
                      }
                    >
                      查看剧集流程
                    </button>
                  </article>
                ))}
              </div>
            </Panel>
          )}
        </>
      )}
      {tab === "workflow" && (
        <>
          <ProductionFlow
            workflow={p.workflow}
            openProject={(projectId) => {
              const episode = rows(p.episodes).find((e) => e.id === projectId);
              openEpisode(
                projectId,
                episode?.duration_seconds > 90 ? "workflow" : "team",
              );
            }}
          />
          <div className="drama-actions">
            <button className="button" onClick={() => setTab("plan")}>
              查看全剧规划
            </button>
            <button className="button" onClick={() => setTab("episodes")}>
              查看剧集
            </button>
          </div>
        </>
      )}
      {!!p.dependency_impacts?.length && (
        <Panel title="前集版本已变化">
          {p.dependency_impacts.map((impact: RecordData) => (
            <p key={impact.episode_id + impact.dependency_number}>
              第{impact.number}集沿用的第{impact.dependency_number}
              集版本已有更新，原成果保留，请查看承接是否需要调整。
            </p>
          ))}
        </Panel>
      )}
      {tab === "plan" && (
        <Panel title="全剧规划">
          {p.workflow?.active_job && (
            <p role="status">规划任务正在执行，请等待本轮完成后再提交确认。</p>
          )}
          {p.error && <p role="alert">{p.error}</p>}
          {!!p.plan_review?.issues?.length && (
            <details open>
              <summary>规划审核问题（{p.plan_review.issues.length}项）</summary>
              {p.plan_review.issues.map((issue: string, i: number) => (
                <p key={i}>{issue}</p>
              ))}
            </details>
          )}
          {!p.plan_version && (
            <div className="drama-actions">
              {p.status === "paused" || p.status === "blocked" ? (
                <button
                  className="button"
                  disabled={busy || !!p.workflow?.active_job}
                  onClick={() =>
                    void operation("actions", { action: "resume" })
                  }
                >
                  继续规划
                </button>
              ) : (
                !["awaiting_plan", "cancelled"].includes(p.status) && (
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() =>
                      void operation("actions", { action: "pause" })
                    }
                  >
                    暂停规划
                  </button>
                )
              )}
              {!["cancelled", "awaiting_plan"].includes(p.status) && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    void operation("actions", { action: "cancel" })
                  }
                >
                  取消规划
                </button>
              )}
            </div>
          )}
          {p.draft_plan ? (
            <PlanEditor
              key={JSON.stringify(p.draft_plan)}
              document={p.draft_plan}
              busy={busy || !!p.workflow?.active_job}
              confirm={(value) => void operation("confirm", { plan: value })}
            />
          ) : (
            <p>
              策划与编剧正在准备全剧设定和分集大纲，独立审核后将在这里显示。
            </p>
          )}
          {p.plan_review && (
            <p>
              {p.plan_review.passed
                ? "全剧规划已通过独立审核"
                : "规划需要修改后重新审核"}
            </p>
          )}
          {!!p.affected_episodes?.length && (
            <p>
              新设定影响第
              {p.affected_episodes.map((e: RecordData) => e.number).join("、")}
              集；已有剧集继续保留原引用版本。
            </p>
          )}
        </Panel>
      )}
      {tab === "episodes" && !p.plan_version && (
        <Panel title="剧集尚未建立">
          <Empty title="先确认全剧规划">
            审核通过并确认规划后，系统才会建立剧集并开放选集制作。
          </Empty>
          <button className="button primary" onClick={() => setTab("plan")}>
            查看全剧规划
          </button>
        </Panel>
      )}
      {tab === "episodes" && !!p.plan_version && (
        <>
          <Panel title="选择本次制作的剧集">
            <div className="drama-actions">
              <button
                className="button"
                onClick={() =>
                  setSelected(
                    rows(p.episodes)
                      .filter((e) => e.status === "draft")
                      .map((e) => e.episode_number),
                  )
                }
              >
                选择全部未生成剧集
              </button>
              <button
                className="button primary"
                disabled={busy || !selected.length}
                onClick={() =>
                  void operation("generate", {
                    episode_numbers: selected,
                    budget: {
                      gpu_minutes: gpuMinutes,
                      wall_minutes: wallMinutes,
                      max_attempts: attempts,
                    },
                  })
                }
              >
                生成所选 {selected.length} 集
              </button>
              <button
                className="button"
                disabled={busy || p.status === "cancelled"}
                onClick={() =>
                  void operation("actions", {
                    action: p.pause_requested ? "resume" : "pause",
                  })
                }
              >
                {p.pause_requested ? "继续整剧制作" : "暂停整剧制作"}
              </button>
            </div>
            <div className="drama-fields">
              <Field label="每单元GPU预算（卡分钟）">
                <input
                  type="number"
                  min={5}
                  max={1440}
                  value={gpuMinutes}
                  onChange={(e) => setGpuMinutes(Number(e.target.value))}
                />
              </Field>
              <Field label="每单元时间上限（分钟）">
                <input
                  type="number"
                  min={10}
                  max={1440}
                  value={wallMinutes}
                  onChange={(e) => setWallMinutes(Number(e.target.value))}
                />
              </Field>
              <Field label="最多尝试次数">
                <input
                  type="number"
                  min={1}
                  max={5}
                  value={attempts}
                  onChange={(e) => setAttempts(Number(e.target.value))}
                />
              </Field>
            </div>
            <p className="muted">
              按集号排队，失败集保留进度，其他无依赖剧集继续。长集按制作单元累计预算，规划完成后显示整集授权上限。
            </p>
          </Panel>
          <div className="drama-cards">
            {rows(p.episodes).map((e) => (
              <article className="drama-card" key={e.id}>
                <label className="drama-episode-select">
                  <input
                    type="checkbox"
                    aria-label={`选择第${e.episode_number}集`}
                    checked={selected.includes(e.episode_number)}
                    disabled={e.status !== "draft" || busy}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked
                          ? [...selected, e.episode_number]
                          : selected.filter((n) => n !== e.episode_number),
                      )
                    }
                  />
                  <strong>{e.title}</strong>
                </label>
                <p>{e.outline.synopsis}</p>
                <p>
                  {statusNames[e.status] || e.status} · {e.progress}% · 设定v
                  {e.plan_version}
                </p>
                {!!e.error && <p role="alert">{e.error}</p>}
                <div className="drama-actions">
                  <button className="button" onClick={() => openEpisode(e.id)}>
                    进入剧集
                  </button>
                  <button
                    className="button"
                    onClick={() =>
                      openEpisode(
                        e.id,
                        e.duration_seconds > 90 ? "workflow" : "team",
                      )
                    }
                  >
                    查看剧集流程
                  </button>
                  {[
                    "queued",
                    "producing",
                    "scripting",
                    "waiting_remote",
                    "waiting_dependency",
                  ].includes(e.status) && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        void operation("actions", {
                          action: "pause",
                          episode_id: e.id,
                        })
                      }
                    >
                      暂停
                    </button>
                  )}
                  {[
                    "paused",
                    "blocked",
                    "failed",
                    "waiting_dependency",
                  ].includes(e.status) && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        void operation("actions", {
                          action: e.status === "blocked" ? "retry" : "resume",
                          episode_id: e.id,
                        })
                      }
                    >
                      继续此集
                    </button>
                  )}
                  {![
                    "approved",
                    "cancelled",
                    "draft",
                    "awaiting_final",
                  ].includes(e.status) && (
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        void operation("actions", {
                          action: "cancel",
                          episode_id: e.id,
                        })
                      }
                    >
                      取消此集
                    </button>
                  )}
                  {e.status === "awaiting_final" && (
                    <button
                      className="button primary"
                      onClick={() => {
                        setReviewing(e);
                        setFeedback("");
                      }}
                    >
                      观看与验收
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        </>
      )}
      {tab === "assets" && (
        <Panel title="全剧共享设定">
          <p>全剧角色使用固定身份和声线；生成素材按设定版本引用。</p>
          {!p.plan_version && (
            <>
              <p>设定尚未确认。以下为规划草稿，确认后按版本供剧集引用。</p>
              <button className="button" onClick={() => setTab("plan")}>
                查看全剧规划
              </button>
            </>
          )}
          <div className="drama-cards">
            {Object.entries(p.shared_assets || {}).flatMap(
              ([version, assets]) =>
                Object.values(assets as RecordData).map((asset: any) => (
                  <div
                    className="drama-card"
                    key={version + asset.kind + asset.entity_id}
                  >
                    <img
                      style={{
                        width: "100%",
                        maxHeight: 300,
                        objectFit: "contain",
                      }}
                      src={mediaUrl(asset.receipt.path)}
                      alt={`${asset.entity_id} · 设定v${version}参考图`}
                    />
                    <p>
                      {asset.entity_id} · 设定v{version} ·
                      已通过视觉审核（自动选择）
                    </p>
                  </div>
                )),
            )}
          </div>
          {(rows(p.plans).at(-1)?.document || p.draft_plan) && (
            <>
              {rows(
                (rows(p.plans).at(-1)?.document || p.draft_plan).characters,
              ).map((c: RecordData) => (
                <div className="drama-card" key={c.character_id}>
                  <strong>{c.name}</strong>
                  <p>{c.identity_constraints}</p>
                  <p>
                    {c.default_wardrobe} · 声线 {c.voice_id}
                  </p>
                </div>
              ))}
              {["scenes", "props"].map((kind) => (
                <div key={kind}>
                  <h3>{kind === "scenes" ? "场景" : "道具"}</h3>
                  {rows(
                    (rows(p.plans).at(-1)?.document || p.draft_plan)[kind],
                  ).map((v) => (
                    <p key={v.entity_id}>
                      {v.name}：{v.description}
                    </p>
                  ))}
                </div>
              ))}
            </>
          )}
        </Panel>
      )}
      {tab === "batches" && (
        <Panel title="生成批次">
          {!rows(p.batches).length && (
            <Empty
              title="尚无生成批次"
              action={
                <button
                  className="button"
                  onClick={() => setTab(p.plan_version ? "episodes" : "plan")}
                >
                  {p.plan_version ? "选择剧集制作" : "查看全剧规划"}
                </button>
              }
            >
              确认规划后，在剧集页选择一集或多集启动制作。
            </Empty>
          )}
          {rows(p.batches).map((b) => (
            <div className="drama-card" key={b.id}>
              <strong>
                第{b.episode_numbers.join("、")}集 · 设定v{b.plan_version}
              </strong>
              <p>
                {b.members
                  .map(
                    (e: RecordData) =>
                      `第${e.number}集 ${statusNames[e.status] || e.status}`,
                  )
                  .join("；")}
              </p>
              <p>
                {statusNames[b.status] || b.status} · {b.progress || 0}% · 累计
                {b.spent_gpu_minutes || 0}卡分钟；
                {b.budget_scope === "per_unit_pending_scene_plan"
                  ? "分段后确定整批授权上限"
                  : `授权上限${b.authorized_gpu_minutes}卡分钟`}
                。
              </p>
              <div className="drama-actions">
                {["pause", "resume", "retry", "cancel"].map((action) => (
                  <button
                    className="button"
                    disabled={busy}
                    key={action}
                    onClick={() =>
                      void operation("actions", { action, batch_id: b.id })
                    }
                  >
                    {action === "pause"
                      ? "暂停批次"
                      : action === "resume"
                        ? "继续批次"
                        : action === "retry"
                          ? "重试失败集"
                          : "取消批次"}
                  </button>
                ))}
              </div>
              <a
                className="button"
                href={`/api/factory/dramas/${id}/download?batch_id=${b.id}`}
                download
              >
                下载此批成果
              </a>
            </div>
          ))}
        </Panel>
      )}
      {tab === "results" && (
        <Panel title="逐集成果">
          {!rows(p.episodes).length && (
            <Empty
              title="暂无剧集成果"
              action={
                <button
                  className="button"
                  onClick={() => setTab(p.plan_version ? "episodes" : "plan")}
                >
                  {p.plan_version ? "选择剧集制作" : "查看全剧规划"}
                </button>
              }
            >
              剧集制作后在这里观看、验收及下载成片。
            </Empty>
          )}
          {!!rows(p.episodes).length && (
            <a
              className="button"
              href={`/api/factory/dramas/${id}/download`}
              download
            >
              按集打包下载
            </a>
          )}
          {rows(p.episodes).map((e) => (
            <div className="drama-card" key={e.id}>
              <strong>{e.title}</strong>
              {e.delivery ? (
                <>
                  <video
                    className="drama-video"
                    controls
                    preload="metadata"
                    src={mediaUrl(
                      e.delivery.video_url || e.delivery.final?.path,
                    )}
                  />
                  <div className="drama-actions">
                    {[
                      ["video_url", "成片"],
                      ["clean_url", "无字幕成片"],
                      ["subtitle_url", "字幕"],
                      ["manifest_url", "制作清单"],
                    ].map(
                      ([key, label]) =>
                        e.delivery[key] && (
                          <a
                            className="button"
                            key={key}
                            href={mediaUrl(e.delivery[key]) || ""}
                            download
                          >
                            {label}
                          </a>
                        ),
                    )}
                  </div>
                </>
              ) : (
                <p>{e.error || statusNames[e.status] || "尚未生成"}</p>
              )}
            </div>
          ))}
        </Panel>
      )}
      {reviewing && (
        <Modal
          id="review-episode"
          title={reviewing.title + " · 终片验收"}
          onClose={() => setReviewing(null)}
        >
          <video
            className="drama-video"
            controls
            src={mediaUrl(
              reviewing.delivery.video_url || reviewing.delivery.final?.path,
            )}
          />
          <Field label="局部修改或剪辑意见">
            <textarea
              rows={4}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
          </Field>
          <div className="drama-actions">
            <button
              className="button primary"
              disabled={busy}
              onClick={() =>
                void operation("actions", {
                  action: "approve",
                  episode_id: reviewing.id,
                  expected_episode_revision: reviewing.revision,
                })
              }
            >
              通过本集
            </button>
            {["revise", "reedit"].map((action) => (
              <button
                className="button"
                key={action}
                disabled={busy || feedback.trim().length < 2}
                onClick={() =>
                  void operation("actions", {
                    action,
                    episode_id: reviewing.id,
                    feedback,
                    expected_episode_revision: reviewing.revision,
                  })
                }
              >
                {action === "revise" ? "局部修改" : "重新剪辑"}
              </button>
            ))}
          </div>
        </Modal>
      )}
    </>
  );
}

export function LongEpisodeDetail({
  episode,
  openProject,
  activeTab,
  onTab,
}: {
  episode: RecordData;
  openProject: (id: string, tab?: string) => void;
  activeTab: string;
  onTab: (tab: string) => void;
}) {
  const query = useQuery({
    queryKey: ["long-episode", episode.id],
    queryFn: () =>
      request(`/dramas/${episode.drama_id}/episodes/${episode.id}`),
    refetchInterval: 5000,
  });
  const client = useQueryClient();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [feedback, setFeedback] = useState(""),
    [unitId, setUnitId] = useState("");
  const pending = useRef(false),
    requestIds = useRef<Record<string, string>>({});
  const p = query.data;
  async function action(kind: string) {
    if (!p || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    const body = {
      action: kind,
      episode_id: p.id,
      expected_episode_revision: p.revision,
      feedback: feedback || undefined,
      unit_id: unitId || undefined,
      expected_revision: p.drama_revision,
    };
    const key = JSON.stringify(body),
      requestId = (requestIds.current[key] ||= crypto.randomUUID());
    try {
      await request(`/dramas/${p.drama_id}/actions`, {
        ...body,
        request_id: requestId,
      });
      await client.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  if (!p)
    return (
      <>
        <ErrorNotice error={query.error} />
        <Loading />
      </>
    );
  return (
    <>
      <PageHeader
        title={p.title}
        parent={
          <button
            className="icon-button"
            aria-label="返回整部剧"
            onClick={() => openProject(p.drama_id)}
          >
            <ArrowLeft size={20} />
          </button>
        }
        meta={
          <span>
            目标{p.duration_seconds}秒 · {statusNames[p.status] || p.status} ·{" "}
            {p.progress}% · 设定v{p.plan_version}
          </span>
        }
      />
      <ErrorNotice error={error || p.error || query.error} />
      <nav className="drama-tabs" aria-label="剧集工作区">
        {[
          ["overview", "剧集总览"],
          ["workflow", "制作流程与团队"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={"button " + (activeTab === key ? "primary" : "")}
            aria-pressed={activeTab === key}
            onClick={() => onTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      {activeTab === "workflow" ? (
        <>
          <ProductionFlow
            workflow={p.workflow}
            openProject={(id) => openProject(id, "team")}
          />
          <Panel title="单元团队工作台">
            {!rows(p.units).length && (
              <p>
                场景规划完成后将在这里显示制作单元。可先展开上方团队任务查看规划结果。
              </p>
            )}
            {rows(p.units).map((u) => (
              <article className="drama-card" key={u.id}>
                <strong>{u.title}</strong>
                <p>
                  {statusNames[u.status] || u.status} · {u.progress}%
                </p>
                <button
                  className="button"
                  onClick={() => openProject(u.id, "team")}
                >
                  查看单元团队
                </button>
              </article>
            ))}
          </Panel>
        </>
      ) : (
        <>
          <Panel title="剧集进度">
            <p>{p.outline.synopsis}</p>
            <p>
              {rows(p.units).filter((u) => u.status === "unit_ready").length} /{" "}
              {rows(p.units).length} 个制作单元已完成；累计
              {p.budget?.spent_gpu_minutes || 0}卡分钟。
            </p>
            {p.total_authorized_gpu_minutes && (
              <p>
                本集授权上限：{p.total_authorized_gpu_minutes}卡分钟；每单元最多
                {p.budget.max_attempts}次尝试。
              </p>
            )}
            <div className="drama-actions">
              {["queued", "producing", "scripting", "waiting_remote"].includes(
                p.status,
              ) && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void action("pause")}
                >
                  暂停此集
                </button>
              )}
              {["paused", "blocked", "failed", "waiting_dependency"].includes(
                p.status,
              ) && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void action("resume")}
                >
                  继续此集
                </button>
              )}
              {!["draft", "cancelled", "approved", "awaiting_final"].includes(
                p.status,
              ) && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void action("cancel")}
                >
                  取消此集
                </button>
              )}
            </div>
          </Panel>
          {p.delivery && (
            <Panel title="本集成片">
              <video
                className="drama-video"
                controls
                preload="metadata"
                src={mediaUrl(p.delivery.video_url)}
              />
              <div className="drama-actions">
                {[
                  ["video_url", "下载成片"],
                  ["clean_url", "无字幕成片"],
                  ["subtitle_url", "字幕"],
                  ["manifest_url", "制作清单"],
                ].map(
                  ([key, label]) =>
                    p.delivery[key] && (
                      <a
                        className="button"
                        download
                        key={key}
                        href={mediaUrl(p.delivery[key]) || ""}
                      >
                        {label}
                      </a>
                    ),
                )}
              </div>
            </Panel>
          )}
          {["awaiting_final", "blocked", "failed"].includes(p.status) && (
            <Panel title="验收与定向修改">
              <Field label="修改范围">
                <select
                  value={unitId}
                  onChange={(e) => setUnitId(e.target.value)}
                >
                  <option value="">根据意见定位单元</option>
                  {rows(p.units).map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.title}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="局部修改或重新剪辑意见">
                <textarea
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  rows={4}
                />
              </Field>
              <div className="drama-actions">
                {p.status === "awaiting_final" && (
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() => void action("approve")}
                  >
                    通过本集
                  </button>
                )}
                {(p.status === "awaiting_final"
                  ? ["revise", "reedit"]
                  : ["revise"]
                ).map((kind) => (
                  <button
                    className="button"
                    key={kind}
                    disabled={busy || feedback.trim().length < 2}
                    onClick={() => void action(kind)}
                  >
                    {kind === "revise" ? "局部修改" : "重新剪辑"}
                  </button>
                ))}
              </div>
            </Panel>
          )}
          <Panel title="场景与制作单元">
            <button className="button" onClick={() => onTab("workflow")}>
              打开制作流程
            </button>
            {!rows(p.units).length && (
              <p>场景规划尚未完成，可打开制作流程查看编剧、审稿与执行情况。</p>
            )}
            <div className="drama-cards">
              {rows(p.units).map((u) => (
                <article className="drama-card" key={u.id}>
                  <strong>{u.title}</strong>
                  <p>
                    {u.duration_seconds}秒 · {statusNames[u.status] || u.status}{" "}
                    · {u.progress}%
                  </p>
                  <p>{u.outline.synopsis}</p>
                  <small>镜头编号：{u.global_prefix}-S01 …</small>
                  <button className="button" onClick={() => openProject(u.id)}>
                    进入镜头工作台
                  </button>
                  <button
                    className="button"
                    onClick={() => openProject(u.id, "team")}
                  >
                    查看单元团队
                  </button>
                </article>
              ))}
            </div>
          </Panel>
        </>
      )}
    </>
  );
}
