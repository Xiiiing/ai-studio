import { useEffect, useState, type ReactNode } from "react";
import {
  Badge,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Panel,
  Readable,
  WorkProgress,
} from "./ui";
import {
  date,
  mediaUrl,
  names,
  request,
  roleName,
  rows,
  type RecordData,
} from "./api";
import { SelectionGroup } from "./SelectionGroup";
import "./production-workspace.css";

export const defaultAutomationBudget = {
  gpu_minutes: 120,
  wall_minutes: 180,
  max_attempts: 3,
};
export type AutomationConfig = {
  enabled: boolean;
  episode_numbers: number[];
  budget: typeof defaultAutomationBudget;
};

/** Used both before first start and when explicitly taking over a manual project. */
export function AutomationSettings({
  count,
  value,
  onChange,
  disabled = false,
  budgetDisabled = false,
  minimumBudget,
}: {
  count: number;
  value: AutomationConfig;
  onChange: (value: AutomationConfig) => void;
  disabled?: boolean;
  budgetDisabled?: boolean;
  minimumBudget?: AutomationConfig["budget"];
}) {
  const [scope, setScope] = useState<"all" | "selected">(
    value.episode_numbers.length === count ? "all" : "selected",
  );
  const numbers = Array.from({ length: Math.max(0, count) }, (_, i) => i + 1);
  useEffect(() => {
    if (
      scope === "all" &&
      (value.episode_numbers.length !== numbers.length ||
        numbers.some(
          (number, index) => value.episode_numbers[index] !== number,
        ))
    )
      onChange({ ...value, episode_numbers: numbers });
  }, [count, scope, value, onChange]);
  const updateBudget = (
    key: keyof AutomationConfig["budget"],
    amount: number,
  ) => onChange({ ...value, budget: { ...value.budget, [key]: amount } });
  return (
    <div className="automation-settings">
      <fieldset disabled={disabled}>
        <legend>本次制作范围</legend>
        <SelectionGroup className="automation-scope" value={scope}>
          {(["all", "selected"] as const).map((kind) => (
            <button
              key={kind}
              type="button"
              aria-pressed={scope === kind}
              onClick={() => {
                setScope(kind);
                onChange({
                  ...value,
                  episode_numbers:
                    kind === "all" ? numbers : value.episode_numbers,
                });
              }}
            >
              {kind === "all" ? `全剧 ${count} 集` : "指定集数"}
            </button>
          ))}
        </SelectionGroup>
        {scope === "selected" && (
          <div className="automation-episode-options">
            {numbers.map((number) => (
              <label key={number}>
                <input
                  type="checkbox"
                  checked={value.episode_numbers.includes(number)}
                  onChange={(event) =>
                    onChange({
                      ...value,
                      episode_numbers: event.target.checked
                        ? [...value.episode_numbers, number].sort(
                            (a, b) => a - b,
                          )
                        : value.episode_numbers.filter((n) => n !== number),
                    })
                  }
                />
                第 {number} 集
              </label>
            ))}
          </div>
        )}
      </fieldset>
      <fieldset disabled={disabled || budgetDisabled}>
        <legend>每个制作单元的授权上限</legend>
        <div className="drama-fields">
          <Field label="GPU 预算（卡分钟 / 单元）">
            <input
              type="number"
              min={Math.max(5, minimumBudget?.gpu_minutes || 0)}
              max={1440}
              required
              value={value.budget.gpu_minutes}
              onChange={(event) =>
                updateBudget("gpu_minutes", Number(event.target.value))
              }
            />
          </Field>
          <Field label="时间上限（分钟 / 单元）">
            <input
              type="number"
              min={Math.max(10, minimumBudget?.wall_minutes || 0)}
              max={1440}
              required
              value={value.budget.wall_minutes}
              onChange={(event) =>
                updateBudget("wall_minutes", Number(event.target.value))
              }
            />
          </Field>
          <Field label="最多尝试次数 / 单元">
            <input
              type="number"
              min={Math.max(1, minimumBudget?.max_attempts || 0)}
              max={5}
              required
              value={value.budget.max_attempts}
              onChange={(event) =>
                updateBudget("max_attempts", Number(event.target.value))
              }
            />
          </Field>
        </div>
        <p className="muted">
          预算按制作单元计量，并非整剧总额。长集拆分后显示累计授权上限；达到上限会暂停等待处理。
        </p>
      </fieldset>
    </div>
  );
}

