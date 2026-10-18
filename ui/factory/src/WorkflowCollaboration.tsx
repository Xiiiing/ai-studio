import { ArrowRight, Users, FolderOpen } from "lucide-react";
import { rows, roleName, date, type RecordData } from "./api";
import { Badge, Readable, Artifact } from "./ui";
import { ExecutionRecord, MaterialCard } from "./DocumentReader";
import { ContextManifest } from "./ContextManifest";

export interface CollaborationSelection {
  open: boolean;
  department: string;
  role: string;
  handoff?: string;
  step?: string;
  line?: string;
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
      return {
        ...saved,
        handoff: typeof saved.handoff === "string" ? saved.handoff : undefined,
        step: typeof saved.step === "string" ? saved.step : undefined,
        line: typeof saved.line === "string" ? saved.line : undefined,
      };
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
    steps.find((s) => s.id === graph.active_node)?.status === "running" &&
    (!flow.current_job || flow.current_job.status === "running");
  const stale =
    flow.revision != null &&
    graph.revision != null &&
    flow.revision < graph.revision;
  const timestamp = (value: unknown) =>
    typeof value === "number"
      ? value * 1000
      : typeof value === "string"
        ? Date.parse(value) || 0
        : 0;
  const lastFinished = Math.max(
    0,
    ...tasks.map((t) => timestamp(t.finished_at || t.generated_at)),
  );
  const currentTasks = tasks.filter(
    (t) =>
      t.status === "running" &&
      t.stage_id === graph.active_node &&
      timestamp(t.started_at) > 0 &&
      (flow.current_job
        ? timestamp(flow.current_job.started) > 0 &&
          timestamp(t.started_at) >= timestamp(flow.current_job.started)
        : timestamp(t.started_at) >= lastFinished),
  );
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
        : currentTasks.some((t) => (t.owner || t.role) === role)
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
    const key = JSON.stringify([source, target, loop]);
    if (seen.has(key)) return;
    seen.add(key);
    handoffs.push({ id: key, source, target, label, loop, basis });
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
        e.loop ? e.label || "修改意见" : a.output || e.label || "工作交付",
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
  const received: RecordData[] = tasks.flatMap((receiver) =>
    rows(receiver.received).map((r) => ({ ...r, receiver })),
  );
  for (const r of received)
    add(
      r.source_role,
      r.receiver.owner || r.receiver.role,
      r.label || "记录中的交付",
      /feedback/.test(r.input_field),
      "实际接收记录",
    );
  for (const handoff of handoffs) {
    const sender = members.find((m) => m.id === handoff.source)!;
    const receiver = members.find((m) => m.id === handoff.target)!;
    handoff.deliveries = received
      .filter(
        (r) =>
          r.source_role === handoff.source &&
          (r.receiver.owner || r.receiver.role) === handoff.target &&
          !!/feedback/.test(r.input_field) === !!handoff.loop,
      )
      .map((r) => ({
        ...r,
        in_progress:
          !stale &&
          projectRunning &&
          receiver.status === "running" &&
          currentTasks.includes(r.receiver),
      }));
    handoff.received_count = handoff.deliveries.length;
    handoff.executions = sender.executions;
    handoff.in_progress = handoff.deliveries.some(
      (r: RecordData) => r.in_progress,
    );
    // Candidate results are readable, but never labeled as received without a linked input.
    handoff.available = sender.savedTasks.filter(
      (t) =>
        (t.output != null || t.outputs != null) &&
        (t.execution_status === "succeeded" ||
          [
            "completed",
            "succeeded",
            "returned",
            "blocked",
            "unit_ready",
          ].includes(t.status)) &&
        t.output !== "尚无已验证输出" &&
        !String(t.id).startsWith("stage-record:") &&
        (!handoff.loop ||
          rows(t.issues).length ||
          t.status === "returned" ||
          t.status === "blocked" ||
          t.output?.passed === false),
    );
    handoff.shared = rows(sender.roleNode?.outputs).filter(
      (o) =>
        o.artifact_url &&
        rows(receiver.roleNode?.inputs).some(
          (i) => i.artifact_url === o.artifact_url,
        ),
    );
  }
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
        : aggregate(owned) === "running" && !projectRunning
          ? "pending"
          : aggregate(owned),
      responsibility: d.responsibility,
      ownerLabel: d.lead ? `牵头 · ${roleName(d.lead)}` : "岗位协作",
      summary: `${d.node_ids.length} 个岗位 · ${owned.length} 个负责步骤`,
      status_label: members.some(
        (m) => d.node_ids.includes(m.id) && m.status === "running",
      )
        ? `${members
            .filter((m) => d.node_ids.includes(m.id) && m.status === "running")
            .map((m) => m.label)
            .join("、")}工作中`
        : undefined,
    };
  });
  return {
    departments: groups,
    members,
    handoffs,
    graph: { flow_kind: "collaboration", active_node: active, edges: handoffs },
  };
}

