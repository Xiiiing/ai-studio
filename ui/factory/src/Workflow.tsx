import { useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  type NodeProps,
  type Node,
  type Edge,
  MarkerType,
} from "@xyflow/react";
import { ArrowRight, Users, FileText, CircleCheck } from "lucide-react";
import { rows, roleName, mediaUrl, date, text, type RecordData } from "./api";
import { Badge, Panel, Empty, Readable, Artifact } from "./ui";
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
    return { ...n, members };
  });
}
function StageNode({ data }: NodeProps<Node<RecordData>>) {
  return (
    <div
      className={"stage-node status-" + data.status}
      data-flow-node={data.id}
    >
      <Handle type="target" position={Position.Left} />
      <div className="stage-top">
        <span>{data.order}</span>
        <small>{roleName(data.owner || data.role || "orchestrator")}</small>
      </div>
      <strong>{data.label || data.id}</strong>
      <Badge status={data.status || "pending"} />
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { stage: StageNode };
export function Workflow({
  graph,
  flow,
  attention,
  onDecision,
  onShot,
}: {
  graph: RecordData;
  flow?: RecordData;
  attention?: RecordData;
  onDecision?: () => void;
  onShot?: (id: string) => void;
}) {
  const [selection, setSelection] = useState("");
  const stages = useMemo(() => workflowNodes(graph, flow), [graph, flow]);
  const selected =
    stages.find((n) => n.id === selection) ||
    stages.find((n) => n.id === graph.active_node) ||
    stages[0];
  const nodes: Node[] = stages.map((n, i) => ({
    id: n.id,
    type: "stage",
    position: { x: (i % 3) * 300, y: Math.floor(i / 3) * 185 },
    data: { ...n, order: String(i + 1).padStart(2, "0") },
    selected: n.id === selected?.id,
    draggable: false,
  }));
  const ids = new Set(nodes.map((n) => n.id));
  const edges: Edge[] = rows(graph.edges)
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e, i) => ({
      id: e.id || e.source + "-" + e.target + "-" + i,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      label: e.loop || e.conditional ? e.label || "返修" : "",
      animated: graph.active_node === e.source,
      style: { stroke: e.loop ? "#c59a56" : "#b9c5d6" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#a0aec0" },
    }));
  if (!stages.length)
    return (
      <Empty title="尚未返回执行图">
        流程定义由服务器提供，连接恢复后可查看真实节点。
      </Empty>
    );
  const productionTasks = rows(
    graph.production_tasks || flow?.production_tasks,
  );
  const histories = rows(flow?.nodes).filter(
    (n) => selected.members.includes(n.role) || n.id === selected.id,
  );
  return (
    <section className="flow-workspace" aria-label="制作工作流">
      <header className="flow-toolbar">
        <div>
          <span className="live-dot" />
          <strong>{graph.current_activity || "制作执行图"}</strong>
          <small>
            {graph.revision ? "版本 " + graph.revision : "标准流程 · 只读"}
          </small>
        </div>
        <span className="muted">选择节点查看职责与产物</span>
      </header>
      <div className="flow-body">
        <div className="flow-stage">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodeClick={(_, n) => setSelection(n.id)}
            fitView
            minZoom={0.35}
            maxZoom={1.4}
            nodesConnectable={false}
            nodesDraggable={false}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#d7dee9" gap={20} />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              nodeColor={(n) =>
                n.data.status === "running"
                  ? "#436ee0"
                  : n.data.status === "human"
                    ? "#daa94c"
                    : "#d8e0ed"
              }
            />
          </ReactFlow>
        </div>
        <aside className="node-inspector" aria-label="节点详情">
          <div className="eyebrow">NODE DETAIL</div>
          <h3>{selected.label || selected.id}</h3>
          <Badge status={selected.status} />
          <dl className="inspector-fields">
            <div>
              <dt>负责人</dt>
              <dd>
                {roleName(selected.owner || selected.role || "orchestrator")}
              </dd>
            </div>
            <div>
              <dt>接收</dt>
              <dd>
                {text(
                  selected.input ||
                    rows(selected.inputs)
                      .map((i) => i.label)
                      .join("、"),
                ) || "查看输入记录"}
              </dd>
            </div>
            <div>
              <dt>交付</dt>
              <dd>
                {text(
                  selected.output ||
                    rows(selected.outputs)
                      .map((i) => i.label)
                      .join("、"),
                ) || "尚未记录"}
              </dd>
            </div>
            <div>
              <dt>执行次数</dt>
              <dd>{selected.attempt || "尚未执行"}</dd>
            </div>
          </dl>
          <h4>参与成员与审核</h4>
          <div className="member-chips">
            {selected.members.map((r: string) => (
              <span key={r}>
                <Users size={13} />
                {roleName(r)}
              </span>
            ))}
          </div>
          {histories.map((n) => (
            <details key={n.id}>
              <summary>{roleName(n.role)} · 输入与交付</summary>
              {rows(n.inputs).map((v, i) => (
                <div className="io-item" key={"i" + i}>
                  <small>接收 · {v.label}</small>
                  <Readable value={v.value} />
                </div>
              ))}
              {rows(n.outputs).map((v, i) => (
                <div className="io-item" key={"o" + i}>
                  <small>交付 · {v.label}</small>
                  <Readable value={v.value} />
                  {v.artifact_url && <Artifact item={v} />}
                </div>
              ))}
            </details>
          ))}
          {attention?.blocking && selected.id === graph.active_node && (
            <button className="button primary full" onClick={onDecision}>
              处理当前事项
              <ArrowRight size={16} />
            </button>
          )}
          <p className="muted">
            状态依据：
            {(
              {
                task_receipt: "实际工单",
                decision_receipt: "已保存的决定",
                current_project_state: "当前项目状态",
                not_recorded: "尚无记录",
              } as RecordData
            )[selected.evidence] ||
              selected.evidence ||
              "服务器定义"}
          </p>
        </aside>
      </div>
      {selected.id === "production" && (
        <div className="production-tasks">
          <h3>镜头与后期实际任务</h3>
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
                      v{t.production_revision} · {t.attempt} 次
                    </td>
                    <td>
                      <Badge status={t.status} />
                    </td>
                    <td>
                      {t.artifact && (
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
          {!productionTasks.length && (
            <p className="muted">
              尚无实际制作记录，生成后显示镜头、配音、剪辑与质检子任务。
            </p>
          )}
        </div>
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
}: {
  flow: RecordData;
  graph: RecordData;
  models?: RecordData;
  projectRevision: number;
  onOpen?: (tab: string) => void;
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
        title="Agent 指挥中心"
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
                  {(t.inputs || [])
                    .map((v: any) => (typeof v === "object" ? v.label : v))
                    .join(" · ") || "创作需求"}
                </span>
                <small>交付</small>
                <span>
                  {(t.outputs || [])
                    .map((v: any) => (typeof v === "object" ? v.label : v))
                    .join(" · ") || "尚未记录"}
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
                  onOpen?.(
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
        description="这里展示历史实际模型，调整当前配置不会改写历史。"
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>成员</th>
                <th>实际模型</th>
                <th>状态</th>
                <th>时间</th>
                <th>证据</th>
              </tr>
            </thead>
            <tbody>
              {rows(flow.nodes).flatMap((n) =>
                rows(n.executions).map((e, i) => (
                  <tr key={n.id + ":" + i}>
                    <td>{roleName(n.role)}</td>
                    <td>
                      {e.model || "未记录"}
                      <small>
                        {e.model_source === "response"
                          ? "服务返回模型"
                          : "请求记录"}
                      </small>
                    </td>
                    <td>
                      <Badge status={e.status} />
                    </td>
                    <td>{date(e.finished_at || e.started_at)}</td>
                    <td>
                      {mediaUrl(e.receipt) && (
                        <Artifact
                          item={{ url: e.receipt, label: "查看回执" }}
                        />
                      )}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
        <details>
          <summary>输入、交付与依赖</summary>
          {rows(flow.nodes).map((n) => (
            <article className="io-record" key={n.id}>
              <h3>{n.label || roleName(n.role)}</h3>
              {rows(n.outputs).map((v, i) => (
                <div key={i}>
                  <strong>{v.label}</strong>
                  <Readable value={v.value} />
                  {v.artifact_url && <Artifact item={v} />}
                </div>
              ))}
            </article>
          ))}
          <p>
            {rows(flow.edges)
              .map((e) => e.label)
              .filter(Boolean)
              .join(" · ")}
          </p>
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
