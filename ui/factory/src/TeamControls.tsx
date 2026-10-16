import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { request, rows, type RecordData } from "./api";
import { Badge, ErrorNotice, Field, Loading, Modal } from "./ui";

export type InterventionTarget = {
  type: "task" | "member" | "department" | "leader";
  id: string;
  label: string;
  task?: RecordData;
};
type Draft = {
  instruction: string;
  operation: "revise" | "retry" | "replan";
  taskIds: string[];
  generations: Record<string, number>;
  preserve: string[];
  after: "auto" | "wait";
  requestId: string;
};
function requestId() {
  return (
    globalThis.crypto?.randomUUID?.() ||
    `team-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}
function useMemory<T>(
  key: string[],
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const client = useQueryClient();
  const query = useQuery<T>({
    queryKey: key,
    queryFn: () => initial,
    initialData: initial,
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  return [
    query.data as T,
    (next) =>
      client.setQueryData<T>(key, (previous) =>
        typeof next === "function"
          ? (next as (value: T) => T)(previous ?? initial)
          : next,
      ),
  ];
}
export function useInterventionTarget(projectId: string) {
  return useMemory<InterventionTarget | null>(
    ["team-active-draft", projectId],
    null,
  );
}
function revision(client: QueryClient, projectId: string, fallback = 0) {
  return client
    .getQueryCache()
    .getAll()
    .reduce((latest, query) => {
      if (
        ![
          "team",
          "team-task",
          "team-tasks",
          "team-instructions",
          "team-control",
        ].includes(String(query.queryKey[0])) ||
        query.queryKey[1] !== projectId
      )
        return latest;
      const value = (query.state.data as RecordData | undefined)
        ?.control_revision;
      return Number.isInteger(value) ? Math.max(latest, value) : latest;
    }, fallback);
}
async function synchronize(
  client: QueryClient,
  projectId: string,
  data: RecordData,
) {
  const current = data.control_revision ?? data.team?.control_revision;
  if (Number.isInteger(current))
    client.setQueryData(["team-control", projectId], {
      control_revision: current,
    });
  if (data.team) client.setQueryData(["team", projectId], data.team);
  if (data.task)
    client.setQueryData(["team-task", projectId, data.task.id], data);
  await client.invalidateQueries({
    predicate: (query) =>
      query.queryKey[1] === projectId &&
      !String(query.queryKey[0]).includes("draft"),
  });
}

export function TaskControlActions({
  projectId,
  task,
  controlRevision,
  onIntervene,
}: {
  projectId: string;
  task: RecordData;
  controlRevision: number;
  onIntervene: () => void;
}) {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const running = useRef(false);
  const operationIds = useRef(new Map<string, string>());
  const actions = rows(task.actions).filter((action) =>
    [
      "pause",
      "resume",
      "lock",
      "unlock",
      "hold_after",
      "release",
      "intervene",
    ].includes(action.id),
  );
  if (!actions.length) return null;
  async function execute(action: string) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    const control = revision(client, projectId, controlRevision);
    const signature = `${task.id}:${action}:${task.generation}:${control}`;
    if (!operationIds.current.has(signature))
      operationIds.current.set(signature, requestId());
    try {
      const result = await request(
        `/projects/${projectId}/tasks/${encodeURIComponent(task.id)}/actions`,
        {
          action,
          request_id: operationIds.current.get(signature),
          expected_control_revision: control,
          expected_generation: task.generation,
        },
      );
      setNotice(
        action === "pause"
          ? "暂停已登记，当前调用收尾后停止后续派发。"
          : action === "hold_after"
            ? "该任务完成后将等待你确认。"
            : action === "release"
              ? "等待确认的安排已解除，后续任务按当前状态继续。"
              : "任务安排已更新。",
      );
      await synchronize(client, projectId, result);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="team-control-actions" aria-label="任务控制">
      <div className="team-action-buttons">
        {actions.map((action) => (
          <button
            key={action.id}
            className="button small"
            disabled={busy || action.enabled === false}
            title={action.disabled_reason}
            onClick={() =>
              action.id === "intervene" ? onIntervene() : execute(action.id)
            }
          >
            {action.label}
          </button>
        ))}
      </div>
      {actions
        .filter((action) => action.enabled === false && action.disabled_reason)
        .map((action) => (
          <p className="muted" key={action.id}>
            {action.label}：{action.disabled_reason}
          </p>
        ))}
      <ErrorNotice error={error} />
      {notice && (
        <p className="team-control-notice" role="status">
          {notice}
        </p>
      )}
    </section>
  );
}
export function EnableTeamControl({
  projectId,
  team,
}: {
  projectId: string;
  team: RecordData;
}) {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const identity = useRef(requestId());
  const action = rows(team.actions).find(
    (item) => item.id === "enable_control",
  );
  if (!action) return null;
  async function enable() {
    setBusy(true);
    setError("");
    try {
      const result = await request(`/projects/${projectId}/team/actions`, {
        action: "enable",
        request_id: identity.current,
        expected_control_revision: revision(
          client,
          projectId,
          team.control_revision,
        ),
      });
      await synchronize(client, projectId, result);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="team-enable-control">
      <button
        className="button small"
        disabled={busy || action.enabled === false}
        title={action.disabled_reason}
        onClick={enable}
      >
        {action.label || "启用团队控制"}
      </button>
      {action.disabled_reason && (
        <p className="muted">{action.disabled_reason}</p>
      )}
      <ErrorNotice error={error} />
    </div>
  );
}

export function InterventionComposer({
  projectId,
  target,
  team,
  onClose,
  onRegistered,
}: {
  projectId: string;
  target: InterventionTarget;
  team: RecordData;
  onClose: () => void;
  onRegistered: (message: string) => void;
}) {
  const client = useQueryClient();
  const initial: Draft = {
    instruction: "",
    operation: "revise",
    taskIds: target.task ? [target.task.id] : [],
    generations: target.task
      ? { [target.task.id]: target.task.generation }
      : {},
    preserve: [],
    after: "auto",
    requestId: requestId(),
  };
  const [draft, storeDraft] = useMemory<Draft>(
    ["team-intervention-draft", projectId, target.type, target.id],
    initial,
  );
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [preview, setPreview] = useState<RecordData | null>(null);
  const running = useRef(false);
  const scopeQuery = useQuery({
    queryKey: [
      "team-tasks",
      projectId,
      "intervention",
      target.type,
      target.id,
      page,
    ],
    queryFn: () => {
      const params = new URLSearchParams({
        offset: String(page * 100),
        limit: "100",
      });
      if (target.type === "member") params.set("owner", target.id);
      if (target.type === "department") params.set("department", target.id);
      if (target.type === "task" && target.task?.shot_id)
        params.set("shot_id", target.task.shot_id);
      return request(`/projects/${projectId}/tasks?${params}`);
    },
    retry: false,
    refetchInterval: 5000,
  });
  const candidates = rows(scopeQuery.data?.tasks);
  const available =
    target.type === "task"
      ? ([target.task].filter(Boolean) as RecordData[])
      : candidates;
  const cachedTasks = client
    .getQueryCache()
    .getAll()
    .filter((query) => query.queryKey[1] === projectId)
    .flatMap((query) => {
      const data = query.state.data as RecordData | undefined;
      return data?.task ? [data.task] : rows(data?.tasks);
    });
  const taskName = (id: string) =>
    candidates.find((task) => task.id === id)?.label ||
    cachedTasks.find((task) => task.id === id)?.label ||
    id;
  const stale = draft.taskIds.some((id) => {
    const observed = [...candidates, ...cachedTasks].filter(
      (task) => task.id === id,
    );
    const latestGeneration = Math.max(
      ...observed.map((task) => task.generation),
    );
    return observed.length > 0 && latestGeneration !== draft.generations[id];
  });
  useEffect(() => {
    if (!draft.instruction.trim()) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [draft.instruction]);
  function update(change: Partial<Draft>) {
    storeDraft((old) => ({ ...old, ...change, requestId: requestId() }));
    setPreview(null);
    setError("");
  }
  function payload() {
    return {
      request_id: draft.requestId,
      expected_control_revision: revision(
        client,
        projectId,
        team.control_revision,
      ),
      target_type: target.type,
      target_id: target.id,
      task_ids: draft.taskIds,
      task_generations: draft.generations,
      instruction: draft.instruction.trim(),
      operation: draft.operation,
      preserve: draft.preserve,
      after: draft.after,
    };
  }
  async function inspect() {
    setBusy(true);
    setError("");
    try {
      const result = await request(
        `/projects/${projectId}/interventions/preview`,
        payload(),
      );
      setPreview(result);
      if (Number.isInteger(result.control_revision))
        client.setQueryData(["team-control", projectId], {
          control_revision: result.control_revision,
        });
      return result;
    } catch (failure) {
      setError((failure as Error).message);
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (running.current || !draft.instruction.trim() || !draft.taskIds.length)
      return;
    running.current = true;
    setBusy(true);
    setError("");
    try {
      const checked =
        preview ||
        (await request<RecordData>(
          `/projects/${projectId}/interventions/preview`,
          payload(),
        ));
      setPreview(checked);
      if (Number.isInteger(checked.control_revision))
        client.setQueryData(["team-control", projectId], {
          control_revision: checked.control_revision,
        });
      if (rows(checked.conflicts).length) return;
      const result = await request(
        `/projects/${projectId}/interventions`,
        payload(),
      );
      const instruction = result.instruction || {};
      if (
        [
          "conflict",
          "blocked",
          "needs_attention",
          "needs_decision",
          "rejected",
        ].includes(instruction.status)
      ) {
        setError(instruction.reason || "指令需要处理，已保留你的修改要求。");
        await synchronize(client, projectId, result);
        return;
      }
      onRegistered("指令已登记，当前调用收尾后生效。");
      onClose();
      client.removeQueries({
        queryKey: [
          "team-intervention-draft",
          projectId,
          target.type,
          target.id,
        ],
        exact: true,
      });
      await synchronize(client, projectId, result);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  async function reloadBaseline() {
    setBusy(true);
    setError("");
    try {
      const fresh = await Promise.all(
        draft.taskIds.map((id) =>
          request(`/projects/${projectId}/tasks/${encodeURIComponent(id)}`),
        ),
      );
      const generations = Object.fromEntries(
        fresh.map((data) => [data.task.id, data.task.generation]),
      );
      for (const data of fresh)
        client.setQueryData(["team-task", projectId, data.task.id], data);
      update({ generations });
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const preservedOptions = [
    ...new Set([
      ...candidates
        .filter((task) => !draft.taskIds.includes(task.id))
        .map((task) => task.id),
      ...(preview?.affected_tasks || []).filter(
        (id: string) => !draft.taskIds.includes(id),
      ),
      ...draft.preserve,
    ]),
  ];
  return (
    <Modal
      id="team-intervention-dialog"
      title={`介入并下达要求 · ${target.label}`}
      onClose={onClose}
    >
      <form className="team-intervention-form" onSubmit={submit}>
        <p className="muted">
          指令立即登记并停止受影响的后续派发；当前调用收尾后切换。终片、预算和新增费用授权仍由你决定。
        </p>
        <fieldset disabled={busy}>
          <legend>修改范围</legend>
          {scopeQuery.isPending && !target.task ? (
            <Loading />
          ) : (
            available.map((task) => (
              <label className="team-scope-choice" key={task.id}>
                <input
                  type="checkbox"
                  aria-label={"修改 " + task.label}
                  checked={draft.taskIds.includes(task.id)}
                  disabled={target.type === "task"}
                  onChange={(event) =>
                    update({
                      taskIds: event.target.checked
                        ? [...draft.taskIds, task.id]
                        : draft.taskIds.filter((id) => id !== task.id),
                      generations: event.target.checked
                        ? { ...draft.generations, [task.id]: task.generation }
                        : Object.fromEntries(
                            Object.entries(draft.generations).filter(
                              ([id]) => id !== task.id,
                            ),
                          ),
                      preserve: draft.preserve.filter((id) => id !== task.id),
                    })
                  }
                />
                <span>
                  {task.label}
                  <small>
                    {task.id} · 第{" "}
                    {draft.generations[task.id] ?? task.generation} 版
                  </small>
                </span>
              </label>
            ))
          )}
          {target.type !== "task" && (
            <div className="team-pagination">
              <small>
                已选 {draft.taskIds.length} 项 / 共{" "}
                {scopeQuery.data?.total ?? 0} 项
              </small>
              <button
                className="button small"
                type="button"
                disabled={!page}
                onClick={() => setPage(page - 1)}
              >
                上一页任务
              </button>
              <button
                className="button small"
                type="button"
                disabled={(page + 1) * 100 >= (scopeQuery.data?.total ?? 0)}
                onClick={() => setPage(page + 1)}
              >
                下一页任务
              </button>
            </div>
          )}
        </fieldset>
        <ErrorNotice error={scopeQuery.error} />
        <Field label="处理方式">
          <select
            value={draft.operation}
            disabled={busy}
            onChange={(event) =>
              update({ operation: event.target.value as Draft["operation"] })
            }
          >
            <option value="revise">按新要求修改</option>
            <option value="retry">重新执行</option>
          </select>
        </Field>
        <Field label="新的制作要求">
          <textarea
            required
            maxLength={4000}
            rows={5}
            value={draft.instruction}
            disabled={busy}
            placeholder="说明需要修改什么，以及必须保留的内容。"
            onChange={(event) => update({ instruction: event.target.value })}
          />
        </Field>
        {!!preservedOptions.length && (
          <fieldset disabled={busy}>
            <legend>保留现有成果</legend>
            {preservedOptions.map((id) => (
              <label className="team-scope-choice" key={id}>
                <input
                  type="checkbox"
                  checked={draft.preserve.includes(id)}
                  onChange={(event) =>
                    update({
                      preserve: event.target.checked
                        ? [...draft.preserve, id]
                        : draft.preserve.filter((value) => value !== id),
                    })
                  }
                />
                <span>
                  {taskName(id)}
                  <small>{id}</small>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        <Field label="完成后的安排">
          <select
            value={draft.after}
            disabled={busy}
            onChange={(event) =>
              update({ after: event.target.value as Draft["after"] })
            }
          >
            <option value="auto">自动继续后续任务</option>
            <option value="wait">本次任务完成后等我确认</option>
          </select>
        </Field>
        <p className="muted">
          草稿在本次应用会话中保留。关闭窗口或退出应用前，请提交或另行保存要求。
        </p>
        {stale && (
          <div className="notice">
            任务版本已变化，草稿仍依据打开时的版本。核对后可重新读取基线。
          </div>
        )}
        {(stale || error) && (
          <button
            className="button small"
            type="button"
            disabled={busy}
            onClick={reloadBaseline}
          >
            重新读取基线（保留要求）
          </button>
        )}
        <ErrorNotice error={error} />
        {preview && (
          <section className="team-impact-preview" aria-label="返工影响预览">
            <h3>返工影响</h3>
            <p>
              需要更新：
              {(preview.affected_tasks || []).map(taskName).join("、") || "无"}
            </p>
            <p>
              保留成果：
              {(preview.preserved_tasks || []).map(taskName).join("、") || "无"}
            </p>
            {rows(preview.conflicts).map((conflict, index) => (
              <p className="notice" key={conflict.task_id || index}>
                {taskName(conflict.task_id)}：{conflict.reason}
              </p>
            ))}
          </section>
        )}
        <div className="team-action-buttons">
          <button
            type="button"
            className="button"
            disabled={
              busy || !draft.taskIds.length || !draft.instruction.trim()
            }
            onClick={inspect}
          >
            查看影响
          </button>
          <button
            className="button primary"
            type="submit"
            disabled={
              busy ||
              stale ||
              !draft.taskIds.length ||
              !draft.instruction.trim()
            }
          >
            {busy ? "正在登记…" : "登记指令并执行"}
          </button>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={onClose}
          >
            稍后继续编辑
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function TeamInstructions({
  projectId,
  instructions,
  controlRevision,
}: {
  projectId: string;
  instructions: RecordData[];
  controlRevision: number;
}) {
  const client = useQueryClient();
  const [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  const labels: Record<string, string> = {
    queued: "已登记",
    planning: "正在安排",
    needs_decision: "需要你处理",
    pending: "已登记",
    applied: "已应用",
    waiting: "等待安全边界",
    needs_attention: "需要处理",
    conflict: "存在冲突",
    blocked: "需要处理",
    dismissed: "已撤回",
    rejected: "未应用",
  };
  if (!instructions.length) return null;
  async function dismiss(id: string) {
    setBusy(id);
    setError("");
    try {
      const result = await request(
        `/projects/${projectId}/interventions/${encodeURIComponent(id)}/dismiss`,
        {
          request_id: requestId(),
          expected_control_revision: revision(
            client,
            projectId,
            controlRevision,
          ),
        },
      );
      await synchronize(client, projectId, result);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="team-instructions" aria-label="已登记的制作指令">
      <h3>制作指令</h3>
      <ErrorNotice error={error} />
      {instructions.map((item) => (
        <article className="team-attempt" key={item.id}>
          <div className="row-between">
            <strong>{item.target_id}</strong>
            <Badge
              status={item.status}
              label={labels[item.status] || item.status}
            />
          </div>
          <p>{item.instruction}</p>
          {item.reason && <p className="notice">{item.reason}</p>}
          {[
            "queued",
            "needs_decision",
            "pending",
            "waiting",
            "needs_attention",
            "conflict",
            "blocked",
            "rejected",
          ].includes(item.status) && (
            <button
              className="text-link"
              disabled={!!busy}
              onClick={() => dismiss(item.id)}
            >
              撤回此指令
            </button>
          )}
        </article>
      ))}
    </section>
  );
}
