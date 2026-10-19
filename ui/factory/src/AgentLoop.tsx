import { StudioSelect } from "./StudioSelect";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request, rows, type RecordData } from "./api";
import { Empty, ErrorNotice, Field, Loading, Panel } from "./ui";
import "./generation-pipeline.css";

const defaults = {
  protocol: "auto",
  max_turns: 12,
  max_actions: 32,
  max_output_tokens: 12000,
  total_output_tokens: 65536,
  max_wall_seconds: 600,
};
const limits = [
  ["max_turns", "模型轮数", 4, 32],
  ["max_actions", "工具动作数", 4, 96],
  ["max_output_tokens", "单次输出上限", 128, 16384],
  ["total_output_tokens", "总输出上限", 512, 262144],
  ["max_wall_seconds", "任务时限（秒）", 5, 1800],
] as const;
const states: Record<string, string> = {
  running: "执行中",
  paused: "已暂停，可恢复",
  completed: "候选已提交业务校验",
  handoff: "已请求上游补料",
  unknown: "请求结果未知，停止新增请求",
  closed: "未知请求已结束",
  blocked: "需要处理",
  superseded: "输入已更新，旧结果不可提交",
  exhausted: "额度已用尽",
};
const events: Record<string, string> = {
  agent_start: "恢复任务",
  turn_start: "请求下一步",
  turn_response: "模型响应已保存",
  tool_start: "开始动作",
  tool_end: "动作结果",
  visual_delegate: "交给视觉岗位",
  agent_end: "本次循环结束",
  agent_paused: "暂停",
};
export function AgentLoop({ project }: { project: RecordData }) {
  return <ProjectAgent key={project.id} project={project} />;
}
function ProjectAgent({ project }: { project: RecordData }) {
  const client = useQueryClient(),
    path = `/projects/${project.id}/agent-loop`,
    key = ["agent-loop", project.id];
  const query = useQuery({
    queryKey: key,
    queryFn: () => request(path),
    refetchInterval: 5000,
  });
  const [settings, setSettings] = useState<RecordData | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [pending, setPending] = useState<{
      body: RecordData;
      suffix: string;
    } | null>(null),
    [selected, setSelected] = useState("");
  const detail = useQuery({
    queryKey: [...key, selected],
    queryFn: () => request(`${path}/${selected}`),
    enabled: !!selected,
    refetchInterval: 5000,
  });
  const data = query.data,
    config = settings || data?.snapshot?.settings || data?.defaults || defaults;
  async function operate(body: RecordData, suffix = "", retry = false) {
    if (busy) return;
    const op =
      retry && pending
        ? pending
        : {
            suffix,
            body: {
              ...body,
              request_id: crypto.randomUUID().replaceAll("-", ""),
              expected_revision: data?.revision,
              confirmed,
            },
          };
    setPending(op);
    setBusy(true);
    setError("");
    try {
      await request(path + op.suffix, op.body);
      setPending(null);
      setConfirmed(false);
      setSettings(null);
      for (const k of [
        key,
        ["workspace", project.id],
        ["drama", project.id],
        ["long-episode", project.id],
      ])
        await client.invalidateQueries({ queryKey: k });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (query.isPending) return <Loading />;
  return (
    <div className="stack generation-pipeline" data-agent-loop>
      <Panel
        title="Agent 执行循环"
        description="读取材料 → 规划 → 使用工具 → 检查 → 提交候选或交接。"
      >
        <ErrorNotice error={error || query.error} />
        <p>
          当前项目：
          {data?.enabled ? "使用有界 Agent 循环 v1" : "沿用原执行流程"}。
        </p>
        <p>
          在岗位任务中主动读取授权资料、检查实际媒体并补料。候选继续经过原业务校验与独立审核。启用本身不启动任务，已批准素材继续保留。
        </p>
        <div className="pipeline-grid">
          <Field label="动作协议">
            <StudioSelect
              disabled={busy || !!pending}
              value={config.protocol}
              onChange={(e) =>
                setSettings({ ...config, protocol: e.target.value })
              }
            >
              <option value="auto">按模型连接自动选择</option>
              <option value="native">原生工具调用</option>
              <option value="structured">结构化动作</option>
            </StudioSelect>
          </Field>
          {limits.map(([name, label, min, max]) => (
            <Field key={name} label={label}>
              <input
                type="number"
                min={min}
                max={max}
                value={config[name]}
                disabled={busy || !!pending}
                onChange={(e) =>
                  setSettings({ ...config, [name]: Number(e.target.value) })
                }
              />
            </Field>
          ))}
        </div>
        <p className="muted">
          额度按一次岗位任务的固定输入计算，视觉补证计入总输出。仅使用已有内网推理；费用未知的决策请求会停止。制作、素材生成仍受原预算约束。暂停并等待在途任务结束后应用配置，历史任务保持原快照。
        </p>
        <label className="pipeline-confirm">
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy || !!pending}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>我确认将配置应用于此项目后续岗位任务，并保留已有成果</span>
        </label>
        <div className="pipeline-actions">
          <button
            className="button primary"
            disabled={!confirmed || busy || !!pending || !data}
            onClick={() => operate({ enabled: true, settings: config })}
          >
            {data?.enabled ? "应用新执行配置" : "启用 Agent 循环"}
          </button>
          {data?.enabled && (
            <button
              className="button"
              disabled={!confirmed || busy || !!pending}
              onClick={() => operate({ enabled: false, settings: config })}
            >
              回退原执行流程
            </button>
          )}
        </div>
        {pending && !busy && (
          <div className="pipeline-actions">
            <button
              className="button"
              onClick={() => operate(pending.body, pending.suffix, true)}
            >
              重试此操作
            </button>
            <button
              className="button"
              onClick={() => {
                setPending(null);
                setError("");
                client.invalidateQueries({ queryKey: key });
              }}
            >
              刷新后重新准备
            </button>
          </div>
        )}
      </Panel>
      {!!rows(data?.preflight).length && (
        <Panel
          title="岗位能力预检"
          description="根据当前团队连接检查，不会调用模型；实际任务使用当次模型快照。"
        >
          {rows(data?.preflight).map((r) => (
            <p key={r.role}>
              <strong>{r.label}</strong> · {r.model || "未配置"} ·{" "}
              {r.ready ? "可执行" : "需处理"} ·{" "}
              {r.ready ? r.protocol : r.reason}
            </p>
          ))}
        </Panel>
      )}
      <Panel
        title="任务与动作记录"
        description="最近30次岗位任务。技术合格、合约检查与首帧观察均不能代替成片验收。"
      >
        {!rows(data?.runs).length && (
          <Empty title="尚无 Agent 执行证据">
            启用后从原制作入口继续任务；未执行的岗位不会显示为完成。
          </Empty>
        )}
        {rows(data?.runs).map((r) => (
          <article key={r.id} className="pipeline-record">
            <h3>
              {r.role} · {r.stage}
            </h3>
            <p>
              {states[r.state] || r.state} ·{" "}
              {r.evidence_type === "synthetic_fixture"
                ? "合成协议样例，非真实模型效果"
                : "模型执行记录"}
            </p>
            <p>
              模型轮数 {r.turns || 0} · 已记录输出 {r.spent_output_tokens || 0}{" "}
              · 独立质量结论：未由本循环建立
            </p>
            {r.reason && <p role="status">{r.reason}</p>}
            {!!r.plan?.length && (
              <ol>
                {r.plan.map((s: string, i: number) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
            )}
            <details>
              <summary>配置与动作时间线</summary>
              <pre>{JSON.stringify(r.snapshot?.settings, null, 2)}</pre>
              <ol>
                {rows(r.events).map((e) => (
                  <li key={e.seq}>
                    {events[e.event] || e.event}
                    {e.tool ? ` · ${e.tool}` : ""} · {e.summary}
                    {e.arguments && (
                      <pre>{JSON.stringify(e.arguments, null, 2)}</pre>
                    )}
                  </li>
                ))}
              </ol>
            </details>
            <button className="button" onClick={() => setSelected(r.id)}>
              查看任务产物与来源
            </button>
            {r.state === "unknown" && (
              <button
                className="button"
                disabled={!confirmed || busy || !!pending}
                onClick={() => operate({ run_id: r.id }, "/settle")}
              >
                核实后结束此未知请求
              </button>
            )}
          </article>
        ))}
        {selected && (
          <div className="pipeline-record">
            <h3>岗位候选与来源</h3>
            <ErrorNotice error={detail.error} />
            {detail.isPending ? (
              <Loading />
            ) : (
              <>
                <p>
                  候选尚需业务校验和独立审核；未自动批准或发布。结束未知请求保留回执，不重投；继续前须重新显式应用配置。
                </p>
                <pre>
                  {JSON.stringify(
                    {
                      candidate: detail.data?.candidate,
                      materials: detail.data?.materials,
                      handoff: detail.data?.handoff,
                    },
                    null,
                    2,
                  )}
                </pre>
              </>
            )}
            <button className="button" onClick={() => setSelected("")}>
              关闭产物详情
            </button>
          </div>
        )}
      </Panel>
    </div>
  );
}
