import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ChevronDown,
  ChevronRight,
  LocateFixed,
  Minus,
  Plus,
  Scan,
} from "lucide-react";
import { type RecordData, roleName } from "./api";
import { Badge } from "./ui";
import { stageFlow, stageTitles } from "./WorkflowMap";
import {
  handoffForPhases,
  type collaborationModel,
} from "./WorkflowCollaboration";

type Model = ReturnType<typeof collaborationModel>;
export type CanvasSelection = {
  kind: "step" | "department" | "role" | "handoff" | "stage-edge";
  id: string;
  stage?: string;
  department?: string;
  handoffs?: string[];
  sourceStage?: string;
  targetStage?: string;
};
type Box = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  stage: string;
  kind: string;
  data: RecordData;
  parent?: string;
};
export function fixedWorkflowLayout(
  graph: RecordData,
  groups: RecordData[],
  steps: RecordData[],
  model: Model,
) {
  const phases = stageFlow(graph, groups, steps);
  const boxes: Box[] = [];
  phases.nodes.forEach((phase, index) => {
    const x = 48 + index * 292;
    const rootY = phase.id === "revision" ? 352 : 80;
    boxes.push({
      id: phase.id,
      stage: phase.id,
      kind: "stage",
      x,
      y: rootY,
      width: 212,
      height: 168,
      data: phase,
    });
    const members = new Set(
      steps
        .filter((s) => phase.node_ids.includes(s.id))
        .flatMap((s) => s.members || []),
    );
    let y = rootY + 272;
    model.departments.forEach((dept) => {
      const roles = dept.node_ids.filter((r) => members.has(r));
      if (!roles.length) return;
      const id = `${phase.id}/${dept.id}`;
      boxes.push({
        id,
        stage: phase.id,
        kind: "department",
        x: x + 8,
        y,
        width: 204,
        height: 116,
        data: { ...dept, node_ids: roles },
        parent: phase.id,
      });
      roles.forEach((role, ri) => {
        boxes.push({
          id: `${id}/${role}`,
          stage: phase.id,
          kind: "role",
          x: x + 24,
          y: y + 148 + ri * 112,
          width: 188,
          height: 92,
          data: model.members.find((m) => m.id === role)!,
          parent: id,
        });
      });
      // Reserve all descendants, including collapsed ones. Expansion never repacks siblings.
      y += 148 + roles.length * 112 + 48;
    });
  });
  return {
    boxes,
    phases,
    width: Math.max(600, phases.nodes.length * 292 + 16),
    height: Math.max(620, ...boxes.map((b) => b.y + b.height + 56)),
  };
}
function stored(project?: string): RecordData {
  try {
    const v = JSON.parse(
      sessionStorage.getItem(`studio.fixed-canvas.${project}`) || "{}",
    );
    return v && typeof v === "object" ? v : {};
  } catch {
    return {};
  }
}
export function readCanvasSelection(
  project?: string,
): CanvasSelection | undefined {
  const value = stored(project).selection;
  if (
    !value ||
    typeof value.id !== "string" ||
    !["step", "department", "role", "handoff", "stage-edge"].includes(
      value.kind,
    )
  )
    return undefined;
  return {
    sourceStage:
      typeof value.sourceStage === "string" ? value.sourceStage : undefined,
    targetStage:
      typeof value.targetStage === "string" ? value.targetStage : undefined,
    kind: value.kind,
    id: value.id,
    stage: typeof value.stage === "string" ? value.stage : undefined,
    department:
      typeof value.department === "string" ? value.department : undefined,
    handoffs: Array.isArray(value.handoffs)
      ? value.handoffs.filter((id: unknown) => typeof id === "string")
      : undefined,
  };
}
export function FixedWorkflowCanvas({
  graph,
  groups,
  steps,
  model,
  selection,
  onSelection,
}: {
  graph: RecordData;
  groups: RecordData[];
  steps: RecordData[];
  model: Model;
  selection?: CanvasSelection;
  onSelection: (value: CanvasSelection) => void;
}) {
  const key = `studio.fixed-canvas.${graph.project_id}`;
  const initial = useRef(stored(graph.project_id)).current;
  const [expanded, setExpanded] = useState<string[]>(() =>
    Array.isArray(initial.expanded)
      ? initial.expanded.filter((v: unknown) => typeof v === "string")
      : [],
  );
  const [zoom, setZoom] = useState(() =>
    typeof initial.zoom === "number" && Number.isFinite(initial.zoom)
      ? Math.min(1.25, Math.max(0.5, initial.zoom))
      : 1,
  );
  const viewport = useRef<HTMLDivElement>(null);
  const camera = useRef({
    left: Number(initial.left) || 0,
    top: Number(initial.top) || 0,
  });
  const layout = useMemo(
    () => fixedWorkflowLayout(graph, groups, steps, model),
    [graph, groups, steps, model],
  );
  const marker = useId().replace(/:/g, "");
  const visible = layout.boxes.filter(
    (b) =>
      b.kind === "stage" ||
      (expanded.includes(b.stage) &&
        (b.kind === "department" || expanded.includes(b.parent!))),
  );
  const persist = () => {
    if (!graph.project_id) return;
    try {
      sessionStorage.setItem(
        key,
        JSON.stringify({ expanded, zoom, selection, ...camera.current }),
      );
    } catch {
      /* Optional preference. */
    }
  };
  useEffect(persist, [key, expanded, zoom, selection]);
  useLayoutEffect(() => {
    const el = viewport.current;
    if (el) {
      el.scrollLeft = camera.current.left;
      el.scrollTop = camera.current.top;
    }
  }, []);
  const toggle = (id: string) =>
    setExpanded((old) =>
      old.includes(id) ? old.filter((v) => v !== id) : [...old, id],
    );
  const selectBox = (box: Box) =>
    onSelection(
      box.kind === "stage"
        ? {
            kind: "step",
            id: box.data.node_ids.includes(graph.active_node)
              ? graph.active_node
              : steps.find(
                  (s) =>
                    box.data.node_ids.includes(s.id) &&
                    ["blocked", "running", "human", "failed"].includes(
                      s.status,
                    ),
                )?.id || box.data.node_ids[0],
          }
        : {
            kind: box.kind as "department" | "role",
            id: box.data.id,
            stage: box.stage,
            department:
              box.kind === "department"
                ? box.data.id
                : box.parent!.split("/").slice(1).join("/"),
          },
    );
  const locate = () => {
    const box = layout.boxes.find(
      (b) => b.kind === "stage" && b.data.node_ids.includes(graph.active_node),
    );
    if (box && viewport.current) {
      viewport.current.scrollLeft = Math.max(0, box.x * zoom - 36);
      viewport.current.scrollTop = 0;
    }
  };
  const edges: {
    id: string;
    source: string;
    target: string;
    path: string;
    label: string;
    x: number;
    y: number;
    repair: boolean;
    running: boolean;
    completed: boolean;
    selection: CanvasSelection;
  }[] = [];
  const phaseById = (id: string) => layout.boxes.find((b) => b.id === id)!;
  layout.phases.edges.forEach((edge, index) => {
    const s = phaseById(edge.source),
      t = phaseById(edge.target);
    const relevant = model.handoffs
      .filter((h) =>
        edge.handoffs?.some(
          (e) =>
            steps.find((n) => n.id === e.source)?.members.includes(h.source) &&
            steps.find((n) => n.id === e.target)?.members.includes(h.target),
        ),
      )
      .map((h) => handoffForPhases(h, model, s.data.node_ids, t.data.node_ids));
    const forward = t.x > s.x && !edge.repair;
    const lane = 32 + index * 6;
    const sx = s.x + s.width / 2,
      tx = t.x + t.width / 2;
    edges.push({
      ...edge,
      path: forward
        ? `M ${s.x + s.width} ${s.y + 84} H ${t.x}`
        : `M ${sx} ${s.y} V ${lane} H ${tx} V ${t.y}`,
      label: edge.repair ? "退回修改" : "阶段交付",
      x: forward ? (s.x + s.width + t.x) / 2 : (sx + tx) / 2,
      y: forward ? s.y + 70 : lane - 8,
      running: relevant.some((h) => h.in_progress),
      completed: relevant.some((h) => h.received_count),
      selection: {
        kind: "stage-edge",
        id: edge.id,
        handoffs: relevant.map((h) => h.id),
        sourceStage: s.stage,
        targetStage: t.stage,
      },
    });
  });
  // Select a stage occurrence using recorded stage provenance when available. The same
  // employee can participate in multiple phases; each occurrence keeps its own slot.
  model.handoffs.forEach((h, index) => {
    const sources = layout.boxes.filter(
      (b) => b.kind === "role" && b.data.id === h.source,
    );
    const targets = layout.boxes.filter(
      (b) => b.kind === "role" && b.data.id === h.target,
    );
    const phaseContains = (box: Box, step: string) =>
      layout.phases.nodes
        .find((n) => n.id === box.stage)
        ?.node_ids.includes(step);
    let pair: [Box, Box] | undefined,
      best = -Infinity;
    for (const s of sources)
      for (const t of targets) {
        const scoped = handoffForPhases(
          h,
          model,
          phaseById(s.stage).data.node_ids,
          phaseById(t.stage).data.node_ids,
        );
        const recorded = scoped.received_count > 0;
        const contract = graph.edges?.some(
          (e: RecordData) =>
            phaseContains(s, e.source) && phaseContains(t, e.target),
        );
        const score =
          (scoped.in_progress ? 100000 : 0) +
          (recorded ? 10000 : 0) +
          (scoped.available.some((task: RecordData) => task.stage_id) ||
          scoped.executions.some((execution: RecordData) => execution.stage)
            ? 2000
            : 0) +
          (contract ? 1000 : 0) -
          Math.abs(t.x - s.x);
        if (score > best) {
          pair = [s, t];
          best = score;
        }
      }
    if (!pair) return;
    const endpoint = (b: Box) =>
      visible.find((v) => v.id === b.id) ||
      visible.find((v) => v.id === b.parent);
    const s = endpoint(pair[0]),
      t = endpoint(pair[1]);
    if (!s || !t || s.id === t.id) return;
    const scoped = handoffForPhases(
      h,
      model,
      phaseById(s.stage).data.node_ids,
      phaseById(t.stage).data.node_ids,
    );
    const routeId = `${s.id}>${t.id}:${h.loop ? "repair" : "forward"}`;
    const existing = edges.find((e) => e.id === routeId);
    if (existing) {
      existing.selection.handoffs!.push(h.id);
      existing.running ||= !!scoped.in_progress;
      existing.completed ||= !!scoped.received_count;
      return;
    }
    const sx = s.x + s.width,
      sy = s.y + s.height / 2,
      ty = t.y + t.height / 2;
    const gutter = s.x + s.width + 16 + (index % 4) * 5;
    const same = s.stage === t.stage;
    const targetGutter = phaseById(t.stage).x - 12 - (index % 3) * 4;
    const lane = 276 + (index % 9) * 6;
    const path = same
      ? `M ${sx} ${sy} H ${gutter} V ${ty} H ${t.x + t.width}`
      : `M ${sx} ${sy} H ${gutter} V ${lane} H ${targetGutter} V ${ty} H ${t.x}`;
    edges.push({
      id: routeId,
      source: s.data.id,
      target: t.data.id,
      path,
      label: `${s.kind === "department" ? s.data.label : roleName(h.source)} → ${t.kind === "department" ? t.data.label : roleName(h.target)}`,
      x: same ? gutter + 4 : (gutter + targetGutter) / 2,
      y: same ? (sy + ty) / 2 : lane - 5,
      repair: !!h.loop,
      running: !!scoped.in_progress,
      completed: !!scoped.received_count,
      selection: {
        kind: "handoff",
        id: routeId,
        handoffs: [h.id],
        sourceStage: s.stage,
        targetStage: t.stage,
      },
    });
  });
  const dragging = useRef<{
    x: number;
    y: number;
    left: number;
    top: number;
    id: number;
  } | null>(null);
  return (
    <section className="workflow-fixed-canvas" aria-label="项目协作画布">
      <div className="workflow-canvas-tools">
        <span>阶段 → 部门 → 岗位</span>
        <div>
          <button
            className="text-link"
            onClick={() => {
              locate();
              onSelection({
                kind: "step",
                id: graph.active_node || steps[0]?.id,
              });
            }}
          >
            <LocateFixed size={15} />
            当前步骤
          </button>
          <button
            aria-label="缩小画布"
            onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.1).toFixed(2)))}
          >
            <Minus size={15} />
          </button>
          <output aria-label="画布缩放">{Math.round(zoom * 100)}%</output>
          <button
            aria-label="放大画布"
            onClick={() =>
              setZoom((z) => Math.min(1.25, +(z + 0.1).toFixed(2)))
            }
          >
            <Plus size={15} />
          </button>
          <button
            aria-label="适应画布宽度"
            onClick={() =>
              setZoom(
                Math.max(
                  0.5,
                  Math.min(
                    1,
                    (viewport.current?.clientWidth || 800) / layout.width,
                  ),
                ),
              )
            }
          >
            <Scan size={15} />
          </button>
        </div>
      </div>
      <div
        ref={viewport}
        className="workflow-canvas-viewport"
        tabIndex={0}
        aria-label="工作流画布，可横向和纵向滚动"
        onScroll={() => {
          const el = viewport.current!;
          camera.current = { left: el.scrollLeft, top: el.scrollTop };
          persist();
        }}
        onPointerDown={(e) => {
          if (
            e.pointerType !== "mouse" ||
            e.button !== 0 ||
            (e.target as Element).closest("button,[data-flow-edge]")
          )
            return;
          const el = viewport.current!;
          dragging.current = {
            x: e.clientX,
            y: e.clientY,
            left: el.scrollLeft,
            top: el.scrollTop,
            id: e.pointerId,
          };
          el.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = dragging.current;
          if (d) {
            viewport.current!.scrollLeft = d.left + d.x - e.clientX;
            viewport.current!.scrollTop = d.top + d.y - e.clientY;
          }
        }}
        onPointerUp={() => {
          dragging.current = null;
        }}
        onPointerCancel={() => {
          dragging.current = null;
        }}
      >
        <div
          className="workflow-canvas-surface"
          style={{ width: layout.width * zoom, height: layout.height * zoom }}
        >
          <div
            className="workflow-canvas-board workflow-map"
            style={{
              width: layout.width,
              height: layout.height,
              transform: `scale(${zoom})`,
            }}
          >
            <svg
              width={layout.width}
              height={layout.height}
              className="workflow-fixed-links"
              aria-label="工作数据流"
            >
              <defs>
                {["normal", "repair", "running", "done"].map((v) => (
                  <marker
                    key={v}
                    id={`${marker}-${v}`}
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="7"
                    markerHeight="7"
                    orient="auto-start-reverse"
                  >
                    <path
                      d="M 0 0 L 10 5 L 0 10 z"
                      className={`workflow-arrow-${v}`}
                    />
                  </marker>
                ))}
              </defs>
              {visible
                .filter((b) => b.parent)
                .map((b) => {
                  const parent = phaseById(b.parent!);
                  const x = b.kind === "department" ? parent.x - 20 : b.x - 12;
                  return (
                    <path
                      key={`tree-${b.id}`}
                      className="workflow-hierarchy-line"
                      d={`M ${parent.x + 8} ${parent.y + parent.height} V ${parent.y + parent.height + 16} H ${x} V ${b.y + b.height / 2} H ${b.x}`}
                    />
                  );
                })}
              {edges.map((e) => (
                <g
                  key={e.id}
                  data-flow-edge={e.id}
                  data-flow-state={
                    e.running
                      ? "running"
                      : e.completed
                        ? "completed"
                        : "pending"
                  }
                  data-source={e.source}
                  data-target={e.target}
                  role="button"
                  tabIndex={0}
                  aria-label={`${e.label}，查看交付内容`}
                  aria-pressed={selection?.id === e.id}
                  className={`workflow-fixed-edge ${e.repair ? "is-repair" : ""} ${e.running ? "is-running" : ""} ${e.completed ? "is-complete" : ""}`}
                  onClick={() => onSelection(e.selection)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelection(e.selection);
                    }
                  }}
                >
                  <title>
                    {e.label} ·{" "}
                    {e.running
                      ? "正在接收"
                      : e.completed
                        ? "已有接收记录"
                        : "查看交付定义与记录"}
                  </title>
                  <path
                    d={e.path}
                    className="workflow-flow-line"
                    markerEnd={`url(#${marker}-${e.running ? "running" : e.repair ? "repair" : e.completed ? "done" : "normal"})`}
                  />
                  {e.running && (
                    <path d={e.path} className="workflow-flow-stream" />
                  )}
                  <path d={e.path} className="workflow-edge-hit" />
                </g>
              ))}
              {edges
                .filter((e) => e.selection.kind === "stage-edge")
                .map((e) => (
                  <g
                    key={`label-${e.id}`}
                    className="workflow-fixed-edge-label"
                    role="button"
                    tabIndex={0}
                    aria-label={`${e.label}，查看交付内容`}
                    onClick={() => onSelection(e.selection)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelection(e.selection);
                      }
                    }}
                  >
                    <rect
                      x={e.x - 29}
                      y={e.y - 14}
                      width="58"
                      height="22"
                      rx="5"
                    />
                    <text x={e.x} y={e.y + 1} textAnchor="middle">
                      {e.label}
                    </text>
                  </g>
                ))}
            </svg>
            {visible.map((box) => {
              const active =
                box.kind === "stage"
                  ? selection?.kind === "step" &&
                    box.data.node_ids.includes(selection.id)
                  : selection?.kind === box.kind &&
                    selection.id === box.data.id &&
                    selection.stage === box.stage;
              const children = layout.boxes.filter((b) => b.parent === box.id);
              const phaseSteps =
                layout.phases.nodes.find((n) => n.id === box.stage)?.node_ids ||
                [];
              const roleStatus = (m: RecordData) =>
                m.assignments.some(
                  (s: RecordData) =>
                    phaseSteps.includes(s.id) && s.id === graph.active_node,
                )
                  ? m.status
                  : m.savedTasks.some(
                        (t: RecordData) =>
                          phaseSteps.includes(t.stage_id) &&
                          ["succeeded", "completed"].includes(t.status),
                      )
                    ? "succeeded"
                    : "pending";
              const statuses =
                box.kind === "department"
                  ? model.members
                      .filter((m) => box.data.node_ids.includes(m.id))
                      .map(roleStatus)
                  : [];
              const status =
                box.kind === "role"
                  ? roleStatus(box.data)
                  : box.kind === "department"
                    ? statuses.includes("running")
                      ? "running"
                      : statuses.includes("blocked")
                        ? "blocked"
                        : statuses.every((s) => s === "succeeded")
                          ? "succeeded"
                          : "pending"
                    : box.data.status;

              return (
                <article
                  key={box.id}
                  className={`workflow-fixed-node kind-${box.kind} ${active ? "is-selected" : ""} ${status === "running" ? "is-running" : ""}`}
                  data-map-node={box.id}
                  style={{
                    left: box.x,
                    top: box.y,
                    width: box.width,
                    height: box.height,
                  }}
                >
                  <button
                    className="workflow-fixed-node-select"
                    data-stage={
                      box.kind === "stage" && box.id !== "revision"
                        ? box.id
                        : undefined
                    }
                    data-branch={box.id === "revision" ? box.id : undefined}
                    data-department={
                      box.kind === "department" ? box.data.id : undefined
                    }
                    data-role={box.kind === "role" ? box.data.id : undefined}
                    aria-pressed={active}
                    onClick={() => selectBox(box)}
                  >
                    <span className="workflow-fixed-node-meta">
                      <small>
                        {box.id === "revision"
                          ? "返修分支"
                          : box.kind === "stage"
                            ? `${String(layout.phases.nodes.findIndex((n) => n.id === box.id) + 1).padStart(2, "0")} · 阶段`
                            : box.kind === "department"
                              ? "部门"
                              : "岗位"}
                      </small>
                      <Badge
                        status={status}
                        label={
                          box.kind === "role" && status === box.data.status
                            ? box.data.status_label
                            : undefined
                        }
                      />
                    </span>
                    <strong title={box.data.label}>
                      {box.kind === "stage"
                        ? stageTitles[box.data.label] || box.data.label
                        : box.data.label}
                    </strong>
                    <small>
                      {box.kind === "stage"
                        ? box.data.ownerLabel
                        : box.kind === "department"
                          ? `${box.data.node_ids.length} 个岗位参与`
                          : box.data.ownerLabel}
                    </small>
                  </button>
                  {children.length > 0 && (
                    <button
                      className="workflow-fixed-disclosure"
                      aria-expanded={expanded.includes(box.id)}
                      aria-label={`${expanded.includes(box.id) ? "收起" : "展开"}${box.data.label}的${box.kind === "stage" ? "部门" : "岗位"}`}
                      onClick={() => toggle(box.id)}
                    >
                      {expanded.includes(box.id) ? (
                        <ChevronDown size={14} />
                      ) : (
                        <ChevronRight size={14} />
                      )}
                      <span>
                        {box.kind === "stage"
                          ? `${children.length} 个部门`
                          : `${children.length} 个岗位`}
                      </span>
                    </button>
                  )}
                </article>
              );
            })}
          </div>
        </div>
      </div>
      <p className="workflow-canvas-hint">
        拖动画布或滚动查看 · 展开按钮显示分工 · 点击卡片或连线阅读详情{" "}
        <span>实线：交付　虚线：退回　流动：正在接收</span>
      </p>
    </section>
  );
}
