import { SelectionGroup } from "./SelectionGroup";
import { StudioSelect } from "./StudioSelect";
import { useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Users,
  Clapperboard,
  AudioLines,
  PenLine,
  ShieldCheck,
  ClipboardList,
  Film,
  LockKeyhole,
} from "lucide-react";
import {
  date,
  request,
  roleName,
  rows,
  ownedMedia,
  text,
  type RecordData,
} from "./api";
import {
  Artifact,
  Badge,
  Empty,
  ErrorNotice,
  FloatingErrorNotice,
  Field,
  Loading,
  Panel,
} from "./ui";
import { MaterialCard } from "./DocumentReader";
import {
  EnableTeamControl,
  LeaderEntry,
  InterventionComposer,
  TaskControlActions,
  TeamInstructions,
  useInterventionTarget,
} from "./TeamControls";

const statusLabels: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  waiting: "等待交接",
  completed: "已完成",
  failed: "执行失败",
  blocked: "需要处理",
  paused: "已暂停",
  superseded: "已被替代",
  reused: "复用成果",
  unknown: "历史状态未知",
};
const reviewLabels: Record<string, string> = {
  pending: "待审核",
  passed: "审核通过",
  failed: "审核未通过",
  not_required: "无需审核",
};
const adoptionLabels: Record<string, string> = {
  unselected: "未采用",
  selected: "已采用",
  not_applicable: "无需选择",
};
const icons = [
  ClipboardList,
  PenLine,
  Clapperboard,
  Film,
  AudioLines,
  Film,
  ShieldCheck,
];
function taskStatus(task: RecordData) {
  return (
    <Badge
      status={task.status}
      label={statusLabels[task.status] || task.status || "未记录"}
    />
  );
}
function useTeam(projectId: string) {
  return useQuery({
    queryKey: ["team", projectId],
    queryFn: async () => {
      const data = await request(`/projects/${projectId}/team`);
      return Array.isArray(data.departments) && Array.isArray(data.members)
        ? data
        : null;
    },
    retry: false,
    refetchInterval: 5000,
  });
}
export function TeamOverview({
  projectId,
  onOpen,
}: {
  projectId: string;
  onOpen: () => void;
}) {
  const { data } = useTeam(projectId);
  if (!data) return null;
  const summary = data.task_summary || {};
  return (
    <Panel
      title="制作团队"
      className="team-overview"
      action={
        <button className="text-link" onClick={onOpen}>
          打开团队指挥台 <ArrowRight size={14} />
        </button>
      }
    >
      <div className="team-summary" aria-label="团队任务概况">
        {[
          ["running", "执行中"],
          ["waiting", "等待交接"],
          ["blocked", "需要处理"],
          ["completed", "已完成"],
        ].map(([key, label]) => (
          <span key={key}>
            <strong>{summary[key] ?? 0}</strong>
            {label}
          </span>
        ))}
      </div>
      {rows(data.events)
        .slice(0, 2)
        .map((event) => (
          <p className="team-overview-event" key={event.id}>
            {event.message}
          </p>
        ))}
      {!summary.total && (
        <p className="muted">尚无任务账本记录；执行后显示真实分工与交接。</p>
      )}
    </Panel>
  );
}

