import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { CornerDownLeft } from "lucide-react";
import { createPortal } from "react-dom";
import { rows, roleName, type RecordData } from "./api";
import { Badge } from "./ui";

export interface FlowStage extends RecordData {
  id: string;
  label: string;
  node_ids: string[];
  status: string;
  summary: string;
}
export interface StageEdge {
  id: string;
  source: string;
  target: string;
  repair: boolean;
  running: boolean;
  completed: boolean;
  labels: string[];
  handoffs?: RecordData[];
  separateReverse?: boolean;
}
export function stageFlow(
  graph: RecordData,
  groups: RecordData[],
  steps: RecordData[],
) {
  const active = steps.find((n) => n.id === graph.active_node);
  const stageNodes: FlowStage[] = groups.map((g) => {
    const members = steps.filter((n) => g.node_ids.includes(n.id));
    const current = members.find((n) => n.id === active?.id);
    const required = members.filter((n) => !n.conditional);
    const done = members.filter((n) => n.status === "succeeded").length;
    const status =
      (current?.status !== "succeeded" ? current?.status : undefined) ||
      members.find((n) =>
        ["blocked", "failed", "human", "paused", "running"].includes(n.status),
      )?.status ||
      (required.length && required.every((n) => n.status === "succeeded")
        ? "succeeded"
        : "pending");
    return {
      ...g,
      id: g.id,
      label: g.label,
      node_ids: g.node_ids,
      status:
        graph.flow_kind === "collaboration" && g.status ? g.status : status,
      completedSteps: done,
      totalSteps: members.length,
      ownerLabel:
        g.ownerLabel ||
        roleName(
          current?.owner ||
            members[0]?.owner ||
            members[0]?.role ||
            "orchestrator",
        ),
      summary:
        g.summary ||
        (members.length === 1
          ? members[0]?.output
            ? `交付：${members[0].output}`
            : "等待交付"
          : `${members.length} 个步骤 · ${done} 个已完成`),
    } as FlowStage;
  });
  const revision = steps.find((n) => n.id === "revision");
  if (revision)
    stageNodes.push({
      id: "revision",
      label: revision.label || "定向返修",
      node_ids: ["revision"],
      status: revision.status || "pending",
      summary: "修改后返回镜头与后期",
    });
  const owners = new Map(
    stageNodes.flatMap((n) => n.node_ids.map((id) => [id, n.id] as const)),
  );
  const activeStage =
    active?.status === "running" ? owners.get(active.id) : undefined;
  const edges = new Map<string, StageEdge>();
  for (const edge of rows(graph.edges)) {
    const source = owners.get(edge.source),
      target = owners.get(edge.target);
    if (!source || !target || source === target) continue;
    const repair =
      !!edge.loop ||
      source === "revision" ||
      target === "revision" ||
      (graph.flow_kind !== "collaboration" &&
        stageNodes.findIndex((n) => n.id === target) <
          stageNodes.findIndex((n) => n.id === source));
    const id = `${source}:${target}:${repair ? "repair" : "forward"}`;
    const existing = edges.get(id);
    const label =
      graph.flow_kind === "collaboration" && edge.label
        ? edge.label
        : repair
          ? source === "revision"
            ? "返修后接续"
            : "按需返修"
          : "交付下一阶段";
    if (existing) {
      if (!existing.labels.includes(label)) existing.labels.push(label);
      existing.handoffs?.push(edge);
      if (graph.flow_kind === "collaboration") {
        existing.running ||= !!edge.in_progress;
        existing.completed ||= !!edge.received_count;
      }
      continue;
    }
    edges.set(id, {
      id,
      source,
      target,
      repair,
      running:
        graph.flow_kind === "collaboration"
          ? !!edge.in_progress
          : source === activeStage && (!repair || source === "revision"),
      completed:
        graph.flow_kind === "collaboration"
          ? !!edge.received_count
          : stageNodes.find((n) => n.id === source)?.status === "succeeded" &&
            stageNodes.find((n) => n.id === target)?.status === "succeeded",
      labels: [label],
      handoffs: [edge],
    });
  }
  const links = [...edges.values()];
  for (const edge of links)
    edge.separateReverse =
      !edge.repair &&
      links.some(
        (other) =>
          !other.repair &&
          other.source === edge.target &&
          other.target === edge.source,
      );
  return { nodes: stageNodes, edges: links };
}
const stageTitles: Record<string, string> = {
  全剧设定与分集大纲: "设定与大纲",
  全剧规划独立审核: "规划审核",
  出品人确认全剧规划: "规划确认",
  按集制作与团队协作: "剧集制作",
  逐集验收与成果: "验收与成果",
  单集场景创作与独立审核: "场景与审核",
  分镜与制作单元: "分镜与单元",
  连续性检查与单集合成: "连续性与合成",
};
export interface FlowBox {
  x: number;
  y: number;
  width: number;
  height: number;
}
interface Measurement {
  width: number;
  height: number;
  boxes: Record<string, FlowBox>;
}
const middleX = (b: FlowBox) => b.x + b.width / 2;
const middleY = (b: FlowBox) => b.y + b.height / 2;
export function stageEdgePath(
  source: FlowBox,
  target: FlowBox,
  edge: StageEdge,
  bounds: { width: number; height: number },
  obstacles: FlowBox[] = [],
) {
  const sx = middleX(source),
    sy = middleY(source),
    tx = middleX(target),
    ty = middleY(target);
  const sameRow =
    Math.abs(sy - ty) < Math.min(source.height, target.height) / 2;
  const sameColumn = Math.abs(sx - tx) < 5;
  const blockedRow = obstacles.some(
    (b) =>
      b !== source &&
      b !== target &&
      sy > b.y + 1 &&
      sy < b.y + b.height - 1 &&
      b.x < Math.max(source.x, target.x) &&
      b.x + b.width >
        Math.min(source.x + source.width, target.x + target.width),
  );
  if (edge.separateReverse && sameRow && !blockedRow) {
    const right = tx > sx,
      offset = right ? -20 : 20;
    const start = right ? source.x + source.width : source.x,
      end = right ? target.x : target.x + target.width;
    return `M ${start} ${sy + offset} L ${end} ${ty + offset}`;
  }
  if (sameRow && !edge.repair && !blockedRow) {
    const right = tx > sx,
      start = right ? source.x + source.width : source.x,
      end = right ? target.x : target.x + target.width,
      mid = (start + end) / 2;
    return `M ${start} ${sy} C ${mid} ${sy}, ${mid} ${ty}, ${end} ${ty}`;
  }
  const verticalStart = Math.min(
    source.y + source.height,
    target.y + target.height,
  );
  const verticalEnd = Math.max(source.y, target.y);
  const blockedColumn = obstacles.some(
    (b) =>
      b !== source &&
      b !== target &&
      sx > b.x + 1 &&
      sx < b.x + b.width - 1 &&
      b.y < verticalEnd &&
      b.y + b.height > verticalStart,
  );
  if (sameColumn && !edge.repair && !blockedColumn) {
    const down = ty > sy,
      start = down ? source.y + source.height : source.y,
      end = down ? target.y : target.y + target.height,
      mid = (start + end) / 2;
    const offset = edge.separateReverse ? (down ? -20 : 20) : 0;
    return `M ${sx + offset} ${start} C ${sx + offset} ${mid}, ${tx + offset} ${mid}, ${tx + offset} ${end}`;
  }
  // Route returns through the gaps above rows and the reserved outer gutter.
  // A line from a card's bottom to a bottom lane could cut through lower cards.
  const offset = edge.source === "revision" ? 12 : 22;
  const sourceLane = Math.max(6, source.y - offset);
  const targetLane = Math.max(6, target.y - offset);
  if (sameRow)
    return `M ${sx} ${source.y} L ${sx} ${sourceLane} L ${tx} ${sourceLane} L ${tx} ${target.y}`;
  const outer =
    edge.source === "revision" || (edge.separateReverse && sy > ty)
      ? bounds.width - 8
      : 8;
  return `M ${sx} ${source.y} L ${sx} ${sourceLane} L ${outer} ${sourceLane} L ${outer} ${targetLane} L ${tx} ${targetLane} L ${tx} ${target.y}`;
}
export function WorkflowMap({
  graph,
  groups,
  steps,
  selectedGroup,
  selectedStep,
  onSelect,
  caption = "阶段与数据流向",
  navigationLabel = "制作阶段",
  hint = "点击节点查看输入、交付与内部步骤",
  maxColumns = 5,
  onEdgeSelect,
  selectedEdge,
}: {
  graph: RecordData;
  groups: RecordData[];
  steps: RecordData[];
  selectedGroup?: string;
  selectedStep?: string;
  onSelect: (id: string) => void;
  caption?: string;
  navigationLabel?: string;
  hint?: string;
  maxColumns?: number;
  onEdgeSelect?: (edge: StageEdge) => void;
  selectedEdge?: string;
}) {
  const flow = useMemo(
    () => stageFlow(graph, groups, steps),
    [graph, groups, steps],
  );
  const ref = useRef<HTMLElement>(null);
  const [measurement, setMeasurement] = useState<Measurement>({
    width: 0,
    height: 0,
    boxes: {},
  });
  const columns = Math.max(
    1,
    Math.min(
      maxColumns,
      flow.nodes.length || 1,
      measurement.width && measurement.width < 520
        ? 2
        : measurement.width >= 1050 && flow.nodes.length <= 5
          ? flow.nodes.length
          : 3,
    ),
  );
  const markerId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const [labelLayer, setLabelLayer] = useState<SVGGElement | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      const boxes: Record<string, FlowBox> = {};
      element
        .querySelectorAll<HTMLElement>("[data-map-node]")
        .forEach((node) => {
          const box = node.getBoundingClientRect();
          boxes[node.dataset.mapNode!] = {
            x: box.left - rect.left,
            y: box.top - rect.top,
            width: box.width,
            height: box.height,
          };
        });
      const next = { width: rect.width, height: rect.height, boxes };
      setMeasurement((previous) =>
        JSON.stringify(previous) === JSON.stringify(next) ? previous : next,
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    element
      .querySelectorAll("[data-map-node]")
      .forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [flow.nodes, columns]);
  return (
    <div className="workflow-map-panel">
      <div className="workflow-map-caption">
        <strong>{caption}</strong>
        <div className="workflow-flow-legend">
          <span>
            <i
              className="flow-legend-running"
              data-status-indicator="running"
              aria-hidden="true"
            />
            执行中
          </span>
          <span>
            <i
              className="flow-legend-completed"
              data-status-indicator="success"
              aria-hidden="true"
            />
            已完成
          </span>
          <span>
            <i
              className="flow-legend-repair"
              data-status-indicator="review"
              aria-hidden="true"
            />
            返修
          </span>
        </div>
      </div>
      <nav
        ref={ref}
        className="workflow-stages workflow-map"
        aria-label={navigationLabel}
        style={{ "--flow-columns": columns } as CSSProperties}
      >
        <svg
          className={`workflow-flow-lines ${onEdgeSelect ? "is-interactive" : ""}`}
          width={measurement.width}
          height={measurement.height}
          aria-hidden={onEdgeSelect ? undefined : true}
          aria-label={onEdgeSelect ? "工作交接连线" : undefined}
        >
          <defs>
            {["pending", "running", "completed", "repair"].map((state) => (
              <marker
                key={state}
                id={`${markerId}-${state}`}
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path
                  d="M 1 1 L 9 5 L 1 9 z"
                  className={`flow-arrow-${state}`}
                />
              </marker>
            ))}
          </defs>
          {flow.edges.map((edge) => {
            const source = measurement.boxes[edge.source],
              target = measurement.boxes[edge.target];
            if (!source?.width || !target?.width) return null;
            const path = stageEdgePath(
              source,
              target,
              edge,
              measurement,
              Object.values(measurement.boxes),
            );
            const state = edge.running
              ? "running"
              : edge.repair
                ? "repair"
                : edge.completed
                  ? "completed"
                  : "pending";
            return (
              <InteractiveEdge
                key={edge.id}
                edge={edge}
                path={path}
                state={state}
                markerId={markerId}
                width={measurement.width}
                labelLayer={labelLayer}
                label={`${flow.nodes.find((n) => n.id === edge.source)?.label} → ${flow.nodes.find((n) => n.id === edge.target)?.label}`}
                selected={selectedEdge === edge.id}
                onSelect={onEdgeSelect ? () => onEdgeSelect(edge) : undefined}
              />
            );
          })}
          {onEdgeSelect && (
            <g ref={setLabelLayer} className="workflow-edge-labels" />
          )}
        </svg>
        <div className="workflow-map-grid">
          {flow.nodes.map((node, index) => {
            const row = Math.floor(index / columns),
              column = index % columns;
            const selected =
              node.id === selectedGroup ||
              (node.id === "revision" && selectedStep === "revision");
            return (
              <button
                key={node.id}
                className={`workflow-stage-card workflow-map-node status-${node.status} ${selected ? "selected" : ""} ${node.id === "revision" ? "workflow-repair-node" : ""}`}
                data-stage={node.id !== "revision" ? node.id : undefined}
                data-map-node={node.id}
                aria-pressed={selected}
                onClick={() => onSelect(node.id)}
                style={{
                  gridRow: row + 1,
                  gridColumn: (row % 2 ? columns - 1 - column : column) + 1,
                }}
              >
                <span className="workflow-map-node-top">
                  <span className="workflow-stage-number">
                    {node.id === "revision" ? (
                      <CornerDownLeft size={15} />
                    ) : (
                      String(index + 1).padStart(2, "0")
                    )}
                  </span>
                  <Badge status={node.status} label={node.status_label} />
                </span>
                <span className="workflow-stage-copy">
                  <strong title={node.label}>
                    {stageTitles[node.label] || node.label}
                  </strong>
                  <small className="workflow-node-owner">
                    {node.ownerLabel || "按需返修"}
                  </small>
                  <small className="workflow-node-summary">
                    {node.summary}
                  </small>
                </span>
                {node.node_ids.some((id) =>
                  rows(graph.edges).some(
                    (e) =>
                      e.loop &&
                      e.source === id &&
                      node.node_ids.includes(e.target),
                  ),
                ) && (
                  <small className="workflow-internal-repair">含审核返修</small>
                )}
                {graph.flow_kind !== "collaboration" && node.totalSteps > 0 && (
                  <span
                    className="workflow-node-progress"
                    aria-label={`${node.completedSteps} / ${node.totalSteps} 步骤已完成`}
                  >
                    {Array.from({ length: node.totalSteps }, (_, i) => (
                      <i
                        key={i}
                        className={i < node.completedSteps ? "done" : ""}
                      />
                    ))}
                  </span>
                )}
                {["top", "right", "bottom", "left"].map((side) => (
                  <i
                    key={side}
                    className={`workflow-port port-${side}`}
                    aria-hidden="true"
                  />
                ))}
              </button>
            );
          })}
        </div>
      </nav>
      <p className="workflow-map-hint">{hint}</p>
    </div>
  );
}

