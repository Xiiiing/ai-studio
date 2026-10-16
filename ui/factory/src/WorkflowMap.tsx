import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { CornerDownLeft } from "lucide-react";
import { rows, type RecordData } from "./api";
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
      status,
      summary: current?.label || `${members.length} 个步骤 · ${done} 个已完成`,
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
      stageNodes.findIndex((n) => n.id === target) <
        stageNodes.findIndex((n) => n.id === source);
    const id = `${source}:${target}:${repair ? "repair" : "forward"}`;
    const existing = edges.get(id);
    const label = repair
      ? source === "revision"
        ? "返修后接续"
        : "按需返修"
      : "交付下一阶段";
    if (existing) {
      if (!existing.labels.includes(label)) existing.labels.push(label);
      continue;
    }
    edges.set(id, {
      id,
      source,
      target,
      repair,
      running: source === activeStage && (!repair || source === "revision"),
      completed:
        stageNodes.find((n) => n.id === source)?.status === "succeeded" &&
        stageNodes.find((n) => n.id === target)?.status === "succeeded",
      labels: [label],
    });
  }
  return { nodes: stageNodes, edges: [...edges.values()] };
}
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
) {
  const sx = middleX(source),
    sy = middleY(source),
    tx = middleX(target),
    ty = middleY(target);
  const sameRow =
    Math.abs(sy - ty) < Math.min(source.height, target.height) / 2;
  const sameColumn = Math.abs(sx - tx) < 5;
  if (sameRow && Math.abs(sx - tx) < source.width * 1.6) {
    const right = tx > sx,
      start = right ? source.x + source.width : source.x,
      end = right ? target.x : target.x + target.width,
      mid = (start + end) / 2;
    return `M ${start} ${sy} C ${mid} ${sy}, ${mid} ${ty}, ${end} ${ty}`;
  }
  if (sameColumn && !edge.repair) {
    const down = ty > sy,
      start = down ? source.y + source.height : source.y,
      end = down ? target.y : target.y + target.height,
      mid = (start + end) / 2;
    return `M ${sx} ${start} C ${sx} ${mid}, ${tx} ${mid}, ${tx} ${end}`;
  }
  const lane = bounds.height - (edge.source === "revision" ? 18 : 42);
  if (sameRow)
    return `M ${sx} ${source.y + source.height} L ${sx} ${lane} L ${tx} ${lane} L ${tx} ${target.y + target.height}`;
  // Reserve an outer lane for return routes so the line never crosses another stage card.
  const left = source.x < bounds.width / 2 || target.x < bounds.width / 2;
  const outer = left
    ? edge.source === "revision"
      ? 8
      : 18
    : bounds.width - (edge.source === "revision" ? 8 : 18);
  if (edge.source === "revision") {
    const end = left ? target.x : target.x + target.width;
    return `M ${sx} ${source.y + source.height} L ${sx} ${lane} L ${outer} ${lane} L ${outer} ${ty} L ${end} ${ty}`;
  }
  const start = left ? source.x : source.x + source.width;
  return `M ${start} ${sy} L ${outer} ${sy} L ${outer} ${lane} L ${tx} ${lane} L ${tx} ${target.y + target.height}`;
}
export function WorkflowMap({
  graph,
  groups,
  steps,
  selectedGroup,
  selectedStep,
  onSelect,
}: {
  graph: RecordData;
  groups: RecordData[];
  steps: RecordData[];
  selectedGroup?: string;
  selectedStep?: string;
  onSelect: (id: string) => void;
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
  const columns = measurement.width && measurement.width < 520 ? 2 : 3;
  const markerId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
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
        <strong>阶段与数据流向</strong>
        <div className="workflow-flow-legend">
          <span>
            <i className="flow-legend-running" />
            执行中
          </span>
          <span>
            <i className="flow-legend-completed" />
            已完成
          </span>
          <span>
            <i className="flow-legend-repair" />
            返修
          </span>
        </div>
      </div>
      <nav
        ref={ref}
        className="workflow-stages workflow-map"
        aria-label="制作阶段"
        style={{ "--flow-columns": columns } as CSSProperties}
      >
        <svg
          className="workflow-flow-lines"
          width={measurement.width}
          height={measurement.height}
          aria-hidden="true"
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
            const path = stageEdgePath(source, target, edge, measurement);
            const state = edge.running
              ? "running"
              : edge.repair
                ? "repair"
                : edge.completed
                  ? "completed"
                  : "pending";
            return (
              <g
                key={edge.id}
                data-flow-edge={edge.id}
                data-source={edge.source}
                data-target={edge.target}
                data-flow-state={state}
                className={`workflow-flow-edge is-${state}`}
              >
                <title>
                  {flow.nodes.find((n) => n.id === edge.source)?.label} →{" "}
                  {flow.nodes.find((n) => n.id === edge.target)?.label} ·{" "}
                  {edge.labels.join("、")}
                </title>
                <path
                  className="workflow-flow-line"
                  d={path}
                  markerEnd={`url(#${markerId}-${state})`}
                />
                {edge.running && (
                  <>
                    <path className="workflow-flow-stream" d={path} />
                    <circle className="workflow-flow-packet" r="3.5">
                      <animateMotion
                        dur="2.6s"
                        repeatCount="indefinite"
                        path={path}
                      />
                    </circle>
                  </>
                )}
              </g>
            );
          })}
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
                  <Badge status={node.status} />
                </span>
                <span className="workflow-stage-copy">
                  <strong>{node.label}</strong>
                  <small>{node.summary}</small>
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
      <p className="workflow-map-hint">点击节点查看输入、交付与内部步骤</p>
    </div>
  );
}
