import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request, rows, type RecordData } from "./api";
import { Empty, ErrorNotice, Field, Loading, Panel } from "./ui";
import "./generation-pipeline.css";

export function GenerationPipeline({ project }: { project: RecordData }) {
  return <ProjectPipeline key={project.id} project={project} />;
}
function ProjectPipeline({ project }: { project: RecordData }) {
  const client = useQueryClient();
  const path = `/projects/${project.id}/generation-pipeline`;
  const key = ["generation-pipeline", project.id];
  const query = useQuery({
    queryKey: key,
    queryFn: () => request(path),
    refetchInterval: 5000,
  });
  const [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [pending, setPending] = useState<{
    body: RecordData;
    suffix: string;
  } | null>(null);
  const [shot, setShot] = useState("");
  async function operate(body: RecordData, suffix = "", retry = false) {
    if (busy) return;
    const operation =
      retry && pending
        ? pending
        : {
            suffix,
            body: {
              ...body,
              request_id: crypto.randomUUID().replaceAll("-", ""),
              expected_revision: query.data?.revision,
              confirmed: body.confirmed ?? confirmed,
            },
          };
    setPending(operation);
    setBusy(true);
    setError("");
    try {
      await request(path + operation.suffix, operation.body);
      setPending(null);
      setConfirmed(false);
      await client.invalidateQueries({ queryKey: key });
      await client.invalidateQueries({ queryKey: ["workspace", project.id] });
      await client.invalidateQueries({ queryKey: ["drama", project.id] });
      await client.invalidateQueries({
        queryKey: ["long-episode", project.id],
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const data = query.data,
    shots = rows(data?.shots),
    records = rows(data?.records);
  const selected = shot || shots[0]?.shot_id || "";
  const spec = shots.find((s) => s.shot_id === selected);
  const relevant = records.filter(
    (r) => !selected || !r.document?.shot_id || r.document.shot_id === selected,
  );
  if (query.isPending) return <Loading />;
  return (
    <div className="stack generation-pipeline" data-generation-pipeline>
      <Panel
        title="生成提示词流水线"
        description="批准规格 → 结构化制作计划 → 实际适配器参数。"
      >
        <ErrorNotice error={error || query.error} />
        <p>
          当前项目：
          {data?.enabled ? "使用生成提示词流水线 v1" : "沿用原生成流程"}。
        </p>
        <p className="muted">
          启用本身不执行模型或生成。后续制作任务将编译单帧画面、视频起始/动作/结束及配音参数。旧项目与子项目独立选择版本，已批准且输入未变的成果继续复用。
        </p>
        <p className="muted">
          视频编译先读取真实首帧；图片描述、历史审核与分镜要求均不能冒充像素证据。矛盾、能力不足、未知结果会暂停生成。
        </p>
        <label className="pipeline-confirm">
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy || !!pending}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>我确认只更改此项目后续制作流程；已有成果保留</span>
        </label>
        <div className="pipeline-actions">
          <button
            className="button primary"
            disabled={!confirmed || busy || !!pending || !data}
            onClick={() => operate({ enabled: !data?.enabled })}
          >
            {data?.enabled ? "回退原生成流程" : "启用生成提示词流水线"}
          </button>
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
        <p className="muted">
          提示词编译与首帧观察暂限已有内网模型。先暂停并等待在途任务结束后切换。实际制作仍受既有任务授权与预算控制；本页不会启动收费提交，也不能批准作品。
        </p>
      </Panel>
      <Panel
        title="批准规格预览"
        description="此处是现有镜头材料；未运行编译时，不显示为模型已执行。"
      >
        {shots.length ? (
          <>
            <Field label="选择镜头">
              <select
                value={selected}
                onChange={(e) => setShot(e.target.value)}
              >
                {shots.map((s) => (
                  <option key={s.shot_id} value={s.shot_id}>
                    {s.shot_id}
                  </option>
                ))}
              </select>
            </Field>
            {spec?.error && <p role="alert">{spec.error}</p>}
            {spec?.source && (
              <>
                <p>批准规格（不代表已执行）</p>
                <div className="pipeline-grid">
                  <div>
                    <h3>故事与硬要求</h3>
                    <pre>{JSON.stringify(spec.source.story_goal, null, 2)}</pre>
                  </div>
                  <div>
                    <h3>镜头、动作与状态</h3>
                    <pre>
                      {JSON.stringify(spec.source.approved_shot, null, 2)}
                    </pre>
                  </div>
                </div>
              </>
            )}
          </>
        ) : (
          <Empty title="尚无已批准镜头">
            完成故事与分镜确认后，可在制作单元中预览；此页面不会自动补写分镜。
          </Empty>
        )}
      </Panel>
      <Panel
        title="实际编译与首帧证据"
        description="展示最近300份编译及请求准备记录和全部未解决状态；提交结果以原生成任务回执为准。"
      >
        {!records.length && (
          <Empty title="尚未执行新流水线">
            启用后按原入口执行制作或定向返修，实际编译结果会出现在这里。已有素材不会因启用而重做。
          </Empty>
        )}
        {relevant.map((r) => {
          const d = r.document || {},
            evidence =
              d.first_frame ||
              (d.scope === "first_frame_pixels_only" ? d : null);
          return (
            <article key={r.id} className="pipeline-record">
              <h3>
                {d.shot_id || "编译记录"} ·{" "}
                {r.state === "running"
                  ? "正在编译"
                  : r.state === "unknown"
                    ? "结果未知，未重投"
                    : r.state === "closed"
                      ? "已结束，证据保留"
                      : r.state === "blocked"
                        ? "编译未通过，未提交生成"
                        : d.phase === "request"
                          ? "生成请求（已准备）"
                          : d.phase === "audio"
                            ? "配音参数"
                            : evidence && !d.phase
                              ? "首帧观察"
                              : d.phase === "image"
                                ? "单帧画面计划"
                                : "视频制作计划"}
              </h3>
              <p className="muted">记录 {r.id}</p>
              {evidence && (
                <>
                  <p>
                    首帧哈希：{evidence.sha256} · 观察模型：{evidence.model}
                  </p>
                  <p>{evidence.observation?.summary}</p>
                  <p className="muted">
                    仅限首帧可见内容，不代表动作完成或整片审核通过。
                  </p>
                  <pre>{JSON.stringify(evidence.observation, null, 2)}</pre>
                </>
              )}
              {d.phase === "request" && (
                <p className="muted">
                  此记录保存精确请求和返修参数；已准备不等于供应商已受理或生成成功。
                </p>
              )}
              {d.plan && (
                <details open>
                  <summary>结构化制作计划</summary>
                  <pre>{JSON.stringify(d.plan, null, 2)}</pre>
                </details>
              )}
              {d.parameters && (
                <details>
                  <summary>实际适配器参数</summary>
                  <pre>{JSON.stringify(d.parameters, null, 2)}</pre>
                </details>
              )}
              {d.source && (
                <details>
                  <summary>批准输入与实际引用</summary>
                  <pre>{JSON.stringify(d.source, null, 2)}</pre>
                </details>
              )}
              {(r.state || d.phase === "audio") && (
                <pre>{JSON.stringify(d, null, 2)}</pre>
              )}
              {r.state === "unknown" && (
                <button
                  className="button"
                  disabled={busy || !!pending}
                  onClick={() => {
                    if (
                      window.confirm(
                        "请先核实模型供应商或服务端回执。确认结束此未知编译并保留全部记录？此操作不会重新请求模型。",
                      )
                    )
                      operate({ record_id: r.id, confirmed: true }, "/settle");
                  }}
                >
                  核实并结束未知编译
                </button>
              )}
            </article>
          );
        })}
        <p className="muted">
          真实效果结论尚未建立。模型文本、结构合法或观察评分不能代替实际媒体审核。返修需更换首帧时，先定向修订并确认图片，再读取新帧编译视频。
        </p>
      </Panel>
    </div>
  );
}
