import { useEffect, useRef } from "react";
import { ArrowRight, ArrowLeft, Users, FolderOpen } from "lucide-react";
import { rows, roleName, date, type RecordData } from "./api";
import { Badge, Readable, Artifact } from "./ui";
import { ExecutionRecord, MaterialCard } from "./DocumentReader";
import { WorkflowMap } from "./WorkflowMap";

export interface CollaborationSelection {
  open: boolean;
  department: string;
  role: string;
}
const initialSelection: CollaborationSelection = {
  open: false,
  department: "",
  role: "",
};
export function readCollaboration(projectId?: string): CollaborationSelection {
  try {
    const saved = JSON.parse(
      sessionStorage.getItem(`studio.collaboration.${projectId}`) || "null",
    );
    if (
      projectId &&
      saved &&
      typeof saved.open === "boolean" &&
      typeof saved.department === "string" &&
      typeof saved.role === "string"
    )
      return saved;
  } catch {
    /* Storage is optional; in-memory navigation remains available. */
  }
  return { ...initialSelection };
}
export function saveCollaboration(
  projectId: string | undefined,
  selection: CollaborationSelection,
) {
  if (!projectId) return;
  try {
    sessionStorage.setItem(
      `studio.collaboration.${projectId}`,
      JSON.stringify(selection),
    );
  } catch {
    /* Optional preference. */
  }
}
const aggregate = (steps: RecordData[]) =>
  steps.find((s) =>
    ["running", "blocked", "failed", "human", "paused", "waiting"].includes(
      s.status,
    ),
  )?.status ||
  (steps.length &&
  steps.filter((s) => !s.conditional).length &&
  steps.filter((s) => !s.conditional).every((s) => s.status === "succeeded")
    ? "succeeded"
    : "pending");
const unique = (values: unknown[]) => [
  ...new Set(values.filter((v): v is string => typeof v === "string" && !!v)),
];

/** Organizational membership comes from server definitions; edges are declared handoffs,
 * and records remain attributed to their recorded role/stage instead of cloned to coworkers. */