function InteractiveEdge({
  edge,
  path,
  state,
  markerId,
  width,
  labelLayer,
  label,
  selected,
  onSelect,
}: {
  edge: StageEdge;
  path: string;
  state: string;
  markerId: string;
  width: number;
  labelLayer: SVGGElement | null;
  label: string;
  selected: boolean;
  onSelect?: () => void;
}) {
  const ref = useRef<SVGPathElement>(null);
  const groupRef = useRef<SVGGElement>(null);
  const [anchor, setAnchor] = useState<{ x: number; y: number }>();
  useLayoutEffect(() => {
    const line = ref.current;
    if (!line || !onSelect || !line.getTotalLength) return;
    const point = line.getPointAtLength(line.getTotalLength() / 2);
    setAnchor({ x: Math.max(20, Math.min(width - 20, point.x)), y: point.y });
  }, [path, !!onSelect, width]);
  const count =
    edge.handoffs?.reduce((n, h) => n + (h.received_count || 0), 0) || 0;
  const content = count
    ? `${count} 份已接收`
    : edge.labels.length > 1
      ? `${edge.labels.length} 项交接`
      : edge.labels[0];
  const short = count
    ? `${count}份`
    : edge.labels.length > 1
      ? `${edge.labels.length}项`
      : edge.repair
        ? "退回"
        : /大纲/.test(content)
          ? "大纲"
          : /设定/.test(content)
            ? "设定"
            : /场景/.test(content)
              ? "场景"
              : /审核|修改/.test(content)
                ? "意见"
                : /成片|制作结果/.test(content)
                  ? "成片"
                  : "交付";
  return (
    <g
      ref={groupRef}
      data-flow-edge={edge.id}
      data-source={edge.source}
      data-target={edge.target}
      data-flow-state={state}
      className={`workflow-flow-edge is-${state} ${selected ? "is-selected" : ""}`}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-label={
        onSelect ? `查看交付：${label} · ${edge.labels.join("、")}` : undefined
      }
      aria-pressed={onSelect ? selected : undefined}
      onClick={
        onSelect
          ? () => {
              groupRef.current?.focus({ preventScroll: true });
              onSelect();
            }
          : undefined
      }
      onPointerDown={
        onSelect
          ? () => groupRef.current?.focus({ preventScroll: true })
          : undefined
      }
      onKeyDown={
        onSelect
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect();
              }
            }
          : undefined
      }
    >
      <title>
        {label} · {edge.labels.join("、")}
      </title>
      {onSelect && <path className="workflow-flow-hit" d={path} />}
      <path
        ref={ref}
        className="workflow-flow-line"
        d={path}
        markerEnd={`url(#${markerId}-${state})`}
      />
      {edge.running && (
        <>
          <path className="workflow-flow-stream" d={path} />
          <circle className="workflow-flow-packet" r="3.5">
            <animateMotion dur="2.6s" repeatCount="indefinite" path={path} />
          </circle>
        </>
      )}
      {onSelect &&
        anchor &&
        labelLayer &&
        createPortal(
          <g
            className="workflow-edge-label"
            data-label-edge={edge.id}
            transform={`translate(${anchor.x},${anchor.y})`}
            aria-hidden="true"
          >
            <rect x={-18} y={-14} width={36} height={28} rx={6} />
            <text textAnchor="middle" dominantBaseline="central">
              {short}
            </text>
          </g>,
          labelLayer,
        )}
    </g>
  );
}
