import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Users, CircleCheck, CornerDownLeft } from "lucide-react";
import { rows, roleName, date, type RecordData } from "./api";
import { Badge, Panel, Empty, Artifact } from "./ui";
import { ExecutionRecord, MaterialCard } from "./DocumentReader";

export function workflowNodes(
  graph: RecordData,
  flow?: RecordData,
): RecordData[] {
  const tasks = rows(flow?.task_board?.tasks);
  return rows(graph.nodes).map((n) => {
    const contract = tasks.find((t) => t.id === n.id);
    const members = [
      ...new Set<string>(
        [
          n.owner,
          n.role,
          n.reviewer,
          ...(Array.isArray(n.members) ? n.members : []),
          contract?.owner,
          contract?.reviewer,
        ].filter(Boolean),
      ),
    ];
    return {
      ...n,
      owner: n.owner || n.role || contract?.owner,
      reviewer: n.reviewer || contract?.reviewer,
      members,
    };
  });
}
const legacyGroups = [
  {
    id: "story",
    label: "故事策划",
    node_ids: ["research", "proposals", "story_decision"],
  },
  {
    id: "script",
    label: "剧本与分镜",
    node_ids: ["story_resolution", "screenplay", "storyboard", "script_review"],
  },
  {
    id: "visual",
    label: "视觉确认",
    node_ids: ["visual_design", "visual_development", "style_decision"],
  },
  { id: "production", label: "镜头与后期", node_ids: ["production"] },
  {
    id: "delivery",
    label: "终审交付",
    node_ids: ["final_decision", "delivery"],
  },
];
export function workflowGroups(graph: RecordData, nodes: RecordData[]) {
  const ids = new Set(nodes.map((n) => n.id));
  const supplied = rows(graph.presentation?.stages);
  const groups: RecordData[] = (supplied.length ? supplied : legacyGroups)
    .map((g) => ({
      ...g,
      node_ids: (g.node_ids || []).filter((id: string) => ids.has(id)),
    }))
    .filter((g) => g.node_ids.length);
  const included = new Set(groups.flatMap((g) => g.node_ids));
  const other = nodes.filter((n) => n.id !== "revision" && !included.has(n.id));
  if (other.length)
    groups.push({
      id: "other",
      label: "其他步骤",
      node_ids: other.map((n) => n.id),
    });
  return groups;
}
function chooseStep(ids: string[], nodes: RecordData[], active?: string) {
  const needsAttention = (id: string) =>
    [
      "running",
      "human",
      "blocked",
      "waiting",
      "waiting_remote",
      "paused",
    ].includes(nodes.find((n) => n.id === id)?.status);
  return (
    ids.find((id) => id === active && needsAttention(id)) ||
    ids.find(needsAttention) ||
    ids[0]
  );
}
function productTab(id: string) {
  if (
    ["story_resolution", "screenplay", "storyboard", "script_review"].includes(
      id,
    )
  )
    return "script";
  if (
    [
      "proposals",
      "story_decision",
      "visual_design",
      "visual_development",
      "style_decision",
      "final_decision",
    ].includes(id)
  )
    return "decisions";
  return (
    {
      production: "shots",
      delivery: "delivery",
      revision: "quality",
    } as Record<string, string>
  )[id];
}
function roleKind(role: string) {
  return role === "producer"
    ? "人工决策"
    : [
          "image_generator",
          "video_generator",
          "speech",
          "editor",
          "orchestrator",
        ].includes(role)
      ? "制作工具"
      : "模型角色";
}
export function Workflow({
  graph,
  flow,
  attention,
  onDecision,
  onShot,
  onOpen,
  selectedNode,
  onSelectNode,
  selectedExecution,
}: {
  graph: RecordData;
  flow?: RecordData;
  attention?: RecordData;
  onDecision?: () => void;
  onShot?: (id: string) => void;
  onOpen?: (tab: string) => void;
  selectedNode?: string;
  onSelectNode?: (id: string) => void;
  selectedExecution?: string;
}) {
  const [selection, setSelection] = useState("");
  const stages = useMemo(() => workflowNodes(graph, flow), [graph, flow]);
  const groups = useMemo(() => workflowGroups(graph, stages), [graph, stages]);
  const selected =
    stages.find((n) => n.id === (selectedNode ?? selection)) ||
    stages.find((n) => n.id === graph.active_node) ||
    stages.find((n) => n.id === groups[0]?.node_ids[0]) ||
    stages[0];
  const group = groups.find((g) => g.node_ids.includes(selected?.id));
  const select = (id: string) => {
    setSelection(id);
    onSelectNode?.(id);
  };
  useEffect(() => {
    if (selectedExecution)
      Array.from(document.querySelectorAll<HTMLElement>("[data-execution]"))
        .find((el) => el.dataset.execution === selectedExecution)
        ?.scrollIntoView({ block: "center" });
  }, [selectedExecution, selected?.id]);
  if (!selected)
    return (
      <Empty title="尚未返回执行图">
        流程定义由服务器提供，连接恢复后可查看真实节点。
      </Empty>
    );
  const material = rows(flow?.steps).find((s) => s.id === selected.id);
  const histories = rows(flow?.nodes).filter(
    (n) => selected.members.includes(n.role) || n.id === selected.id,
  );
  const records: RecordData[] = rows(flow?.nodes).flatMap((n) =>
    rows(n.executions)
      .filter((e) =>
        e.stage ? e.stage === selected.id : selected.members.includes(n.role),
      )
      .map((e) => ({ ...e, role: n.role })),
  );
  const stale =
    flow?.revision != null &&
    graph.revision != null &&
    flow.revision < graph.revision;
  const io = (side: "inputs" | "outputs") => {
    const items: RecordData[] = material
      ? rows(material[side])
      : histories.flatMap((n) =>
          rows(n[side]).map((v) => ({
            ...v,
            source_label: roleName(n.role) + " · 当前项目材料",
          })),
        );
    return items.length ? (
      items.map((v, i) => (
        <MaterialCard
          key={v.key || v.label + ":" + i}
          item={{
            ...v,
            revision: flow?.revision,
            ...(stale
              ? { artifact_url: undefined, url: undefined, path: undefined }
              : {}),
          }}
          projectId={graph.project_id || flow?.project_id}
          onSource={(id) => {
            if (stages.some((n) => n.id === id)) select(id);
          }}
        />
      ))
    ) : (
      <p className="muted">
        {!graph.project_id
          ? "进入项目后查看实际材料。"
          : side === "inputs"
            ? "未记录输入材料。"
            : "尚未生成交付内容。"}
      </p>
    );
  };
  const transitions = rows(graph.edges).filter(
    (e) =>
      (e.loop || e.source === "revision" || e.target === "revision") &&
      (e.source === selected.id || e.target === selected.id),
  );
  const returnTarget = graph.presentation?.branches?.find((b: RecordData) =>
    b.node_ids?.includes(selected.id),
  )?.return_to;
  const productionTasks = rows(
    graph.production_tasks || flow?.production_tasks,
  );
  return (
    <section className="workflow-workspace" aria-label="制作工作流">
      <header className="workflow-heading">
        <div>
          <strong>制作工作流</strong>
          <p>
            {graph.current_activity || "按阶段查看制作步骤、参与角色与交付内容"}
          </p>
        </div>
        <small>
          {graph.project_id
            ? `项目版本 ${graph.revision ?? "—"}`
            : "标准流程说明"}
        </small>
      </header>
      {stale && (
        <p className="notice">材料正在同步，保留上次记录；文件入口暂时停用。</p>
      )}
      <div className="workflow-layout">
        <nav className="workflow-stages" aria-label="制作阶段">
          {groups.map((g, i) => {
            const children = stages.filter((n) => g.node_ids.includes(n.id));
            const active = children.find((n) => n.id === graph.active_node);
            const done = children.filter(
              (n) => n.status === "succeeded",
            ).length;
            return (
              <button
                key={g.id}
                className={
                  "workflow-stage-card " +
                  (group?.id === g.id ? "selected" : "")
                }
                data-stage={g.id}
                aria-pressed={group?.id === g.id}
                onClick={() =>
                  select(chooseStep(g.node_ids, stages, graph.active_node))
                }
              >
                <span className="workflow-stage-number">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span className="workflow-stage-copy">
                  <strong>{g.label}</strong>
                  <small>
                    {active
                      ? active.label
                      : `${children.length} 个步骤 · ${done} 个已完成`}
                  </small>
                </span>
                {active ? (
                  <Badge status={active.status} />
                ) : (
                  <ArrowRight size={16} />
                )}
              </button>
            );
          })}
          {stages.some((n) => n.id === "revision") && (
            <div className="workflow-repair">
              <small>按需返回制作</small>
              <button
                className={
                  "workflow-stage-card " +
                  (selected.id === "revision" ? "selected" : "")
                }
                aria-pressed={selected.id === "revision"}
                onClick={() => select("revision")}
              >
                <CornerDownLeft size={20} />
                <span className="workflow-stage-copy">
                  <strong>定向返修</strong>
                  <small>修改后返回镜头与后期</small>
                </span>
                <Badge
                  status={stages.find((n) => n.id === "revision")?.status}
                />
              </button>
            </div>
          )}
        </nav>
        <aside className="workflow-inspector" aria-label="节点详情">
          <nav className="workflow-steps" aria-label="阶段内步骤">
            {stages
              .filter((n) =>
                group ? group.node_ids.includes(n.id) : n.id === selected.id,
              )
              .sort((a, b) =>
                group
                  ? group.node_ids.indexOf(a.id) - group.node_ids.indexOf(b.id)
                  : 0,
              )
              .map((n) => (
                <button
                  key={n.id}
                  data-flow-node={n.id}
                  aria-pressed={n.id === selected.id}
                  className={n.id === selected.id ? "active" : ""}
                  onClick={() => select(n.id)}
                >
                  {n.label}
                  <Badge status={n.status} />
                  {n.conditional && <small>按需执行</small>}
                </button>
              ))}
          </nav>
          <header className="workflow-step-heading">
            <div>
              <small>{group?.label || "返修分支"}</small>
              <h3>{selected.label || selected.id}</h3>
            </div>
            <Badge status={selected.status} />
          </header>
          <p className="workflow-description">
            {selected.description ||
              `${roleName(selected.owner || selected.role || "orchestrator")}负责此步骤。`}
          </p>
          <dl className="workflow-contract">
            <div>
              <dt>接收</dt>
              <dd>{selected.input || "查看下方输入材料"}</dd>
            </div>
            <div>
              <dt>交付</dt>
              <dd>{selected.output || "查看下方交付内容"}</dd>
            </div>
          </dl>
          <section className="workflow-section">
            <h4>参与 Agent 与职责</h4>
            <div className="workflow-members">
              {selected.members.map((r: string) => (
                <span key={r}>
                  <Users size={14} />
                  <strong>{roleName(r)}</strong>
                  <small>
                    {roleKind(r)}
                    {r === selected.owner
                      ? " · 负责"
                      : r === selected.reviewer
                        ? " · 审核"
                        : ""}
                  </small>
                </span>
              ))}
            </div>
          </section>
          {(transitions.length > 0 || returnTarget) && (
            <div className="workflow-transitions">
              <strong>返修与返回</strong>
              {transitions.map((e, i) => (
                <p key={i}>
                  <span>
                    {stages.find((n) => n.id === e.source)?.label || e.source}
                  </span>
                  <ArrowRight size={13} />
                  <button
                    className="text-link"
                    onClick={() => select(e.target)}
                  >
                    {stages.find((n) => n.id === e.target)?.label || e.target}
                  </button>
                  <small>
                    {(
                      {
                        repair: "审核未通过",
                        repropose: "重新提案",
                        restyle: "重新设计",
                        continue: "按返修要求接续",
                      } as RecordData
                    )[e.label] || e.label}
                  </small>
                </p>
              ))}
              {returnTarget &&
                !transitions.some((e) => e.target === returnTarget) && (
                  <p>
                    完成后返回{" "}
                    <button
                      className="text-link"
                      onClick={() => select(returnTarget)}
                    >
                      {stages.find((n) => n.id === returnTarget)?.label ||
                        returnTarget}
                    </button>
                  </p>
                )}
            </div>
          )}
          {attention?.blocking && selected.id === graph.active_node && (
            <button className="button primary" onClick={onDecision}>
              处理当前事项
              <ArrowRight size={16} />
            </button>
          )}
          <section className="workflow-section" aria-label="输入材料">
            <h4>输入材料</h4>
            {io("inputs")}
          </section>
          <section className="workflow-section" aria-label="交付内容">
            <h4>交付内容</h4>
            {io("outputs")}
          </section>
          {onOpen && productTab(selected.id) && (
            <button
              className="text-link"
              onClick={() => onOpen(productTab(selected.id))}
            >
              打开对应功能
              <ArrowRight size={14} />
            </button>
          )}
          <section className="workflow-section" aria-label="步骤执行记录">
            <h4>执行记录</h4>
            {records.length ? (
              records.map((e) => (
                <div
                  key={e.id}
                  className={
                    e.id === selectedExecution ? "execution-record-focus" : ""
                  }
                >
                  <ExecutionRecord
                    execution={
                      stale
                        ? { ...e, artifacts: [], attempts: [], receipt: null }
                        : e
                    }
                    role={e.role}
                    projectId={graph.project_id || flow?.project_id}
                  />
                </div>
              ))
            ) : (
              <p className="muted">
                尚无此步骤的调用记录。历史角色调用仅在有保存记录时显示。
              </p>
            )}
            {flow?.evidence?.truncated && (
              <p className="muted">
                历史记录较多，当前展示最近保存的部分记录。
              </p>
            )}
          </section>
          {selected.id === "production" && (
            <section className="workflow-section production-tasks">
              <h4>镜头与后期实际任务</h4>
              {productionTasks.length ? (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>镜头 / 阶段</th>
                        <th>负责 / 审核</th>
                        <th>版本 / 尝试</th>
                        <th>状态</th>
                        <th>产物</th>
                      </tr>
                    </thead>
                    <tbody>
                      {productionTasks.map((t, i) => (
                        <tr key={t.id || i}>
                          <td>
                            {t.shot_id ? (
                              <button
                                className="text-link"
                                onClick={() => onShot?.(t.shot_id)}
                              >
                                {t.shot_id}
                                <ArrowRight size={13} />
                              </button>
                            ) : (
                              t.phase || t.stage
                            )}
                          </td>
                          <td>
                            {roleName(t.owner)} / {roleName(t.reviewer)}
                          </td>
                          <td>
                            {t.production_revision == null
                              ? "版本未记录"
                              : `v${t.production_revision}`}{" "}
                            · {t.attempt || "—"} 次
                          </td>
                          <td>
                            <Badge status={t.status} />
                          </td>
                          <td>
                            {t.artifact && !stale && (
                              <Artifact
                                item={{
                                  url:
                                    typeof t.artifact === "string"
                                      ? t.artifact
                                      : t.artifact.path,
                                  label: "查看产物",
                                }}
                                projectId={graph.project_id}
                              />
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="muted">
                  尚无实际制作记录，生成后显示镜头、配音、剪辑与质检子任务。
                </p>
              )}
            </section>
          )}
          <p className="workflow-evidence">
            状态依据：
            {(
              {
                task_receipt: "实际工单",
                decision_receipt: "已保存的决定",
                current_project_state: "当前项目状态",
                not_recorded: "尚无记录",
                producer_approval: "出品人终审通过",
              } as RecordData
            )[selected.evidence] ||
              selected.evidence ||
              "服务器流程定义"}
            {selected.attempt ? ` · 第 ${selected.attempt} 次执行` : ""}
          </p>
        </aside>
      </div>
    </section>
  );
}
export function Team({
  flow,
  graph,
  models,
  projectRevision,
  onOpen,
  onInspect,
}: {
  flow: RecordData;
  graph: RecordData;
  models?: RecordData;
  projectRevision: number;
  onOpen?: (tab: string) => void;
  onInspect?: (step: string, execution?: string) => void;
}) {
  const tasks = rows(flow.task_board?.tasks),
    stale = flow.revision < projectRevision;
  const groups = rows(graph.groups).length
    ? rows(graph.groups)
    : rows(flow.groups);
  const records = rows(flow.supervision?.records);
  return (
    <div className="stack">
      {stale && (
        <div className="notice">
          团队记录正在同步；以下保留上次执行记录，旧交付链接暂时停用。
        </div>
      )}
      <Panel
        title="制作团队与职责"
        description="负责人、独立审核和交接产物共同构成任务闭环。"
      >
        <div className="department-grid">
          {groups.map((g) => (
            <article className="department-card" key={g.id}>
              <Users size={23} />
              <h3>{g.label}</h3>
              <p>{g.responsibility}</p>
              <div className="member-chips">
                {(Array.isArray(g.roles) ? g.roles : []).map((r: string) => (
                  <span key={r}>{roleName(r)}</span>
                ))}
              </div>
            </article>
          ))}
        </div>
        {!groups.length && (
          <div className="member-chips">
            {rows(flow.teams).map((t) => (
              <span key={t.id}>
                {t.label || roleName(t.role)} · {t.model || "自动编排"}
              </span>
            ))}
          </div>
        )}
      </Panel>
      <Panel
        title="当前任务与交接"
        description="工单完成和独立审核通过分别记录。"
      >
        <div className="task-grid">
          {tasks.map((t) => (
            <article className="task-card" data-team-task={t.id} key={t.id}>
              <div className="row-between">
                <h3>{t.label}</h3>
                <Badge status={t.current ? t.status : "pending"} />
              </div>
              <p>
                负责：{roleName(t.owner)} <ArrowRight size={13} /> 审核：
                {roleName(t.reviewer)}
              </p>
              <div className="task-io">
                <small>接收</small>
                <span>
                  {rows(graph.nodes).find((n) => n.id === t.id)?.input ||
                    (t.inputs || [])
                      .map((v: any) => (typeof v === "object" ? v.label : v))
                      .join(" · ") ||
                    "创作需求"}
                </span>
                <small>交付</small>
                <span>
                  {rows(graph.nodes).find((n) => n.id === t.id)?.output ||
                    (t.outputs || [])
                      .map((v: any) => (typeof v === "object" ? v.label : v))
                      .join(" · ") ||
                    "尚未记录"}
                </span>
              </div>
              {t.record && (
                <small>
                  第 {t.record.attempt || 1} 次 ·{" "}
                  {date(t.record.finished_at || t.record.started_at)}
                </small>
              )}
              <button
                className="text-link"
                onClick={() =>
                  onInspect
                    ? onInspect(t.id)
                    : onOpen?.(
                        t.id === "production"
                          ? "shots"
                          : t.id === "delivery"
                            ? "delivery"
                            : t.id.endsWith("_decision")
                              ? "decisions"
                              : "script",
                      )
                }
              >
                打开关联产物
                <ArrowRight size={12} />
              </button>
            </article>
          ))}
        </div>
        {!tasks.length && (
          <Empty title="尚无当前任务记录">
            团队执行后显示实际工单，不根据阶段名称推测调用。
          </Empty>
        )}
      </Panel>
      {records.length > 0 && (
        <Panel title="总制片决定" className="supervisor-panel">
          {records.map((r, i) => (
            <article className="supervisor-record" key={i}>
              <div className="row-between">
                <h3>
                  {(
                    {
                      story: "故事方向",
                      style: "视觉方向",
                      final: "终审建议",
                    } as RecordData
                  )[r.kind] || r.kind}
                </h3>
                <Badge status={r.status} />
              </div>
              <p>{r.proposal?.rationale || "决定尚未完成"}</p>
              {rows(r.proposal?.risks).length > 0 && (
                <p className="muted">{r.proposal.risks.join("；")}</p>
              )}
              <small>
                {r.requested_model || "模型未记录"} · 不代表已观看成片
              </small>
            </article>
          ))}
        </Panel>
      )}
      <Panel
        title="实际调用与回执"
        description="按实际调用查看请求、响应和重试；缺少步骤归属的旧调用保留为角色历史。"
      >
        {rows(flow.nodes).flatMap((n) =>
          rows(n.executions).map((e) => (
            <ExecutionRecord
              key={n.id + ":" + e.id}
              execution={
                stale ? { ...e, artifacts: [], attempts: [], receipt: null } : e
              }
              role={n.role}
              projectId={flow.project_id}
              onLocate={onInspect}
            />
          )),
        )}
        {!rows(flow.nodes).some((n) => rows(n.executions).length) && (
          <p className="muted">尚无保存的执行记录。</p>
        )}
        {flow.evidence?.truncated && (
          <p className="muted">当前展示最近保存的部分历史记录。</p>
        )}
        <details>
          <summary>输入、交付与依赖</summary>
          {rows(flow.nodes).map((n) => (
            <article className="io-record" key={n.id}>
              <h3>{n.label || roleName(n.role)}</h3>
              {(["inputs", "outputs"] as const).map((side) => (
                <section key={side}>
                  <h4>{side === "inputs" ? "输入材料" : "交付内容"}</h4>
                  {rows(n[side]).map((item, i) => (
                    <MaterialCard
                      key={i}
                      item={stale ? { ...item, artifact_url: undefined } : item}
                      projectId={flow.project_id}
                    />
                  ))}
                  {!rows(n[side]).length && (
                    <p className="muted">
                      {side === "inputs" ? "未记录" : "尚未生成"}
                    </p>
                  )}
                </section>
              ))}
            </article>
          ))}
        </details>
      </Panel>
      {flow.delivery_readiness && (
        <Panel title="交付与发布检查" className="delivery-readiness">
          <div className="check-list">
            {rows(flow.delivery_readiness.checks).map((c) => (
              <div className="row-between" key={c.id}>
                <span>
                  <CircleCheck size={16} />
                  {c.label}
                </span>
                <Badge status={c.status} />
              </div>
            ))}
          </div>
          <div className="artifact-grid">
            {rows(flow.delivery_readiness.artifacts).map((a, i) =>
              stale ? (
                <span key={i}>{a.label || a.name}</span>
              ) : (
                <Artifact key={i} item={a} projectId={flow.project_id} />
              ),
            )}
          </div>
          <p className="muted">工程验收不等于平台审核通过。</p>
          {rows(flow.delivery_readiness.platform_checklist).map((c) => (
            <div className="row-between" key={c.id}>
              <span>{c.label}</span>
              <Badge status="human_required" />
            </div>
          ))}
        </Panel>
      )}
    </div>
  );
}