const stateLabels: Record<string, string> = {
  disabled: "分步制作",
  planning: "AI 正在规划",
  reviewing: "独立审核中",
  producing: "AI 连续制作中",
  awaiting_final: "等待成片验收",
  completed: "已验收完成",
  paused: "已暂停",
  blocked: "需要处理",
  running: "执行中",
  pending: "尚未开始",
  succeeded: "已完成",
  human: "等待人工处理",
  waiting: "等待处理",
  returned: "返修中",
  exhausted: "返修额度用尽",
  ...names,
};
const statusLabel = (status: string) =>
  stateLabels[status] || status || "尚未开始";

const stoppedProductionCodes = new Set([
  "retry_limit",
  "production_recovery_stop",
  "production_recovery_request_upstream",
]);
const stopReasons: Record<string, string> = {
  retry_limit: "本制作对象的授权尝试次数已用尽。",
  recovery_cycle_limit: "已达到本次自主修订上限。",
  recovery_has_no_new_directed_change:
    "现有修订已经尝试，系统尚无可执行的新修复方案。",
  approved_inputs_require_upstream_revision: "需要核对上游材料或当前媒介能力。",
  actual_reference_evidence_missing: "缺少可核验的实际参考素材。",
  first_frame_requires_independent_upstream_repair:
    "首帧未通过独立审核，需要先处理首帧问题。",
  first_frame_review_unverified: "当前首帧缺少有效的独立审核证据。",
};

