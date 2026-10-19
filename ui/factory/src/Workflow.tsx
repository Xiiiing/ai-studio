import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Users, CircleCheck } from "lucide-react";
import { rows, roleName, date, activityName, type RecordData } from "./api";
import { Badge, Panel, Empty, Artifact, Modal } from "./ui";
import { WorkflowSummary, selectedHandoffs } from "./WorkflowSummary";
import { ExecutionRecord, MaterialCard } from "./DocumentReader";
import { RoleHandoffs } from "./RoleHandoffs";
import {
  FixedWorkflowCanvas,
  readCanvasSelection,
  type CanvasSelection,
} from "./FixedWorkflowCanvas";
import {
  CollaborationDetails,
  collaborationModel,
} from "./WorkflowCollaboration";

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
export function Workflow(props: Parameters<typeof WorkflowWorkspace>[0]) {
  return (
    <WorkflowWorkspace
      key={props.graph.project_id || "definition"}
      {...props}
    />
  );
}
function WorkflowWorkspace({
  graph,
  flow,
  attention,
  onDecision,
  onShot,
  onOpen,
  selectedNode,
  onSelectNode,
  selectedExecution,
  renderRecords,
  onProject,
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
  renderRecords?: (node: RecordData) => ReactNode;
  onProject?: (id: string) => void;
}) {
  const [canvasSelection, setCanvasSelection] = useState<
    CanvasSelection | undefined
  >(
    () =>
      readCanvasSelection(graph.project_id) || {
        kind: "step",
        id: selectedNode || graph.active_node || rows(graph.nodes)[0]?.id,
      },
  );
  const [selection, setSelection] = useState(() => {
    const saved = readCanvasSelection(graph.project_id);
    return saved?.kind === "step" ? saved.id : "";
  });
  const [detailView, setDetailView] = useState("materials");
  const [detailOpen, setDetailOpen] = useState(false);
  const inspectorRef = useRef<HTMLElement>(null);
  const stages = useMemo(() => workflowNodes(graph, flow), [graph, flow]);
  const groups = useMemo(() => workflowGroups(graph, stages), [graph, stages]);
  const model = useMemo(
    () => collaborationModel(graph, flow || {}, stages),
    [graph, flow, stages],
  );
  const selectCanvas = (value: CanvasSelection) => {
    setCanvasSelection(value);
    if (inspectorRef.current)
      (
        inspectorRef.current.closest<HTMLElement>(".dialog-body") ||
        inspectorRef.current
      ).scrollTop = 0;
    if (value.kind === "step") {
      setSelection(value.id);
      setDetailView("materials");
      onSelectNode?.(value.id);
    }
  };
  const selected =
    stages.find((n) => n.id === (selectedNode ?? selection)) ||
    stages.find((n) => n.id === graph.active_node) ||
    stages.find((n) => n.id === groups[0]?.node_ids[0]) ||
    stages[0];
  const group = groups.find((g) => g.node_ids.includes(selected?.id));
  const select = (id: string) => {
    if (id !== selected?.id && inspectorRef.current)
      (
        inspectorRef.current.closest<HTMLElement>(".dialog-body") ||
        inspectorRef.current
      ).scrollTop = 0;
    setCanvasSelection({ kind: "step", id });
    setDetailView("materials");
    setSelection(id);
    onSelectNode?.(id);
  };
  useEffect(() => {
    if (selectedExecution) {
      setDetailOpen(true);
      setCanvasSelection({ kind: "step", id: selectedNode || selected?.id });
      setDetailView("records");
      const frame = requestAnimationFrame(() => {
        const pane = inspectorRef.current?.closest<HTMLElement>(".dialog-body");
        const record = Array.from(
          pane?.querySelectorAll<HTMLElement>("[data-execution]") || [],
        ).find((el) => el.dataset.execution === selectedExecution);
        if (pane && record)
          pane.scrollTop +=
            record.getBoundingClientRect().top -
            pane.getBoundingClientRect().top -
            24;
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [selectedExecution, selected?.id]);
  useEffect(() => {
    if (selectedNode) setCanvasSelection({ kind: "step", id: selectedNode });
  }, [selectedNode]);
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
    <section
      className="workflow-workspace workflow-fixed-workspace"
      aria-label="制作工作流"
    >
      <header className="workflow-heading">
        <div>
          <h2>制作工作流</h2>
          <p>
            {activityName(
              graph.current_activity,
              "按阶段查看制作步骤、参与角色与交付内容",
            )}
          </p>
        </div>
        <div className="workflow-heading-actions">
          <small>
            {graph.project_id
              ? `项目版本 ${graph.revision ?? "—"}`
              : "标准流程说明"}
          </small>
        </div>
      </header>
      <div className="workflow-progress-strip">
        <span>
          <Badge
            status={
              stages.find((n) => n.id === graph.active_node)?.status ||
              "pending"
            }
          />
          {stages.find((n) => n.id === graph.active_node)?.label || "等待开始"}
          {model.members.some((m) => m.status === "running") && (
            <small className="workflow-current-workers">
              正在工作：
              {model.members
                .filter((m) => m.status === "running")
                .map((m) => m.label)
                .join("、")}
            </small>
          )}
        </span>
        <small>
          {
            stages.filter((n) => n.status === "succeeded" && !n.conditional)
              .length
          }{" "}
          / {stages.filter((n) => !n.conditional).length} 步骤已完成
        </small>
      </div>
      {stale && (
        <p className="notice">材料正在同步，保留上次记录；文件入口暂时停用。</p>
      )}
      <div
        className="workflow-fixed-layout"
        aria-hidden={detailOpen || undefined}
      >
        <FixedWorkflowCanvas
          graph={graph}
          groups={groups}
          steps={stages}
          model={model}
          selection={canvasSelection}
          onSelection={selectCanvas}
        />
        <WorkflowSummary
          graph={graph}
          flow={flow || {}}
          steps={stages}
          groups={groups}
          model={model}
          selection={canvasSelection}
          selected={selected}
          onClose={() => setCanvasSelection(undefined)}
          onOpen={(view) => {
            setDetailView(view);
            setDetailOpen(true);
          }}
          onDecision={onDecision}
          blocking={!!attention?.blocking}
        />
      </div>
      {detailOpen && (
        <Modal
          id="workflow-detail-card"
          title="工作与交付详情"
          className="workflow-detail-card"
          onClose={() => setDetailOpen(false)}
        >
          <aside
            ref={inspectorRef}
            tabIndex={-1}
            className="workflow-inspector workflow-detail-content"
            aria-label="节点详情"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !event.defaultPrevented) {
                event.stopPropagation();
                setDetailOpen(false);
              }
            }}
          >
            <RoleHandoffs
              key={`${graph.project_id}:${canvasSelection?.kind}:${canvasSelection?.id}`}
              projectId={graph.project_id}
              value={flow?.controlled_handoffs}
              stale={!!stale}
              roles={
                canvasSelection?.kind === "role"
                  ? [canvasSelection.id]
                  : canvasSelection?.kind === "step"
                    ? selected?.members
                    : undefined
              }
              pairs={
                canvasSelection &&
                ["handoff", "stage-edge"].includes(canvasSelection.kind)
                  ? selectedHandoffs(canvasSelection, model, groups)
                  : undefined
              }
            />
            {canvasSelection?.kind === "step" ? (
              <>
                <nav
                  className="workflow-steps"
                  aria-label="阶段内步骤"
                  hidden={!group || group.node_ids.length < 2}
                >
                  {stages
                    .filter((n) =>
                      group
                        ? group.node_ids.includes(n.id)
                        : n.id === selected.id,
                    )
                    .sort((a, b) =>
                      group
                        ? group.node_ids.indexOf(a.id) -
                          group.node_ids.indexOf(b.id)
                        : 0,
                    )
                    .map((n, index, nodes) => (
                      <span className="workflow-step-segment" key={n.id}>
                        <button
                          key={n.id}
                          data-flow-node={n.id}
                          aria-pressed={n.id === selected.id}
                          className={n.id === selected.id ? "active" : ""}
                          onClick={() => select(n.id)}
                        >
                          {n.label}
                          <Badge status={n.status} />
                        </button>
                        {nodes[index + 1] &&
                          rows(graph.edges).some(
                            (e) =>
                              e.source === n.id &&
                              e.target === nodes[index + 1].id &&
                              !e.loop,
                          ) && (
                            <ArrowRight
                              size={15}
                              aria-hidden="true"
                              className={
                                "workflow-step-link " +
                                (n.id === graph.active_node &&
                                n.status === "running"
                                  ? "is-running"
                                  : "")
                              }
                            />
                          )}
                      </span>
                    ))}
                </nav>
                <header className="workflow-step-heading">
                  <div>
                    {group?.label !== selected.label && (
                      <small>{group?.label || "返修分支"}</small>
                    )}
                    <h3>{selected.label || selected.id}</h3>
                  </div>
                  <span>
                    <Badge status={selected.status} />
                    {selected.conditional && <small>按需执行</small>}
                  </span>
                </header>
                <div
                  className="workflow-detail-switch"
                  role="group"
                  aria-label="节点内容"
                >
                  <button
                    aria-pressed={detailView === "materials"}
                    onClick={() => {
                      setDetailView("materials");
                      if (inspectorRef.current)
                        (
                          inspectorRef.current.closest<HTMLElement>(
                            ".dialog-body",
                          ) || inspectorRef.current
                        ).scrollTop = 0;
                    }}
                  >
                    材料与职责
                  </button>
                  <button
                    aria-pressed={detailView === "records"}
                    onClick={() => {
                      setDetailView("records");
                      if (inspectorRef.current)
                        (
                          inspectorRef.current.closest<HTMLElement>(
                            ".dialog-body",
                          ) || inspectorRef.current
                        ).scrollTop = 0;
                    }}
                  >
                    执行记录
                  </button>
                </div>
                <div hidden={detailView !== "materials"}>
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
                  <details className="workflow-section workflow-role-details">
                    <summary>
                      参与 Agent 与职责 · {selected.members.length} 位
                    </summary>
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
                  </details>
                  {(transitions.length > 0 || returnTarget) && (
                    <div className="workflow-transitions">
                      <strong>返修与返回</strong>
                      {transitions.map((e, i) => (
                        <p key={i}>
                          <span>
                            {stages.find((n) => n.id === e.source)?.label ||
                              e.source}
                          </span>
                          <ArrowRight size={13} />
                          <button
                            className="text-link"
                            onClick={() => select(e.target)}
                          >
                            {stages.find((n) => n.id === e.target)?.label ||
                              e.target}
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
                              {stages.find((n) => n.id === returnTarget)
                                ?.label || returnTarget}
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
                </div>
                <div hidden={detailView !== "records"}>
                  <section
                    className="workflow-section"
                    aria-label="步骤执行记录"
                  >
                    <h4>执行记录</h4>
                    {renderRecords ? (
                      renderRecords(selected)
                    ) : records.length ? (
                      records.map((e) => (
                        <div
                          key={e.id}
                          className={
                            e.id === selectedExecution
                              ? "execution-record-focus"
                              : ""
                          }
                        >
                          <ExecutionRecord
                            execution={
                              stale
                                ? {
                                    ...e,
                                    artifacts: [],
                                    attempts: [],
                                    receipt: null,
                                  }
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
                  {selected.id === "production" && !renderRecords && (
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
                </div>
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
              </>
            ) : canvasSelection ? (
              <>
                {canvasSelection.kind === "stage-edge" &&
                  (() => {
                    const source = canvasSelection.id.split(":")[0],
                      target = canvasSelection.id.split(":")[1];
                    const from = groups.find((g) => g.id === source) || {
                      node_ids: [source],
                      label: source,
                    };
                    const to = groups.find((g) => g.id === target) || {
                      node_ids: [target],
                      label: target,
                    };
                    const outputs = rows(flow?.steps)
                      .filter((s) => from.node_ids.includes(s.id))
                      .flatMap((s) => rows(s.outputs));
                    const inputs = rows(flow?.steps)
                      .filter((s) => to.node_ids.includes(s.id))
                      .flatMap((s) => rows(s.inputs));
                    return (
                      <section className="workflow-stage-delivery">
                        <h3>
                          {from.label} → {to.label}
                        </h3>
                        <p className="muted">
                          阶段之间的交付方向；下方列出保存的材料及可核对的岗位交接记录。
                        </p>
                        <h4>上游交付材料</h4>
                        {outputs.length ? (
                          outputs.map((item, i) => (
                            <MaterialCard
                              key={i}
                              item={
                                stale
                                  ? {
                                      ...item,
                                      artifact_url: undefined,
                                      url: undefined,
                                      path: undefined,
                                    }
                                  : item
                              }
                              projectId={graph.project_id}
                            />
                          ))
                        ) : (
                          <p className="muted">尚无保存的交付材料。</p>
                        )}
                        <h4>下游接收材料</h4>
                        {inputs.length ? (
                          inputs.map((item, i) => (
                            <MaterialCard
                              key={i}
                              item={
                                stale
                                  ? {
                                      ...item,
                                      artifact_url: undefined,
                                      url: undefined,
                                      path: undefined,
                                    }
                                  : item
                              }
                              projectId={graph.project_id}
                            />
                          ))
                        ) : (
                          <p className="muted">尚无保存的接收材料。</p>
                        )}
                      </section>
                    );
                  })()}
                <CollaborationDetails
                  graph={graph}
                  flow={flow || {}}
                  model={model}
                  departmentId={
                    canvasSelection.department ||
                    (canvasSelection.kind === "department"
                      ? canvasSelection.id
                      : undefined)
                  }
                  role={
                    canvasSelection.kind === "role"
                      ? canvasSelection.id
                      : undefined
                  }
                  stageIds={
                    canvasSelection.stage
                      ? groups.find((g) => g.id === canvasSelection.stage)
                          ?.node_ids || [canvasSelection.stage]
                      : undefined
                  }
                  handoffs={
                    ["handoff", "stage-edge"].includes(canvasSelection.kind)
                      ? selectedHandoffs(canvasSelection, model, groups)
                      : undefined
                  }
                  stale={!!stale}
                  onStep={select}
                  onProject={onProject}
                />
              </>
            ) : (
              <div className="collaboration-empty-inspector">
                <h3>查看工作与交付</h3>
                <p>
                  点击阶段、部门或岗位查看详情，点击连线查看谁把什么交给了谁。
                </p>
                <p>点击节点或连线查看摘要，打开卡片阅读完整详情。</p>
              </div>
            )}
          </aside>
        </Modal>
      )}
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
  const timestamp = (value: any) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : Date.parse(value) || 0;
  };
  const completedTaskStatuses = new Set([
    "succeeded",
    "finished",
    "completed",
    "done",
    "skipped",
    "cancelled",
    "reused",
  ]);
  const attentionTaskStatuses = new Set([
    "running",
    "failed",
    "error",
    "blocked",
    "paused",
    "waiting",
    "waiting_remote",
    "human",
    "human_required",
    "interrupted",
    "requested",
    "queued",
  ]);
  const currentTasks = tasks.filter(
    (task) =>
      !completedTaskStatuses.has(task.status) &&
      (task.id === graph.active_node ||
        (task.current !== false &&
          (task.current || attentionTaskStatuses.has(task.status)))),
  );
  const latestHandoff = tasks
    .filter(
      (task) =>
        task.current !== false && completedTaskStatuses.has(task.status),
    )
    .sort(
      (a, b) =>
        timestamp(b.record?.finished_at || b.record?.started_at) -
          timestamp(a.record?.finished_at || a.record?.started_at) ||
        Number(["production", "delivery"].includes(b.id)) -
          Number(["production", "delivery"].includes(a.id)) ||
        tasks.indexOf(b) - tasks.indexOf(a),
    )
    .slice(0, 1);
  const visibleTasks =
    tasks.length > 6
      ? currentTasks.length
        ? currentTasks
        : latestHandoff
      : tasks;
  const otherTasks =
    tasks.length > 6
      ? tasks.filter((task) => !visibleTasks.includes(task))
      : [];
  const foldOrganization = tasks.length > 6 && groups.length > 1;
  const completedDecisionStatuses = new Set([
    "selected",
    "answered",
    "approved",
    "completed",
    "succeeded",
    "superseded",
    "cancelled",
    "skipped",
  ]);
  const currentDecisions = records.filter(
    (record) => record.current_gate_version,
  );
  const latestDecision = [
    ...(currentDecisions.length ? currentDecisions : records),
  ]
    .sort(
      (a, b) =>
        timestamp(a.finished_at || a.started_at) -
        timestamp(b.finished_at || b.started_at),
    )
    .pop();
  const visibleDecisions = records.filter(
    (record) =>
      record === latestDecision ||
      !completedDecisionStatuses.has(record.status),
  );
  const historicalDecisions = records.filter(
    (record) => !visibleDecisions.includes(record),
  );
  const executionEntries = rows(flow.nodes)
    .flatMap((node) =>
      rows(node.executions).map((execution) => {
        const value = execution.started_at || execution.finished_at;
        const numeric = Number(value);
        return {
          key: `${node.id}:${execution.id}`,
          role: node.role || node.id,
          execution,
          time: Number.isFinite(numeric) ? numeric : Date.parse(value) || 0,
        };
      }),
    )
    .sort((a, b) => b.time - a.time || a.key.localeCompare(b.key));
  const [executionView, setExecutionView] = useState(() => ({
    project: flow.project_id,
    role: "all",
    page: 0,
    keys: executionEntries.slice(0, 6).map((entry) => entry.key),
  }));
  const executionRole =
    executionView.project === flow.project_id ? executionView.role : "all";
  const executionRoles = [
    ...new Set([
      ...executionEntries.map((entry) => entry.role),
      ...(executionRole === "all" ? [] : [executionRole]),
    ]),
  ];
  const filteredExecutions = executionEntries.filter(
    (entry) => executionRole === "all" || entry.role === executionRole,
  );
  const executionPages = Math.max(1, Math.ceil(filteredExecutions.length / 6));
  const executionPage = Math.min(
    executionView.project === flow.project_id ? executionView.page : 0,
    executionPages - 1,
  );
  const latestPage = filteredExecutions.slice(
    executionPage * 6,
    executionPage * 6 + 6,
  );
  // Keep the current reading window during polling. New calls must not unmount
  // an expanded request or document by pushing it onto another page.
  const savedPage =
    executionView.project === flow.project_id &&
    executionView.role === executionRole &&
    executionView.page === executionPage
      ? executionView.keys.flatMap((key) => {
          const entry = filteredExecutions.find((item) => item.key === key);
          return entry ? [entry] : [];
        })
      : [];
  const visibleExecutions = savedPage.length ? savedPage : latestPage;
  const executionKeys = JSON.stringify(
    visibleExecutions.map((entry) => entry.key),
  );
  const executionOrderChanged =
    executionKeys !== JSON.stringify(latestPage.map((entry) => entry.key));
  useEffect(() => {
    setExecutionView((current) =>
      current.project === flow.project_id &&
      current.role === executionRole &&
      current.page === executionPage &&
      JSON.stringify(current.keys) === executionKeys
        ? current
        : {
            project: flow.project_id,
            role: executionRole,
            page: executionPage,
            keys: JSON.parse(executionKeys) as string[],
          },
    );
  }, [flow.project_id, executionRole, executionPage, executionKeys]);
  const showExecutions = (role: string, page: number) => {
    const entries = executionEntries.filter(
      (entry) => role === "all" || entry.role === role,
    );
    setExecutionView({
      project: flow.project_id,
      role,
      page,
      keys: entries.slice(page * 6, page * 6 + 6).map((entry) => entry.key),
    });
  };
  const organization = (
    <>
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
    </>
  );
  const renderTask = (t: RecordData) => (
    <article className="task-card" data-team-task={t.id} key={t.id}>
      <div className="row-between">
        <h3>{t.label}</h3>
        <Badge
          status={t.current === false ? "pending" : t.status || "pending"}
        />
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
  );
  const renderDecision = (r: RecordData) => (
    <article
      className="supervisor-record"
      key={`${r.kind}:${r.gate_id || r.run_id || r.started_at || "saved"}`}
    >
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
      <small>{r.requested_model || "模型未记录"} · 不代表已观看成片</small>
    </article>
  );
  return (
    <div className="stack">
      {stale && (
        <div className="notice">
          团队记录正在同步；以下保留上次执行记录，旧交付链接暂时停用。
        </div>
      )}
      <Panel
        title="当前任务与交接"
        description="工单完成和独立审核通过分别记录。"
      >
        <div className="task-grid">{visibleTasks.map(renderTask)}</div>
        {!tasks.length && (
          <Empty title="尚无当前任务记录">
            团队执行后显示实际工单，不根据阶段名称推测调用。
          </Empty>
        )}
        {tasks.length > 6 && !visibleTasks.length && (
          <p className="muted">
            暂无正在执行或待处理的工单，其余记录可按需展开。
          </p>
        )}
        {otherTasks.length > 0 && (
          <details>
            <summary>其他工单 · {otherTasks.length} 项</summary>
            <div className="task-grid">{otherTasks.map(renderTask)}</div>
          </details>
        )}
      </Panel>
      <Panel
        title={foldOrganization ? undefined : "制作团队与职责"}
        description={
          foldOrganization
            ? undefined
            : "负责人、独立审核和交接产物共同构成任务闭环。"
        }
      >
        {foldOrganization ? (
          <details>
            <summary>团队分工 · {groups.length} 个协作组</summary>
            {organization}
          </details>
        ) : (
          organization
        )}
      </Panel>
      {records.length > 0 && (
        <Panel title="总制片决定" className="supervisor-panel">
          {visibleDecisions.map(renderDecision)}
          {historicalDecisions.length > 0 && (
            <details>
              <summary>
                历史总制片决定 · {historicalDecisions.length} 条
              </summary>
              {historicalDecisions.map(renderDecision)}
            </details>
          )}
        </Panel>
      )}
      <Panel
        title="实际调用与回执"
        description="按实际调用查看请求、响应和重试；缺少步骤归属的旧调用保留为角色历史。"
      >
        {(executionEntries.length > 0 || executionRole !== "all") && (
          <div className="execution-history-toolbar">
            <label className="field">
              <span>调用角色</span>
              <select
                value={executionRole}
                onChange={(event) => showExecutions(event.target.value, 0)}
              >
                <option value="all">全部角色</option>
                {executionRoles.map((role) => (
                  <option key={role} value={role}>
                    {roleName(role)}
                  </option>
                ))}
              </select>
            </label>
            <p className="muted">
              共 {filteredExecutions.length} 条 · 最近执行优先
            </p>
            {executionOrderChanged && (
              <button
                className="button small"
                onClick={() => showExecutions(executionRole, 0)}
              >
                查看最新调用
              </button>
            )}
          </div>
        )}
        {visibleExecutions.map(({ key, role, execution }) => (
          <ExecutionRecord
            key={key}
            execution={
              stale
                ? { ...execution, artifacts: [], attempts: [], receipt: null }
                : execution
            }
            role={role}
            projectId={flow.project_id}
            onLocate={onInspect}
          />
        ))}
        {executionPages > 1 && (
          <nav className="pagination" aria-label="调用记录分页">
            <span>
              第 {executionPage + 1} / {executionPages} 页
            </span>
            <div className="button-row">
              <button
                className="button small"
                disabled={executionPage === 0}
                onClick={() => showExecutions(executionRole, executionPage - 1)}
              >
                上一页调用
              </button>
              <button
                className="button small"
                disabled={executionPage + 1 >= executionPages}
                onClick={() => showExecutions(executionRole, executionPage + 1)}
              >
                下一页调用
              </button>
            </div>
          </nav>
        )}
        {!filteredExecutions.length && (
          <p className="muted">
            {executionRole === "all"
              ? "尚无保存的执行记录。"
              : "该角色尚无保存的执行记录。"}
          </p>
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