export function collaborationModel(
  graph: RecordData,
  flow: RecordData,
  steps: RecordData[],
) {
  const stageTasks = rows(flow.task_board?.tasks);
  const recordedStages = stageTasks
    .filter(
      (t) =>
        t.record &&
        (t.record.run_id ||
          t.record.status ||
          t.record.started_at ||
          t.record.finished_at),
    )
    .map((t) => ({
      ...t.record,
      id: "stage-record:" + t.id + ":" + (t.record.run_id || "saved"),
      title: t.label || t.id,
      owner: t.record.owner || t.owner,
      stage_id: t.id,
      status: t.record.status || t.status,
      source_label: t.current === false ? "历史步骤工单" : "已保存的步骤工单",
      input: "此工单未保存独立输入正文，可查看岗位分工与步骤材料。",
      output:
        t.record.completion_semantics ===
        "graph_node_returned_not_quality_approval"
          ? "步骤执行已返回；质量审核与成片验收以对应审核结论为准。"
          : "此工单仅保存执行状态与时间，未保存独立交付正文。",
    }));
  const tasks = [
    ...rows(flow.collaboration_tasks),
    ...rows(graph.production_tasks || flow.production_tasks),
    ...recordedStages,
  ];
  const roles = unique([
    ...steps.flatMap((s) => [s.owner, s.reviewer, ...(s.members || [])]),
    ...tasks.flatMap((t) => [t.owner || t.role, t.reviewer]),
    ...rows(flow.nodes)
      .filter((n) => rows(n.executions).length > 0)
      .map((n) => n.role),
  ]);
  const definitions = rows(graph.departments).length
    ? rows(graph.departments)
    : rows(graph.groups);
  const claimed = new Set<string>();
  const departments: (RecordData & {
    id: string;
    label: string;
    node_ids: string[];
  })[] = definitions
    .map((d) => {
      const members = roles.filter(
        (r) => d.roles?.includes(r) && !claimed.has(r),
      );
      members.forEach((r) => claimed.add(r));
      return { ...d, id: d.id, label: d.label, node_ids: members };
    })
    .filter((d) => d.node_ids.length);
  const other = roles.filter((r) => !claimed.has(r));
  if (other.length)
    departments.push({
      id: "other-roles",
      label: "其他协作岗位",
      node_ids: other,
      responsibility: "当前工作流中尚未归入部门的岗位。",
    });
  const projectRunning =
    steps.find((s) => s.id === graph.active_node)?.status === "running";
  const stale =
    flow.revision != null &&
    graph.revision != null &&
    flow.revision < graph.revision;
  const members = roles.map((role) => {
    const assignments = steps.filter((s) => s.members?.includes(role));
    const savedTasks = tasks.filter((t) => (t.owner || t.role) === role);
    const executions = rows(flow.nodes)
      .filter((n) => n.role === role)
      .flatMap((n) => rows(n.executions));
    const roleNode = rows(flow.nodes).find((n) => n.role === role);
    const owned = assignments.filter((s) => s.owner === role),
      reviews = assignments.filter((s) => s.reviewer === role);
    const reportedStatus =
      roleNode?.status_source && roleNode.status_source !== "not_recorded"
        ? roleNode.status
        : savedTasks.some(
              (t) => t.status === "running" && t.stage_id === graph.active_node,
            )
          ? "running"
          : "pending";
    const status =
      stale || (reportedStatus === "running" && !projectRunning)
        ? "pending"
        : reportedStatus;
    const count = savedTasks.length + executions.length;
    return {
      id: role,
      role,
      label: roleName(role),
      owner: role,
      members: [role],
      status,
      status_label: stale
        ? "正在同步记录"
        : reportedStatus === "running" && !projectRunning
          ? "当前未执行"
          : status === "pending"
            ? count
              ? `${count} 份记录`
              : "暂无执行记录"
            : undefined,
      ownerLabel:
        [
          owned.length ? `负责 ${owned.length} 步` : "",
          reviews.length ? `审核 ${reviews.length} 步` : "",
        ]
          .filter(Boolean)
          .join(" · ") || "参与协作",
      assignments,
      savedTasks,
      executions,
      roleNode,
      summary:
        assignments.map((s) => s.label).join("、") || "查看已保存的任务与交付",
    };
  });
  const handoffs: RecordData[] = [];
  const seen = new Set<string>();
  const add = (
    source: string,
    target: string,
    label: string,
    loop = false,
    basis = "职责交接",
  ) => {
    if (!roles.includes(source) || !roles.includes(target) || source === target)
      return;
    const key = JSON.stringify([source, target, label, loop]);
    if (seen.has(key)) return;
    seen.add(key);
    handoffs.push({ source, target, label, loop, basis });
  };
  for (const e of rows(flow.edges))
    add(e.source, e.target, e.label || "职责交接", !!e.loop);
  for (const e of rows(graph.edges)) {
    const a = steps.find((s) => s.id === e.source),
      b = steps.find((s) => s.id === e.target);
    if (a && b)
      add(
        a.owner,
        b.owner,
        `${a.label} → ${b.label}`,
        !!e.loop,
        "制作步骤交接",
      );
  }
  for (const s of steps)
    if (s.reviewer)
      add(
        s.owner,
        s.reviewer,
        `${s.label} · 提交审核`,
        false,
        "负责与审核分工",
      );
  // Only a role with a current running state receives moving packets.
  const active = members.find((m) => m.status === "running")?.id;
  const owner = new Map(
    departments.flatMap((d) =>
      d.node_ids.map((r: string) => [r, d.id] as const),
    ),
  );
  const groups = departments.map((d) => {
    const owned = steps.filter((s) => owner.get(s.owner) === d.id);
    return {
      ...d,
      status: members.some(
        (m) => d.node_ids.includes(m.id) && m.status === "running",
      )
        ? "running"
        : aggregate(owned),
      responsibility: d.responsibility,
      ownerLabel: d.lead ? `牵头 · ${roleName(d.lead)}` : "岗位协作",
      summary: `${d.node_ids.length} 个岗位 · ${owned.length} 个负责步骤`,
    };
  });
  return {
    departments: groups,
    members,
    handoffs,
    graph: { flow_kind: "collaboration", active_node: active, edges: handoffs },
  };
}

function WorkRecord({
  task,
  projectId,
  stale,
  onProject,
}: {
  task: RecordData;
  projectId?: string;
  stale: boolean;
  onProject?: (id: string) => void;
}) {
  return (
    <details className="collaboration-work-record">
      <summary>
        <strong>{task.title || task.label || task.phase || task.id}</strong>
        <Badge
          status={task.status}
          label={
            task.status === "completed"
              ? "已完成"
              : task.status === "unit_ready"
                ? "单元已完成"
                : undefined
          }
        />
      </summary>
      <p className="muted">
        {task.source_label || "已保存的任务记录"}
        {task.model ? ` · ${task.model}` : ""}
        {task.attempt ? ` · 第 ${task.attempt} 次` : ""}
      </p>
      {(task.finished_at || task.generated_at || task.started_at) && (
        <small>
          {date(task.finished_at || task.generated_at || task.started_at)}
        </small>
      )}
      <dl>
        <dt>接收的工作内容</dt>
        <dd>
          <Readable value={task.input ?? task.inputs ?? "此记录未保存输入"} />
        </dd>
        <dt>交付的工作结果</dt>
        <dd>
          <Readable
            value={task.output ?? task.outputs ?? "此记录未保存交付正文"}
          />
        </dd>
      </dl>
      {rows(task.artifacts).map(
        (a, i) =>
          !stale && (
            <Artifact
              key={i}
              item={a}
              projectId={task.project_id || projectId}
            />
          ),
      )}
      {task.artifact && !stale && (
        <Artifact
          item={
            typeof task.artifact === "string"
              ? { url: task.artifact, label: "查看产物" }
              : { ...task.artifact, label: "查看产物" }
          }
          projectId={task.project_id || projectId}
        />
      )}
      {Array.isArray(task.issues) && task.issues.length > 0 && (
        <div role="note">
          {task.issues.map((v: string, i: number) => (
            <p key={i}>{v}</p>
          ))}
        </div>
      )}
      {task.project_id && onProject && (
        <button className="button" onClick={() => onProject(task.project_id)}>
          <FolderOpen size={15} />
          打开此任务的工作流
        </button>
      )}
    </details>
  );
}