/** Keep a phase occurrence tied to the saved source/receiver task, even when
 * the same two roles collaborate again in a later phase. */
export function handoffForPhases(
  handoff: RecordData,
  model: ReturnType<typeof collaborationModel>,
  sourceSteps: string[],
  targetSteps: string[],
): RecordData {
  const sender = model.members.find((m) => m.id === handoff.source);
  const deliveries = handoff.deliveries.filter((d: RecordData) => {
    const origin = sender?.savedTasks.find(
      (task) => task.id === d.source_task_id,
    );
    return (
      sourceSteps.includes(origin?.stage_id) &&
      targetSteps.includes(d.receiver.stage_id)
    );
  });
  return {
    ...handoff,
    deliveries,
    received_count: deliveries.length,
    in_progress: deliveries.some((d: RecordData) => d.in_progress),
    available: handoff.available.filter(
      (task: RecordData) =>
        !task.stage_id || sourceSteps.includes(task.stage_id),
    ),
    executions: handoff.executions.filter(
      (e: RecordData) => !e.stage || sourceSteps.includes(e.stage),
    ),
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
      {(task.call_id || task.reused) && (
        <ContextManifest
          value={task.context_manifest}
          reused={task.reused}
          originCall={task.origin_call_id}
        />
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
            <MaterialCard
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

export function CollaborationDetails({
  graph,
  flow,
  model,
  departmentId,
  role,
  stageIds,
  handoffs,
  stale,
  onStep,
  onProject,
}: {
  graph: RecordData;
  flow: RecordData;
  model: ReturnType<typeof collaborationModel>;
  departmentId?: string;
  role?: string;
  stageIds?: string[];
  handoffs?: RecordData[];
  stale: boolean;
  onStep: (id: string) => void;
  onProject?: (id: string) => void;
}) {
  const department = model.departments.find((d) => d.id === departmentId);
  const original = model.members.find((m) => m.id === role);
  const member = original && {
    ...original,
    assignments: original.assignments.filter(
      (s) => !stageIds || stageIds.includes(s.id),
    ),
    savedTasks: original.savedTasks.filter(
      (t) => !stageIds || !t.stage_id || stageIds.includes(t.stage_id),
    ),
    executions: original.executions.filter(
      (e) => !stageIds || !e.stage || stageIds.includes(e.stage),
    ),
  };
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
  if (handoffs)
    return (
      <HandoffDetails
        handoffs={handoffs}
        members={model.members}
        projectId={graph.project_id}
        stale={stale}
        onProject={onProject}
      />
    );
  return member ? (
    <div className="collaboration-member">
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
      {rows(flow.teams).find((t) => t.role === member.role)?.model && (
        <p>
          当前配置：
          {rows(flow.teams).find((t) => t.role === member.role)?.model}
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
            <p>{step.description || "按此步骤的职责与交付要求参与协作。"}</p>
            <dl>
              <dt>步骤接收</dt>
              <dd>{step.input || "查看步骤材料"}</dd>
              <dt>步骤交付</dt>
              <dd>{step.output || "查看步骤材料"}</dd>
            </dl>
            <button className="text-link" onClick={() => onStep(step.id)}>
              查看步骤材料
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
              stale ? { ...e, artifacts: [], attempts: [], receipt: null } : e
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
    </div>
  ) : (
    <div className="collaboration-empty-inspector">
      <Users size={24} />
      <h3>{department?.label || "项目协作"}</h3>
      <p>
        {department?.responsibility ||
          "选择阶段、部门或岗位查看工作，选择连线查看交付。"}
      </p>
      <p>部门和岗位通过卡片下方的展开按钮显示，点击卡片查看工作摘要。</p>
      {department && (
        <section aria-label="部门岗位清单">
          <h4>本阶段参与岗位</h4>
          {model.members
            .filter(
              (m) =>
                department.node_ids.includes(m.id) &&
                m.assignments.some((s) => !stageIds || stageIds.includes(s.id)),
            )
            .map((m) => (
              <p key={m.id}>
                {m.label} · {m.ownerLabel}
                <Badge status={m.status} label={m.status_label} />
              </p>
            ))}
        </section>
      )}
    </div>
  );
}

function safeMaterial(item: RecordData, stale: boolean) {
  return stale
    ? { ...item, artifact_url: undefined, url: undefined, path: undefined }
    : item;
}

export function HandoffDetails({
  handoffs,
  members,
  projectId,
  stale,
  onProject,
}: {
  handoffs: RecordData[];
  members: RecordData[];
  projectId?: string;
  stale: boolean;
  onProject?: (id: string) => void;
}) {
  return (
    <>
      {handoffs.map((h) => {
        const sender = members.find((m) => m.id === h.source),
          receiver = members.find((m) => m.id === h.target);
        const otherResults = h.available.filter(
          (task: RecordData) =>
            !h.deliveries.some((d: RecordData) => d.source_task_id === task.id),
        );
        return (
          <section className="collaboration-delivery" key={h.id}>
            <header>
              <h3>
                {roleName(h.source)}
                <ArrowRight size={17} />
                {roleName(h.target)}
              </h3>
              <span className="muted">{h.loop ? "退回修改" : "工作交接"}</span>
            </header>
            <p>{h.label}</p>
            <div className="collaboration-delivery-parties">
              <span>
                发出方 <strong>{sender?.label}</strong>
                <Badge status={sender?.status} label={sender?.status_label} />
              </span>
              <span>
                接收方 <strong>{receiver?.label}</strong>
                <Badge
                  status={receiver?.status}
                  label={receiver?.status_label}
                />
              </span>
            </div>
            {h.deliveries.length > 0 ? (
              <section aria-label="已核对接收的交付">
                <h4>接收输入已包含的内容</h4>
                {h.deliveries.map((d: RecordData, i: number) => (
                  <details
                    className="collaboration-delivered-record"
                    open={i === 0}
                    key={d.source_task_id + ":" + d.receiver.id + ":" + i}
                  >
                    <summary>
                      <strong>{d.label}</strong>
                      <span>已核对接收</span>
                    </summary>
                    <p className="muted">
                      发出记录：
                      {sender?.savedTasks.find(
                        (t: RecordData) => t.id === d.source_task_id,
                      )?.title || d.source_task_id}
                    </p>
                    <p className="muted">
                      接收任务：{d.receiver.title || d.receiver.id}
                      {d.receiver.started_at
                        ? ` · ${date(d.receiver.started_at)}`
                        : " · 时间未记录"}
                    </p>
                    <Readable value={d.value} />
                    {rows(
                      sender?.savedTasks.find(
                        (t: RecordData) => t.id === d.source_task_id,
                      )?.artifacts,
                    ).map((item, index) => (
                      <MaterialCard
                        key={index}
                        item={safeMaterial(item, stale)}
                        projectId={projectId}
                      />
                    ))}
                    <details>
                      <summary>接收方的处理记录</summary>
                      <WorkRecord
                        task={d.receiver}
                        projectId={projectId}
                        stale={stale}
                        onProject={onProject}
                      />
                    </details>
                  </details>
                ))}
                <details className="collaboration-evidence-note">
                  <summary>核对依据与原文说明</summary>
                  <p className="muted">
                    根据保存的输入与前序输出核对内容；接收时间以调用开始时间为准，未记录独立传输时间。展示内容为业务摘要，完整结果有原文件时可直接阅读。
                  </p>
                </details>
              </section>
            ) : (
              <p className="notice">
                尚未关联到接收方的实际输入，不能确认这条交接已经发生。
              </p>
            )}
            {!!otherResults.length && (
              <section aria-label="发出方保存的结果">
                <h4>发出方已保存的结果</h4>
                <p className="muted">
                  可查看具体内容；未关联接收记录的结果不代表已交给此岗位。
                </p>
                {otherResults.map((t: RecordData) => (
                  <WorkRecord
                    key={t.id}
                    task={t}
                    projectId={projectId}
                    stale={stale}
                    onProject={onProject}
                  />
                ))}
              </section>
            )}
            {!!h.shared.length && (
              <section>
                <h4>双方当前共用的项目材料</h4>
                <p className="muted">
                  同一项目文件的材料引用，不代表某次调用已经接收。
                </p>
                {h.shared.map((item: RecordData, i: number) => (
                  <MaterialCard
                    key={i}
                    item={safeMaterial(item, stale)}
                    projectId={projectId}
                  />
                ))}
              </section>
            )}
            {!!h.executions.length && (
              <section aria-label="发出方的调用记录">
                <h4>发出方的调用与结果原文</h4>
                <p className="muted">
                  以下为该岗位的已保存调用，尚未逐次关联接收方。
                </p>
                {h.executions.map((e: RecordData) => (
                  <ExecutionRecord
                    key={e.id}
                    execution={
                      stale
                        ? { ...e, artifacts: [], attempts: [], receipt: null }
                        : e
                    }
                    role={h.source}
                    projectId={projectId}
                  />
                ))}
              </section>
            )}
            {!h.available.length &&
              !h.deliveries.length &&
              !h.shared.length &&
              !h.executions.length && (
                <p className="muted">
                  这条连线目前仅有分工定义，尚无可读取的交付记录。
                </p>
              )}
          </section>
        );
      })}
    </>
  );
}