export function TeamConsole({
  projectId,
  children,
}: {
  projectId: string;
  children?: ReactNode;
}) {
  const teamQuery = useTeam(projectId);
  const team = teamQuery.data;
  const [interventionTarget, setInterventionTarget] =
    useInterventionTarget(projectId);
  const [controlNotice, setControlNotice] = useState("");
  const [department, setDepartment] = useState("");
  const [status, setStatus] = useState("");
  const [shot, setShot] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const detailRef = useRef<HTMLElement>(null);
  function selectTask(taskId: string) {
    setSelectedId(taskId);
    detailRef.current?.scrollIntoView?.({
      block: "nearest",
      behavior: "smooth",
    });
  }
  const limit = 30;
  const tasksQuery = useQuery({
    queryKey: ["team-tasks", projectId, department, status, shot, offset],
    queryFn: () => {
      const params = new URLSearchParams({
        offset: String(offset),
        limit: String(limit),
      });
      if (department) params.set("department", department);
      if (status) params.set("status", status);
      if (shot.trim()) params.set("shot_id", shot.trim());
      return request(`/projects/${projectId}/tasks?${params}`);
    },
    enabled: !!team,
    retry: false,
    refetchInterval: 5000,
  });
  const tasks = rows(tasksQuery.data?.tasks);
  const selected = selectedId || tasks[0]?.id || "";
  const detailQuery = useQuery({
    queryKey: ["team-task", projectId, selected],
    queryFn: () =>
      request(`/projects/${projectId}/tasks/${encodeURIComponent(selected)}`),
    enabled: !!team && !!selected,
    retry: false,
    refetchInterval: 5000,
  });
  const detail =
    detailQuery.data?.task ||
    (detailQuery.data?.id ? detailQuery.data : null) ||
    tasks.find((task) => task.id === selected);
  const members = rows(team?.members);
  const memberName = (id: string) =>
    members.find((member) => member.id === id)?.label || roleName(id);
  if (!team)
    return (
      <>
        {children}
        {!children &&
          (teamQuery.isPending ? (
            <Loading />
          ) : (
            <Empty title="团队账本暂不可用">
              {teamQuery.error
                ? "请稍后重试，已有工作流与执行回执仍可查看。"
                : "当前项目尚无团队任务记录。"}
            </Empty>
          ))}
      </>
    );
  function filter(set: (value: string) => void, value: string) {
    set(value);
    setOffset(0);
  }
  return (
    <div className="stack team-console">
      <FloatingErrorNotice error={teamQuery.error} />
      {controlNotice && (
        <div className="notice team-control-notice" role="status">
          {controlNotice}
        </div>
      )}
      <EnableTeamControl projectId={projectId} team={team} />
      {!team.enabled && (
        <div className="notice">
          当前为团队观察模式，保留项目既有流程。
          {team.enable_available
            ? "团队控制可在安全边界启用。"
            : "历史记录仅展示已保存的执行证据。"}
        </div>
      )}
      <Panel
        title="团队指挥台"
        description="查看部门分工、实际任务和成果交接。"
      >
        {rows(team.actions).some((action) => action.id === "replan") && (
          <div className="team-leadership-toolbar">
            <div>
              <strong>总制片统筹</strong>
              <p className="muted">明确选择任务，由总制片协调跨部门安排。</p>
            </div>
            <LeaderEntry
              label="向总制片下达要求"
              enabled={
                team.enabled &&
                rows(team.actions).find((action) => action.id === "replan")
                  ?.enabled !== false &&
                members.find((member) => member.id === "executive_producer")
                  ?.leader_available !== false
              }
              reason={
                members.find((member) => member.id === "executive_producer")
                  ?.leader_unavailable_reason ||
                rows(team.actions).find((action) => action.id === "replan")
                  ?.disabled_reason
              }
              onOpen={() =>
                setInterventionTarget({
                  type: "leader",
                  id: "executive_producer",
                  label: "总制片",
                })
              }
            />
          </div>
        )}
        <SelectionGroup className="team-departments" value={department}>
          {rows(team.departments).map((item, index) => {
            const Icon = icons[index % icons.length] || Users;
            const departmentMembers = members.filter(
              (member) => member.department === item.id,
            );
            return (
              <article
                className={
                  "team-department" +
                  (department === item.id ? " selected" : "")
                }
                key={item.id}
              >
                <div className="row-between">
                  <Icon size={20} aria-hidden="true" />
                  <button
                    className="text-link"
                    aria-pressed={department === item.id}
                    onClick={() =>
                      filter(
                        setDepartment,
                        department === item.id ? "" : item.id,
                      )
                    }
                  >
                    查看任务 <ArrowRight size={12} />
                  </button>
                </div>
                <h3>{item.label}</h3>
                <p>{item.responsibility}</p>
                <small>负责人：{memberName(item.lead)}</small>
                {rows(team.actions).some(
                  (action) => action.id === "replan",
                ) && (
                  <LeaderEntry
                    label={"请" + item.label + "负责人安排"}
                    enabled={
                      team.enabled &&
                      item.management_available === true &&
                      members.find((member) => member.id === item.lead)
                        ?.leader_available !== false
                    }
                    reason={
                      !team.enabled
                        ? "请先启用团队控制"
                        : item.management_unavailable_reason ||
                          item.management_reason ||
                          members.find((member) => member.id === item.lead)
                            ?.leader_unavailable_reason
                    }
                    onOpen={() =>
                      setInterventionTarget({
                        type: "leader",
                        id: item.lead,
                        label: memberName(item.lead),
                        department: item.id,
                      })
                    }
                  />
                )}
                <div className="team-members">
                  {departmentMembers.map((member) => (
                    <div className="team-member" key={member.id}>
                      <div>
                        <strong>{member.label || roleName(member.role)}</strong>
                        {member.leader && <small>负责人</small>}
                      </div>
                      <Badge
                        status={member.status}
                        label={
                          statusLabels[member.status] ||
                          (member.status === "idle"
                            ? "暂无执行任务"
                            : member.status) ||
                          "未记录"
                        }
                      />
                      {rows(team.actions).some(
                        (action) => action.id === "intervene",
                      ) && (
                        <button
                          className="text-link team-member-task"
                          disabled={
                            rows(team.actions).find(
                              (action) => action.id === "intervene",
                            )?.enabled === false
                          }
                          title={
                            rows(team.actions).find(
                              (action) => action.id === "intervene",
                            )?.disabled_reason
                          }
                          onClick={() =>
                            setInterventionTarget({
                              type: "member",
                              id: member.id,
                              label: member.label || roleName(member.role),
                            })
                          }
                        >
                          介入成员任务
                        </button>
                      )}
                      {Array.isArray(member.current_tasks) &&
                        member.current_tasks.map((taskId: string) => (
                          <button
                            key={taskId}
                            className="text-link team-member-task"
                            onClick={() => selectTask(taskId)}
                          >
                            {tasks.find((task) => task.id === taskId)?.label ||
                              taskId}
                            <ArrowRight size={12} />
                          </button>
                        ))}
                    </div>
                  ))}
                </div>
              </article>
            );
          })}
        </SelectionGroup>
      </Panel>
      <div className="team-workspace">
        <Panel
          title="实际任务"
          description="执行、审核与成片采用分别记录。"
          className="team-task-list"
        >
          <div className="team-filters">
            <Field label="部门">
              <StudioSelect
                value={department}
                onChange={(event) => filter(setDepartment, event.target.value)}
              >
                <option value="">全部部门</option>
                {rows(team.departments).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </StudioSelect>
            </Field>
            <Field label="执行状态">
              <StudioSelect
                value={status}
                onChange={(event) => filter(setStatus, event.target.value)}
              >
                <option value="">全部状态</option>
                {Object.entries(statusLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </StudioSelect>
            </Field>
            <Field label="镜头编号">
              <input
                type="search"
                value={shot}
                placeholder="例如 S01"
                onChange={(event) => filter(setShot, event.target.value)}
              />
            </Field>
          </div>
          <FloatingErrorNotice error={tasksQuery.error} />
          {tasksQuery.error && (
            <button
              className="button small"
              onClick={() => tasksQuery.refetch()}
            >
              重新读取任务
            </button>
          )}
          {tasksQuery.isPending ? (
            <Loading />
          ) : tasks.length ? (
            <SelectionGroup as="ul" className="team-tasks" value={selected}>
              {tasks.map((task) => (
                <li key={task.id}>
                  <button
                    className={
                      "team-task-button" +
                      (selected === task.id ? " selected" : "")
                    }
                    aria-pressed={selected === task.id}
                    onClick={() => selectTask(task.id)}
                  >
                    <span>
                      <strong>{task.label}</strong>
                      {taskStatus(task)}
                    </span>
                    <small>
                      {memberName(task.owner)} ·{" "}
                      {task.shot_id || task.scope || "项目任务"}
                      {task.line_id ? ` · ${task.line_id}` : ""}
                    </small>
                    {task.blocked_reason && <em>{task.blocked_reason}</em>}
                  </button>
                </li>
              ))}
            </SelectionGroup>
          ) : (
            !tasksQuery.error && (
              <Empty title="没有匹配任务">
                调整筛选条件，或等待实际执行记录。
              </Empty>
            )
          )}
          <div className="team-pagination">
            <small>{tasksQuery.data?.total ?? 0} 项任务</small>
            <button
              className="button small"
              disabled={!offset || tasksQuery.isFetching}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              上一页
            </button>
            <button
              className="button small"
              disabled={
                offset + limit >= (tasksQuery.data?.total ?? 0) ||
                tasksQuery.isFetching
              }
              onClick={() => setOffset(offset + limit)}
            >
              下一页
            </button>
          </div>
        </Panel>
        <aside
          className="panel team-task-detail"
          aria-label="制作任务详情"
          ref={detailRef}
        >
          {detail ? (
            <TaskDetail
              key={detail.id}
              task={detail}
              projectId={projectId}
              memberName={memberName}
              onTask={selectTask}
              controls={
                <TaskControlActions
                  projectId={projectId}
                  task={detail}
                  controlRevision={Math.max(
                    team.control_revision ?? 0,
                    detailQuery.data?.control_revision ?? 0,
                    tasksQuery.data?.control_revision ?? 0,
                  )}
                  onIntervene={() =>
                    setInterventionTarget({
                      type: "task",
                      id: detail.id,
                      label: detail.label,
                      task: detail,
                    })
                  }
                />
              }
            />
          ) : selected && detailQuery.isPending ? (
            <Loading />
          ) : (
            <Empty title="选择任务查看详情">
              查看负责人、输入版本、交付成果和审核依据。
            </Empty>
          )}
          <FloatingErrorNotice error={detailQuery.error} />
          {detailQuery.error && (
            <button
              className="button small"
              onClick={() => detailQuery.refetch()}
            >
              重新读取任务详情
            </button>
          )}
        </aside>
      </div>
      <Panel
        title="制作动态"
        description="由真实任务记录生成，可打开关联任务。"
      >
        {rows(team.events).length ? (
          <ol className="team-events">
            {rows(team.events).map((event) => (
              <li key={event.id}>
                <time>{date(event.at)}</time>
                {event.task_id ? (
                  <button
                    className="text-link"
                    onClick={() => selectTask(event.task_id)}
                  >
                    {event.message}
                    <ArrowRight size={13} />
                  </button>
                ) : (
                  <span>{event.message}</span>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">尚无任务交接事件。</p>
        )}
      </Panel>
      <TeamInstructions
        projectId={projectId}
        instructions={rows(team.instructions)}
        controlRevision={team.control_revision ?? 0}
        team={team}
        onTask={selectTask}
      />
      {interventionTarget && (
        <InterventionComposer
          key={interventionTarget.type + ":" + interventionTarget.id}
          projectId={projectId}
          target={interventionTarget}
          team={team}
          onClose={() => setInterventionTarget(null)}
          onRegistered={setControlNotice}
        />
      )}
      {children && (
        <details className="team-legacy-receipts">
          <summary>工作流与历史执行回执</summary>
          {children}
        </details>
      )}
    </div>
  );
}
function TaskDetail({
  task,
  projectId,
  memberName,
  onTask,
  controls,
}: {
  task: RecordData;
  projectId: string;
  memberName: (id: string) => string;
  onTask: (id: string) => void;
  controls?: ReactNode;
}) {
  return (
    <>
      <header className="panel-head">
        <div>
          <small>{task.id}</small>
          <h2>{task.label}</h2>
        </div>
        {taskStatus(task)}
      </header>
      {controls}
      <dl className="team-task-meta">
        <div>
          <dt>负责人</dt>
          <dd>{memberName(task.owner)}</dd>
        </div>
        <div>
          <dt>制作对象</dt>
          <dd>
            {task.shot_id || task.scope || "项目"}
            {task.line_id ? ` · ${task.line_id}` : ""}
          </dd>
        </div>
        <div>
          <dt>输入版本</dt>
          <dd>{text(task.input_version) || "未记录"}</dd>
        </div>
        <div>
          <dt>执行尝试</dt>
          <dd>
            第 {task.generation ?? 1} 版 · 第 {task.attempt ?? 0} 次
          </dd>
        </div>
        {Array.isArray(task.allowed_owners) && (
          <div>
            <dt>可接手成员</dt>
            <dd>
              {task.allowed_owners
                .map((id: string) => memberName(id))
                .join("、") || "暂无其他合格成员"}
            </dd>
          </div>
        )}
        {task.default_owner && (
          <div>
            <dt>默认职责</dt>
            <dd>{memberName(task.default_owner)}</dd>
          </div>
        )}
        <div>
          <dt>实际模型</dt>
          <dd>{task.model || "未记录"}</dd>
        </div>
        <div>
          <dt>审核状态</dt>
          <dd>
            <Badge
              status={task.review_status}
              label={reviewLabels[task.review_status] || "未记录"}
            />
          </dd>
        </div>
        <div>
          <dt>成片采用</dt>
          <dd>{adoptionLabels[task.adoption_status] || "未记录"}</dd>
        </div>
        <div>
          <dt>成果保护</dt>
          <dd>
            {task.locked ? (
              <>
                <LockKeyhole size={13} /> 已锁定
              </>
            ) : (
              "未锁定"
            )}
            {task.hold_after ? " · 完成后等待确认" : ""}
          </dd>
        </div>
      </dl>
      {task.blocked_reason && (
        <div className="notice">{task.blocked_reason}</div>
      )}
      {task.requirements && (
        <section>
          <h3>交付要求</h3>
          <p>
            {typeof task.requirements === "string"
              ? task.requirements
              : JSON.stringify(task.requirements)}
          </p>
        </section>
      )}
      <section>
        <h3>输入材料</h3>
        {rows(task.inputs).length ? (
          rows(task.inputs).map((item, index) => (
            <MaterialCard
              key={item.id || index}
              item={item}
              projectId={projectId}
            />
          ))
        ) : (
          <p className="muted">未记录输入材料。</p>
        )}
      </section>
      <section>
        <h3>交付成果</h3>
        {rows(task.artifacts).length ? (
          rows(task.artifacts).map((item, index) => (
            <div className="team-task-artifact" key={item.id || index}>
              <Artifact item={item} projectId={projectId} />
              <TaskMedia item={item} projectId={projectId} />
              {item.kind && <small>{item.kind}</small>}
            </div>
          ))
        ) : (
          <p className="muted">尚无保存的交付成果。</p>
        )}
      </section>
      <section>
        <h3>前置任务</h3>
        {Array.isArray(task.dependencies) && task.dependencies.length ? (
          task.dependencies.map((id: string) => (
            <button
              className="text-link team-dependency"
              key={id}
              onClick={() => onTask(id)}
            >
              {id}
              <ArrowRight size={12} />
            </button>
          ))
        ) : (
          <p className="muted">无已记录的前置依赖。</p>
        )}
      </section>
      <section>
        <h3>执行记录与消耗</h3>
        <p className="muted">
          开始：{date(task.started_at)} · 完成：{date(task.finished_at)}
        </p>
        {rows(task.attempts).length ? (
          rows(task.attempts).map((attempt, index) => (
            <article className="team-attempt" key={attempt.id || index}>
              <div className="row-between">
                <strong>第 {attempt.attempt ?? index + 1} 次执行</strong>
                <Badge
                  status={attempt.status}
                  label={statusLabels[attempt.status] || attempt.status}
                />
              </div>
              <p>
                {attempt.model || task.model || "模型未记录"} ·{" "}
                {date(attempt.started_at || attempt.at)}
              </p>
              {attempt.error && <p className="notice">{attempt.error}</p>}
              {attempt.elapsed_seconds != null && (
                <small>耗时 {attempt.elapsed_seconds} 秒</small>
              )}
              {attempt.cost && (
                <p className="muted">
                  消耗记录：{JSON.stringify(attempt.cost)}
                </p>
              )}
              {rows(attempt.artifacts).map((item, itemIndex) => (
                <Artifact key={itemIndex} item={item} projectId={projectId} />
              ))}
            </article>
          ))
        ) : (
          <p className="muted">尚无保存的尝试记录；未推算历史调用或费用。</p>
        )}
      </section>
    </>
  );
}

function TaskMedia({
  item,
  projectId,
}: {
  item: RecordData;
  projectId: string;
}) {
  const url = ownedMedia(projectId, item.artifact_url || item.url || item.path);
  if (!url) return null;
  const path = url.toLowerCase();
  if (/\.(mp4|webm|mov)$/.test(path))
    return (
      <video
        key={url}
        src={url}
        controls
        preload="metadata"
        aria-label={item.label || "视频成果"}
      />
    );
  if (/\.(wav|mp3|ogg|m4a)$/.test(path))
    return (
      <audio
        key={url}
        src={url}
        controls
        preload="metadata"
        aria-label={item.label || "声音成果"}
      />
    );
  if (/\.(png|jpe?g|webp|gif)$/.test(path))
    return <img src={url} alt={item.label || "图像成果"} loading="lazy" />;
  return null;
}