/** Opened on demand; viewing an artifact never dispatches production work. */
function EpisodeArtifacts({
  dramaId,
  episode,
}: {
  dramaId: string;
  episode: RecordData;
}) {
  const [snapshot, setSnapshot] = useState<RecordData>();
  const [error, setError] = useState("");
  const [tab, setTab] = useState("script");
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const value = await request(
          episode.duration_seconds > 90
            ? `/dramas/${dramaId}/episodes/${episode.id}`
            : `/projects/${episode.id}/workspace`,
        );
        if (!cancelled) {
          setSnapshot(value.project || value);
          setError("");
        }
      } catch (failure) {
        if (!cancelled) setError((failure as Error).message);
      }
    };
    void load();
    const interval = setInterval(() => void load(), 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [dramaId, episode.id, episode.duration_seconds]);
  const units = rows(snapshot?.units);
  const shots = rows(snapshot?.shots).concat(
    units.flatMap((unit) => rows(unit.shots)),
  );
  const script =
    snapshot?.script || snapshot?.screenplay_draft || snapshot?.scene_plan;
  return (
    <section
      className="production-episode-artifacts"
      aria-label={`第 ${episode.episode_number} 集当前产物`}
    >
      <SelectionGroup className="automation-scope" value={tab}>
        {[
          ["script", "剧本与场景"],
          ["materials", "分镜与素材"],
          ["delivery", "成片与质检"],
        ].map(([key, label]) => (
          <button
            type="button"
            key={key}
            aria-pressed={tab === key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </SelectionGroup>
      <ErrorNotice error={error} />
      {!snapshot && !error && <Loading />}
      {snapshot &&
        (tab === "script" ? (
          script ? (
            <Readable value={script} />
          ) : (
            <p className="muted">尚无已保存剧本或场景方案。</p>
          )
        ) : tab === "materials" ? (
          shots.length ? (
            <div className="production-asset-grid">
              {shots.map((shot, index) => (
                <figure key={shot.id || index}>
                  {shot.image_url && (
                    <img
                      src={mediaUrl(shot.image_url)}
                      alt={`分镜 ${shot.id || index + 1}`}
                    />
                  )}
                  {shot.video_url && (
                    <video
                      controls
                      preload="metadata"
                      src={mediaUrl(shot.video_url)}
                    />
                  )}
                  <figcaption>
                    {shot.id} ·{" "}
                    {shot.dramatic_beat ||
                      shot.description ||
                      shot.action ||
                      statusLabel(shot.status)}
                  </figcaption>
                </figure>
              ))}
            </div>
          ) : (
            <p className="muted">
              尚无可查看的分镜素材，制作中的内容会在保存后显示。
            </p>
          )
        ) : (
          <>
            <Readable
              value={
                snapshot.transition_review ||
                snapshot.delivery?.qa ||
                (snapshot.delivery
                  ? "成片已生成，仍需人工验收。"
                  : "尚无成片或质检结果。")
              }
            />
            {units.length > 0 && (
              <p className="muted">
                已完成{" "}
                {units.filter((unit) => unit.status === "unit_ready").length} /{" "}
                {units.length} 个制作单元。
              </p>
            )}
          </>
        ))}
    </section>
  );
}

export function ProductionWorkspace({
  project,
  busy,
  onOperation,
  onReview,
  planEditor,
  workflow,
  onTab,
}: {
  project: RecordData;
  busy: boolean;
  onOperation: (path: string, body: RecordData) => void | Promise<void>;
  onReview: (episode: RecordData) => void;
  planEditor?: ReactNode;
  workflow?: ReactNode;
  onTab: (tab: string) => void;
}) {
  const p = project;
  const state = p.automation_state || {};
  const automated = !!p.automation?.enabled;
  const episodes = rows(p.episodes);
  const nodes = rows(p.workflow?.graph?.nodes);
  const stages = rows(p.workflow?.stages);
  const active = p.workflow?.graph?.active_node;
  const [selected, setSelected] = useState("");
  const [configure, setConfigure] = useState(false);
  const [viewingEpisode, setViewingEpisode] = useState("");
  const [planningFeedback, setPlanningFeedback] = useState("");
  const [config, setConfig] = useState<AutomationConfig>(() => ({
    enabled: true,
    episode_numbers:
      p.automation?.episode_numbers ||
      Array.from({ length: p.episode_count || 0 }, (_, i) => i + 1),
    budget: { ...defaultAutomationBudget, ...(p.automation?.budget || {}) },
  }));
  const selectedId = nodes.some((node) => node.id === selected)
    ? selected
    : active || nodes[0]?.id;
  const node = nodes.find((entry) => entry.id === selectedId);
  const stage = stages.find((entry) => entry.id === selectedId);
  const tasks = rows(p.workflow?.tasks);
  const activity = tasks
    .filter((task) => !selectedId || task.stage_id === selectedId)
    .slice(0, 8);
  const awaiting = episodes.filter(
    (episode) => episode.status === "awaiting_final",
  );
  const issues = rows(state.attention);
  const blockedEpisodes = episodes.filter((episode) =>
    ["blocked", "failed", "paused", "waiting_dependency"].includes(
      episode.status,
    ),
  );
  const paused =
    !!p.pause_requested || state.status === "paused" || p.status === "paused";
  const planNeedsAttention =
    p.status === "awaiting_plan" ||
    paused ||
    (!p.plan_version && p.status === "blocked") ||
    p.plan_phase === "exhausted" ||
    issues.some((issue) =>
      ["review_required", "plan_uncertainty", "planning_blocked"].includes(
        issue.code,
      ),
    );
  const hasAttention =
    issues.length > 0 ||
    !!p.error ||
    planNeedsAttention ||
    blockedEpisodes.length > 0 ||
    awaiting.length > 0;
  const completed = episodes.filter((episode) =>
    ["awaiting_final", "approved"].includes(episode.status),
  ).length;
  const plan = p.draft_plan || rows(p.plans).at(-1)?.document;
  const assets = Object.entries(p.shared_assets || {}).flatMap(
    ([version, values]) =>
      Object.values(values as RecordData).map((asset: any) => ({
        ...asset,
        version,
      })),
  );
  const targetNumbers =
    state.episode_numbers || p.automation?.episode_numbers || [];
  const invoke = (body: RecordData) => void onOperation("actions", body);

  return (
    <div className="production-workspace">
      <section className="production-command" aria-label="连续制作状态">
        <div className="production-command-copy">
          <span className="eyebrow">
            {automated ? "AI 连续制作" : "分步制作项目"}
          </span>
          <h2>
            {state.reason ||
              (paused
                ? "制作已暂停"
                : automated
                  ? statusLabel(state.status || p.status)
                  : "可在这里开启 AI 连续制作")}
          </h2>
          <p>
            {automated
              ? `已授权${targetNumbers.length ? `第 ${targetNumbers.join("、")} 集` : "指定范围"}。AI 完成规划、剧本、素材与质检，成片由你验收。`
              : "保留已有成果和执行方式。开启后，AI 接续授权范围内尚未完成的工作。"}
          </p>
          {automated && p.automation?.production_recovery?.enabled && (
            <small>
              自主返修已启用：系统在剩余次数和预算内定向修订、复核并继续。
            </small>
          )}
          <WorkProgress
            label="已生成成片"
            completed={completed}
            total={p.episode_count}
          />
          <small>
            已出片 {completed} / {p.episode_count} 集 · 已验收{" "}
            {episodes.filter((episode) => episode.status === "approved").length}{" "}
            集
          </small>
        </div>
        <div className="production-command-actions">
          {automated &&
            paused &&
            !p.automation?.production_recovery?.enabled && (
              <button
                className="button"
                disabled={busy || p.status === "cancelled"}
                onClick={() => invoke({ action: "enable_autonomous_repair" })}
              >
                启用自主返修
              </button>
            )}
          {(!automated || paused || state.status === "blocked") && (
            <button
              className="button primary"
              disabled={busy || p.status === "cancelled"}
              onClick={() => {
                setConfig({
                  enabled: true,
                  episode_numbers:
                    p.automation?.episode_numbers || config.episode_numbers,
                  budget: {
                    ...defaultAutomationBudget,
                    ...(p.automation?.budget || config.budget),
                  },
                });
                setConfigure(!configure);
              }}
            >
              {automated ? "调整制作范围与预算" : "设置连续制作"}
            </button>
          )}
          {p.status !== "cancelled" && p.status !== "approved" && (
            <button
              className={"button " + (paused ? "primary" : "")}
              disabled={busy}
              onClick={() => invoke({ action: paused ? "resume" : "pause" })}
            >
              {paused ? "继续制作" : "暂停制作"}
            </button>
          )}
          <Badge
            status={state.status || p.status}
            label={statusLabel(state.status || p.status)}
          />
        </div>
      </section>
      {configure && (
        <Panel title={automated ? "调整连续制作授权" : "授权 AI 接续制作"}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (config.episode_numbers.length)
                invoke({ action: "enable_automation", automation: config });
            }}
          >
            <AutomationSettings
              count={p.episode_count}
              value={config}
              onChange={setConfig}
              disabled={busy}
              budgetDisabled={automated && !paused}
              minimumBudget={
                automated &&
                episodes.some(
                  (episode) =>
                    config.episode_numbers.includes(episode.episode_number) &&
                    ![
                      "draft",
                      "awaiting_final",
                      "approved",
                      "cancelled",
                    ].includes(episode.status),
                )
                  ? p.automation?.budget
                  : undefined
              }
            />
            {automated && (
              <p className="muted">
                已开工单元仅可增加预算上限，已使用额度继续累计，已有成片保留。
                {paused
                  ? "保存授权会保持暂停，之后点击“继续制作”恢复。"
                  : "制作范围可直接更新；提高已开工预算前需要先暂停。"}
              </p>
            )}
            {automated && !paused && (
              <div className="production-attention-item">
                <p>
                  修改已开工剧集的预算前，请先暂停制作，等待当前任务到达安全边界。
                </p>
                <button
                  className="button"
                  type="button"
                  disabled={busy}
                  onClick={() => invoke({ action: "pause" })}
                >
                  暂停后调整预算
                </button>
              </div>
            )}
            {!config.episode_numbers.length && (
              <p role="alert">请至少选择一集。</p>
            )}
            <button
              className="button primary"
              disabled={busy || !config.episode_numbers.length}
            >
              {automated ? "保存制作授权" : "开启连续制作"}
            </button>
          </form>
        </Panel>
      )}
      {hasAttention && (
        <Panel title="在这里处理" className="production-attention">
          {!!p.error && <p role="alert">{p.error}</p>}
          {issues.map((issue, index) => (
            <p key={issue.code + ":" + index}>
              {issue.episode_number ? `第 ${issue.episode_number} 集：` : ""}
              {issue.message}
            </p>
          ))}
          {planNeedsAttention && (
            <div className="production-attention-item">
              <strong>
                {p.status === "awaiting_plan"
                  ? "全剧规划已通过审核"
                  : paused && p.plan_version
                    ? "制作已暂停，可修改规划"
                    : "全剧规划需要处理"}
              </strong>
              <p>
                {rows(p.plan_review?.issues).join("；") ||
                  "可在下方查看并修改规划，审核通过后继续制作。"}
              </p>
              {["blocked", "paused"].includes(p.status) &&
                (!p.plan_version || p.plan_phase === "exhausted") && (
                  <>
                    <Field label="补充创作要求">
                      <textarea
                        rows={3}
                        maxLength={4000}
                        value={planningFeedback}
                        onChange={(event) =>
                          setPlanningFeedback(event.target.value)
                        }
                        placeholder="补充关键设定、消除歧义，或说明希望如何返修"
                      />
                    </Field>
                    <button
                      className="button"
                      disabled={busy || !!p.workflow?.active_job}
                      onClick={() =>
                        invoke({
                          action: planningFeedback.trim() ? "retry" : "resume",
                          ...(planningFeedback.trim()
                            ? { feedback: planningFeedback.trim() }
                            : {}),
                        })
                      }
                    >
                      继续规划与返修
                    </button>
                  </>
                )}
              {planEditor && (
                <details
                  className="production-plan-editor"
                  open={p.status === "awaiting_plan" || paused}
                >
                  <summary>查看、补充要求与确认规划</summary>
                  {p.plan_version && !paused ? (
                    <p>请先暂停制作，等待当前任务到达安全边界后再修改规划。</p>
                  ) : (
                    planEditor
                  )}
                </details>
              )}
            </div>
          )}
          {blockedEpisodes.map((episode) => {
            const need = episode.needs_decision || {};
            const hardStop = stoppedProductionCodes.has(
              String(need.code || ""),
            );
            const diagnosis = need.details || {};
            const used = diagnosis.attempts_used ?? diagnosis.attempts;
            const limit = diagnosis.attempts_limit;
            const reason = stopReasons[diagnosis.reason || need.code];
            return (
              <div className="production-attention-item" key={episode.id}>
                <strong>
                  第 {episode.episode_number} 集 · {statusLabel(episode.status)}
                </strong>
                <p>
                  {episode.error ||
                    need.message ||
                    (episode.status === "waiting_dependency"
                      ? "等待前集完成；若依赖不在已授权范围内，请先调整制作范围。"
                      : "已有进度和素材保留，可继续此集。")}
                </p>
                {hardStop ? (
                  <div aria-label="制作停止原因">
                    {reason && <p>{reason}</p>}
                    {typeof diagnosis.asset === "string" && (
                      <p>
                        制作对象：{diagnosis.asset}
                        {diagnosis.stage === "video"
                          ? " · 视频"
                          : diagnosis.stage === "image"
                            ? " · 图片"
                            : ""}
                        {typeof used === "number" &&
                          ` · 已用尝试 ${used}${typeof limit === "number" ? ` / ${limit}` : ""}`}
                      </p>
                    )}
                    <p className="muted">
                      核对材料与授权后，再使用顶部入口继续制作。已有成果与累计用量保留。
                    </p>
                  </div>
                ) : (
                  <>
                    <button
                      className="button"
                      disabled={busy || paused}
                      onClick={() =>
                        invoke({
                          action: ["blocked", "failed"].includes(episode.status)
                            ? "retry"
                            : "resume",
                          episode_id: episode.id,
                        })
                      }
                    >
                      {episode.status === "waiting_dependency"
                        ? "检查依赖并继续"
                        : "继续此集"}
                    </button>
                    {paused && <small>请先继续整剧制作。</small>}
                  </>
                )}
              </div>
            );
          })}
          {awaiting.map((episode) => (
            <div className="production-attention-item" key={episode.id}>
              <strong>第 {episode.episode_number} 集 · 等待你的成片验收</strong>
              <p>观看成片后通过，或提交局部修改、重新剪辑意见。</p>
              <button
                className="button primary"
                disabled={busy || !episode.delivery}
                onClick={() => onReview(episode)}
              >
                观看与验收第 {episode.episode_number} 集
              </button>
            </div>
          ))}
        </Panel>
      )}
      <SelectionGroup
        as="nav"
        className="production-stage-rail"
        aria-label="制作阶段"
        value={selectedId}
      >
        {nodes.map((entry, index) => (
          <button
            key={entry.id}
            aria-pressed={selectedId === entry.id}
            onClick={() => setSelected(entry.id)}
          >
            <span className="production-stage-number">{index + 1}</span>
            <span>
              <strong>{entry.label}</strong>
              <small>{statusLabel(entry.status)}</small>
            </span>
          </button>
        ))}
      </SelectionGroup>
      {!nodes.length && (
        <Empty title="尚无阶段记录">状态更新后将在这里显示实际制作阶段。</Empty>
      )}
      <div className="production-workspace-columns">
        <Panel
          title={node?.label || "当前产物"}
          description={node?.description}
          className="production-artifacts"
        >
          {selectedId &&
          ["planning", "review", "confirmation"].includes(selectedId) &&
          plan ? (
            <>
              {p.plan_version > 0 && <p>已确认规划 v{p.plan_version}</p>}
              <Readable
                value={
                  selectedId === "review"
                    ? p.plan_review || stage?.output || "尚无已保存的审核结果"
                    : plan
                }
              />
              {!planNeedsAttention && planEditor && (
                <details className="production-plan-editor">
                  <summary>修改规划与补充要求</summary>
                  {p.plan_version && !paused ? (
                    <>
                      <p>
                        请先暂停制作，等待当前任务到达安全边界后再修改规划。
                      </p>
                      <button
                        className="button"
                        disabled={busy}
                        onClick={() => invoke({ action: "pause" })}
                      >
                        暂停并修改规划
                      </button>
                    </>
                  ) : (
                    planEditor
                  )}
                </details>
              )}
            </>
          ) : selectedId === "production" || selectedId === "acceptance" ? (
            <>
              {!episodes.length && (
                <p className="muted">
                  审核通过并固化规划后，将在这里显示剧集和当前产物。
                </p>
              )}
              {episodes.map((episode) => (
                <article className="production-episode" key={episode.id}>
                  <header>
                    <strong>
                      {episode.title || `第 ${episode.episode_number} 集`}
                    </strong>
                    <Badge status={episode.status} />
                  </header>
                  <p>
                    {episode.outline?.synopsis ||
                      episode.error ||
                      statusLabel(episode.status)}
                  </p>
                  {episode.delivery && (
                    <video
                      className="drama-video"
                      controls
                      preload="metadata"
                      src={mediaUrl(
                        episode.delivery.video_url ||
                          episode.delivery.final?.path,
                      )}
                    />
                  )}
                  <WorkProgress
                    label={`第 ${episode.episode_number} 集制作进度`}
                    completed={episode.progress || 0}
                    total={100}
                  />
                  <button
                    className="button"
                    aria-expanded={viewingEpisode === episode.id}
                    onClick={() =>
                      setViewingEpisode(
                        viewingEpisode === episode.id ? "" : episode.id,
                      )
                    }
                  >
                    {viewingEpisode === episode.id
                      ? "收起当前产物"
                      : "查看当前产物"}
                  </button>
                  {viewingEpisode === episode.id && (
                    <EpisodeArtifacts dramaId={p.id} episode={episode} />
                  )}
                  {episode.status === "awaiting_final" && (
                    <button
                      className="button primary"
                      disabled={busy || !episode.delivery}
                      onClick={() => onReview(episode)}
                    >
                      观看与验收第 {episode.episode_number} 集
                    </button>
                  )}
                </article>
              ))}
              {assets.length > 0 && (
                <details className="production-assets">
                  <summary>共享素材 · {assets.length} 项</summary>
                  <div className="production-asset-grid">
                    {assets.map((asset, index) => (
                      <figure
                        key={`${asset.version}:${asset.entity_id}:${index}`}
                      >
                        <img
                          src={mediaUrl(asset.receipt?.path)}
                          alt={`${asset.entity_id} · 设定 v${asset.version}`}
                        />
                        <figcaption>
                          {asset.entity_id} · 设定 v{asset.version}
                        </figcaption>
                      </figure>
                    ))}
                  </div>
                </details>
              )}
            </>
          ) : stage?.output ? (
            <Readable value={stage.output} />
          ) : (
            <p className="muted">此阶段尚无已保存产物。</p>
          )}
        </Panel>
        <Panel title="AI 执行动态" className="production-activity">
          {!activity.length && (
            <p className="muted">
              当前阶段尚无执行记录。完成的任务会在这里显示。
            </p>
          )}
          {activity.map((task) => (
            <article className="production-activity-item" key={task.id}>
              <strong>{task.title}</strong>
              <p>
                {roleName(task.role || "orchestrator")} ·{" "}
                {statusLabel(task.status)}
              </p>
              {task.model && <small>{task.model}</small>}
              {(task.finished_at || task.generated_at || task.started_at) && (
                <time>
                  {date(
                    task.finished_at || task.generated_at || task.started_at,
                  )}
                </time>
              )}
              {!!task.issues?.length && <p>{task.issues.join("；")}</p>}
              {task.output && (
                <details>
                  <summary>查看本次产物</summary>
                  <Readable value={task.output} />
                </details>
              )}
            </article>
          ))}
          <details className="production-advanced-tools">
            <summary>高级工具与完整记录</summary>
            <p className="muted">按当前项目与规划版本查看制作依据。</p>
            <div className="production-tool-links">
              {[
                ["batches", "生成批次"],
                ["comparison", "质量比较"],
                ["prompts", "提示词工作台"],
                ["generation", "生成提示词流水线"],
                ["agent", "Agent 执行循环"],
              ].map(([tab, label]) => (
                <button className="button" key={tab} onClick={() => onTab(tab)}>
                  {label}
                </button>
              ))}
            </div>
          </details>
        </Panel>
      </div>
      {workflow && (
        <details className="production-full-workflow">
          <summary>完整制作流程与团队记录</summary>
          {workflow}
        </details>
      )}
    </div>
  );
}