export function WorkflowCollaboration({
  graph,
  flow,
  steps,
  selection,
  onSelection,
  onInspectStep,
  onProject,
  stale,
}: {
  graph: RecordData;
  flow: RecordData;
  steps: RecordData[];
  selection: CollaborationSelection;
  onSelection: (value: CollaborationSelection) => void;
  onInspectStep: (id: string) => void;
  onProject?: (id: string) => void;
  stale: boolean;
}) {
  const model = collaborationModel(graph, flow, steps);
  const department = model.departments.find(
    (d) => d.id === selection.department,
  );
  const member = model.members.find(
    (m) => m.id === selection.role && department?.node_ids.includes(m.id),
  );
  const detailRef = useRef<HTMLElement>(null);
  const rosterRef = useRef<HTMLDivElement>(null);
  const returnToDepartment = () => {
    const previousRole = member?.id;
    onSelection({ ...selection, role: "" });
    requestAnimationFrame(() => {
      Array.from(
        rosterRef.current?.querySelectorAll<HTMLButtonElement>(
          "[data-map-node]",
        ) || [],
      )
        .find((e) => e.dataset.mapNode === previousRole)
        ?.focus({ preventScroll: true });
      if (window.matchMedia("(max-width: 900px)").matches)
        rosterRef.current?.scrollIntoView({
          block: "start",
          behavior: "instant",
        });
    });
  };
  useEffect(() => {
    if (member) {
      detailRef.current?.focus({ preventScroll: true });
      if (window.matchMedia("(max-width: 900px)").matches)
        detailRef.current?.scrollIntoView({
          block: "start",
          behavior: "instant",
        });
    }
  }, [member?.id]);
  const roster = model.members.filter(
    (m) => !department || department.node_ids.includes(m.id),
  );
  const edges = model.handoffs.filter(
    (e) =>
      !department ||
      (department.node_ids.includes(e.source) &&
        department.node_ids.includes(e.target)),
  );
  const material = (side: "inputs" | "outputs") =>
    rows(member?.roleNode?.[side]).map((item, i) => (
      <MaterialCard
        key={item.key || item.label + ":" + i}
        item={{
          ...item,
          source_label: "当前项目岗位材料",
          ...(stale
            ? { artifact_url: undefined, url: undefined, path: undefined }
            : {}),
        }}
        projectId={graph.project_id}
      />
    ));
  return (
    <section className="collaboration-workspace" aria-label="部门协作工作流">
      <nav className="collaboration-breadcrumbs" aria-label="协作层级">
        <button
          className="text-link"
          onClick={() => onSelection({ ...selection, open: false })}
        >
          项目总流程
        </button>
        <ArrowRight size={14} />
        <button
          className="text-link"
          onClick={() => onSelection({ open: true, department: "", role: "" })}
        >
          全部部门
        </button>
        {department && (
          <>
            <ArrowRight size={14} />
            <button className="text-link" onClick={returnToDepartment}>
              {department.label}
            </button>
          </>
        )}
        {member && (
          <>
            <ArrowRight size={14} />
            <strong>{member.label}</strong>
          </>
        )}
      </nav>
      {!department ? (
        <>
          <div className="collaboration-intro">
            <h3>项目由各部门协作交付</h3>
            <p>
              {model.departments.length} 个部门 · {model.members.length}{" "}
              个岗位。点击部门，展开内部岗位与工作结果。
            </p>
          </div>
          <WorkflowMap
            graph={model.graph}
            groups={model.departments}
            steps={model.members}
            caption="部门之间的数据与工作交接"
            navigationLabel="项目部门"
            hint="连线来自工作流定义与职责交接；点击部门继续展开"
            onSelect={(id) =>
              onSelection({ open: true, department: id, role: "" })
            }
          />
        </>
      ) : (
        <>
          <div className="collaboration-intro">
            <h3>{department.label}</h3>
            <p>{department.responsibility}</p>
          </div>
          <div className={`collaboration-layout ${member ? "has-member" : ""}`}>
            <div className="collaboration-roster" ref={rosterRef}>
              <WorkflowMap
                graph={{ ...model.graph, edges }}
                groups={roster.map((m) => ({ ...m, node_ids: [m.id] }))}
                steps={roster}
                selectedGroup={member?.id}
                onSelect={(id) => onSelection({ ...selection, role: id })}
                maxColumns={2}
                caption="部门内部的岗位分工"
                navigationLabel="部门岗位"
                hint="点击岗位查看负责的工作、实际执行记录与交付；无连线表示未记录岗位间交接"
              />
              <section
                className="collaboration-handoffs"
                aria-label="部门的上下游交接"
              >
                <h4>与其他部门的交接</h4>
                {model.handoffs
                  .filter(
                    (e) =>
                      department.node_ids.includes(e.source) !==
                      department.node_ids.includes(e.target),
                  )
                  .map((e, i) => (
                    <p key={i}>
                      {roleName(e.source)}
                      <ArrowRight size={13} />
                      {roleName(e.target)}
                      <small>{e.label}</small>
                    </p>
                  ))}
              </section>
            </div>
            {member && (
              <aside
                className="collaboration-member"
                ref={detailRef}
                tabIndex={-1}
                aria-label="岗位工作详情"
                key={member.id}
              >
                <button className="text-link" onClick={returnToDepartment}>
                  <ArrowLeft size={14} />
                  返回部门分工
                </button>
                <header>
                  <h3>
                    <Users size={19} />
                    {member.label}
                  </h3>
                  <Badge status={member.status} label={member.status_label} />
                </header>
                <p className="muted">
                  {member.role === "producer" ? "人工出品人" : "工作流岗位"} ·{" "}
                  {member.ownerLabel}
                </p>
                {rows(flow.teams).find((t) => t.role === member.role)
                  ?.model && (
                  <p>
                    当前配置：
                    {
                      rows(flow.teams).find((t) => t.role === member.role)
                        ?.model
                    }
                    <small className="muted">（历史调用以各条回执为准）</small>
                  </p>
                )}
                <section aria-label="岗位工作分工">
                  <h4>负责与参与的工作</h4>
                  {member.assignments.map((step) => (
                    <article className="collaboration-assignment" key={step.id}>
                      <header>
                        <strong>{step.label}</strong>
                        <span>
                          {step.owner === member.role
                            ? "负责"
                            : step.reviewer === member.role
                              ? "审核"
                              : "协作"}
                        </span>
                      </header>
                      <p>
                        {step.description ||
                          "按此步骤的职责与交付要求参与协作。"}
                      </p>
                      <dl>
                        <dt>步骤接收</dt>
                        <dd>{step.input || "查看步骤材料"}</dd>
                        <dt>步骤交付</dt>
                        <dd>{step.output || "查看步骤材料"}</dd>
                      </dl>
                      <button
                        className="text-link"
                        onClick={() => onInspectStep(step.id)}
                      >
                        打开此步骤的工作流
                        <ArrowRight size={14} />
                      </button>
                      <small className="muted">
                        步骤交付由参与岗位共同完成，个人执行结果以下方记录为准。
                      </small>
                    </article>
                  ))}
                </section>
                <section aria-label="岗位实际工作记录">
                  <h4>实际工作与结果</h4>
                  {!member.savedTasks.length && !member.executions.length && (
                    <p className="muted">
                      此岗位暂无已保存的执行记录；分工定义不代表已经执行。
                    </p>
                  )}
                  {member.savedTasks.map((task, i) => (
                    <WorkRecord
                      key={task.id || i}
                      task={task}
                      projectId={graph.project_id}
                      stale={stale}
                      onProject={onProject}
                    />
                  ))}
                  {member.executions.map((e) => (
                    <ExecutionRecord
                      key={e.id}
                      execution={
                        stale
                          ? { ...e, artifacts: [], attempts: [], receipt: null }
                          : e
                      }
                      role={member.role}
                      projectId={graph.project_id}
                    />
                  ))}
                </section>
                {(rows(member.roleNode?.inputs).length > 0 ||
                  rows(member.roleNode?.outputs).length > 0) && (
                  <section aria-label="岗位项目材料">
                    <h4>当前项目的岗位材料</h4>
                    <p className="muted">
                      这是岗位在项目中的共享材料，单次调用输入与输出请展开对应回执。
                    </p>
                    {material("inputs")}
                    {material("outputs")}
                  </section>
                )}
              </aside>
            )}
          </div>
        </>
      )}
    </section>
  );
}
