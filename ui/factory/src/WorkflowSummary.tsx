import { ArrowRight } from "lucide-react";
import { rows, roleName, type RecordData } from "./api";
import { Badge } from "./ui";
import type { CanvasSelection } from "./FixedWorkflowCanvas";
import {
  handoffForPhases,
  type collaborationModel,
} from "./WorkflowCollaboration";

type Model = ReturnType<typeof collaborationModel>;
export function selectedHandoffs(
  selection: CanvasSelection,
  model: Model,
  groups: RecordData[],
) {
  return model.handoffs
    .filter((h) => selection.handoffs?.includes(h.id))
    .map((h) =>
      selection.sourceStage && selection.targetStage
        ? handoffForPhases(
            h,
            model,
            groups.find((g) => g.id === selection.sourceStage)?.node_ids || [
              selection.sourceStage,
            ],
            groups.find((g) => g.id === selection.targetStage)?.node_ids || [
              selection.targetStage,
            ],
          )
        : h,
    )
    .sort((a, b) => b.received_count - a.received_count);
}

/** A bounded summary never mounts long documents or scrollable record readers. */
export function WorkflowSummary({
  graph,
  flow,
  steps,
  groups,
  model,
  selection,
  selected,
  onClose,
  onOpen,
  onDecision,
  blocking,
}: {
  graph: RecordData;
  flow: RecordData;
  steps: RecordData[];
  groups: RecordData[];
  model: Model;
  selection?: CanvasSelection;
  selected: RecordData;
  onClose: () => void;
  onOpen: (view: string) => void;
  onDecision?: () => void;
  blocking: boolean;
}) {
  const phaseIds = selection?.stage
    ? groups.find((g) => g.id === selection.stage)?.node_ids || [
        selection.stage,
      ]
    : undefined;
  const department = model.departments.find(
    (d) => d.id === (selection?.department || selection?.id),
  );
  const member =
    selection?.kind === "role"
      ? model.members.find((m) => m.id === selection.id)
      : undefined;
  const assignments =
    member?.assignments.filter((s) => !phaseIds || phaseIds.includes(s.id)) ||
    [];
  const tasks =
    member?.savedTasks.filter(
      (t) => !phaseIds || !t.stage_id || phaseIds.includes(t.stage_id),
    ) || [];
  const calls =
    member?.executions.filter(
      (e) => !phaseIds || !e.stage || phaseIds.includes(e.stage),
    ) || [];
  const handoffs = selection ? selectedHandoffs(selection, model, groups) : [];
  const edge =
    selection?.kind === "handoff" || selection?.kind === "stage-edge";
  const stageEdge = selection?.kind === "stage-edge";
  const phase = (id?: string) =>
    groups.find((g) => g.id === id)?.label ||
    steps.find((s) => s.id === id)?.label ||
    id;
  const title = !selection
    ? "查看工作与交付"
    : selection.kind === "step"
      ? selected.label
      : stageEdge
        ? `${phase(selection.sourceStage)} → ${phase(selection.targetStage)}`
        : edge
          ? [
              ...new Set(
                handoffs.map(
                  (h) => `${roleName(h.source)} → ${roleName(h.target)}`,
                ),
              ),
            ].join("、") || "岗位交接"
          : member?.label || department?.label || "部门分工";
  const received = handoffs.reduce((n, h) => n + h.received_count, 0);
  const running = handoffs.some((h) => h.in_progress);
  const material = rows(flow.steps).find((s) => s.id === selected.id);
  const inputItems = material
    ? rows(material.inputs)
    : rows(flow.nodes)
        .filter((n) => selected.members.includes(n.role))
        .flatMap((n) => rows(n.inputs));
  const outputItems = material
    ? rows(material.outputs)
    : rows(flow.nodes)
        .filter((n) => selected.members.includes(n.role))
        .flatMap((n) => rows(n.outputs));
  const status =
    selection?.kind === "step"
      ? selected.status
      : edge
        ? running
          ? "running"
          : received
            ? "succeeded"
            : "pending"
        : member
          ? assignments.some((s) => s.id === graph.active_node)
            ? member.status
            : tasks.some(
                  (t) =>
                    (!phaseIds || phaseIds.includes(t.stage_id)) &&
                    ["succeeded", "completed"].includes(t.status),
                )
              ? "succeeded"
              : "pending"
          : undefined;
  return (
    <aside
      className="workflow-inspector workflow-fixed-inspector workflow-summary"
      aria-label="工作摘要"
    >
      <div className="workflow-fixed-inspector-tools">
        <small>
          {edge
            ? "交付概况"
            : selection?.kind === "role"
              ? "岗位概况"
              : selection?.kind === "department"
                ? "部门概况"
                : "步骤概况"}
        </small>
        {selection && (
          <button className="text-link" onClick={onClose} aria-label="关闭摘要">
            收起
          </button>
        )}
      </div>
      <header className="workflow-summary-heading">
        <h3>{title}</h3>
        {status && <Badge status={status} />}
      </header>
      {!selection ? (
        <p className="muted">
          点击阶段、部门或岗位查看工作，点击连线查看谁把什么交给了谁。
        </p>
      ) : (
        <>
          {selection.kind === "step" ? (
            <>
              <p className="workflow-summary-owner">
                {roleName(selected.owner || selected.role || "orchestrator")}
                负责
                {selected.reviewer
                  ? ` · ${roleName(selected.reviewer)}审核`
                  : ""}
              </p>
              <dl className="workflow-summary-contract">
                <div>
                  <dt>接收</dt>
                  <dd>{selected.input || "查看已保存的输入材料"}</dd>
                </div>
                <div>
                  <dt>交付</dt>
                  <dd>{selected.output || "查看已保存的交付内容"}</dd>
                </div>
              </dl>
              <div className="workflow-summary-links">
                <button onClick={() => onOpen("materials")}>
                  <span>
                    材料与分工
                    <small>
                      {inputItems.length} 份输入 · {outputItems.length} 份交付 ·{" "}
                      {selected.members.length} 个岗位
                    </small>
                  </span>
                  <ArrowRight size={16} />
                </button>
                <button onClick={() => onOpen("records")}>
                  <span>
                    执行记录<small>查看实际调用、过程与结果</small>
                  </span>
                  <ArrowRight size={16} />
                </button>
              </div>
              {blocking && selected.id === graph.active_node && (
                <button className="button primary" onClick={onDecision}>
                  处理当前事项
                  <ArrowRight size={16} />
                </button>
              )}
            </>
          ) : edge ? (
            <>
              <p className="muted">
                {running
                  ? "接收方正在处理已交付内容。"
                  : received
                    ? `已记录 ${received} 次接收，可核对原始交付与接收结果。`
                    : "交付关系已定义，尚无可核对的接收记录。"}
              </p>
              {handoffs.length > 0 && (
                <ul className="workflow-summary-handoffs">
                  {handoffs.slice(0, 3).map((h) => (
                    <li key={h.id}>
                      <span>
                        {roleName(h.source)} → {roleName(h.target)}
                      </span>
                      <small>
                        {h.received_count
                          ? `${h.received_count} 次接收`
                          : "待接收"}
                      </small>
                    </li>
                  ))}
                </ul>
              )}
              {handoffs.length > 3 && (
                <p className="muted">另有 {handoffs.length - 3} 组岗位交接</p>
              )}
            </>
          ) : member ? (
            <>
              <p className="workflow-summary-owner">{member.ownerLabel}</p>
              <dl className="workflow-summary-contract">
                <div>
                  <dt>工作</dt>
                  <dd>
                    {assignments.map((s) => s.label).join("、") ||
                      "本阶段暂无分工"}
                  </dd>
                </div>
                <div>
                  <dt>交付</dt>
                  <dd>
                    {assignments
                      .map((s) => s.output)
                      .filter(Boolean)
                      .join("；") || "以实际工作记录为准"}
                  </dd>
                </div>
                <div>
                  <dt>记录</dt>
                  <dd>
                    {tasks.length} 份工单 · {calls.length} 次模型调用
                  </dd>
                </div>
              </dl>
            </>
          ) : (
            <>
              <p className="muted">
                {department?.responsibility || "按本阶段的分工协作完成交付。"}
              </p>
              <div className="workflow-summary-roster">
                {model.members
                  .filter(
                    (m) =>
                      department?.node_ids.includes(m.id) &&
                      m.assignments.some(
                        (s) => !phaseIds || phaseIds.includes(s.id),
                      ),
                  )
                  .map((m) => (
                    <span key={m.id}>{m.label}</span>
                  ))}
              </div>
            </>
          )}
          <button
            className="button workflow-summary-open"
            onClick={() => onOpen("materials")}
          >
            查看完整详情
            <ArrowRight size={16} />
          </button>
          <p className="workflow-summary-hint">
            完整材料与记录将在独立卡片中打开
          </p>
        </>
      )}
    </aside>
  );
}
