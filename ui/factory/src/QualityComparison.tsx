import { StudioSelect } from "./StudioSelect";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { date, request, roleName, rows, type RecordData } from "./api";
import { Empty, ErrorNotice, Field, Loading, Panel } from "./ui";
import "./quality-comparison.css";

const dimensions: Record<string, string> = {
  creation_review: "故事提案",
  resolution_review: "故事目标",
  script_review: "剧本与分镜",
  plan_review: "全剧规划审核",
  image: "首帧",
  video: "视频",
  hook: "开场吸引力",
  causality: "因果",
  motivation: "人物动机",
  originality: "原创组合",
  closure: "结局闭合",
  feasibility: "制作可行性",
  proposal: "故事候选",
  voice_timing: "对白时长实测",
  delivery_clean: "无字幕成片技术检查",
  delivery_subtitled: "字幕版成片技术检查",
};
const statuses: Record<string, string> = {
  pass: "记录通过",
  fail: "记录未通过",
  recorded: "已有记录",
  unavailable: "尚无有效结论",
  technical_pass_semantic_pending: "技术通过，内容待审",
};
const value = (v: unknown, suffix = "") =>
  typeof v === "number" && Number.isFinite(v) ? `${v}${suffix}` : "未知";

export function QualityComparison({ project }: { project: RecordData }) {
  return <ProjectComparison key={project.id} project={project} />;
}

function ProjectComparison({ project }: { project: RecordData }) {
  const client = useQueryClient();
  const path = `/projects/${project.id}/quality-baselines`;
  const key = ["quality-baselines", project.id];
  const [label, setLabel] = useState("");
  const [baseline, setBaseline] = useState("");
  const [candidate, setCandidate] = useState("");
  // Retain the request identity after a timeout so retrying cannot duplicate it.
  const [pending, setPending] = useState<RecordData | null>(null);
  const list = useQuery({ queryKey: key, queryFn: () => request(path) });
  const save = useMutation({
    mutationFn: (body: RecordData) => request(path, body),
    onSuccess: async (result) => {
      setCandidate(result.id);
      setPending(null);
      setLabel("");
      await client.invalidateQueries({ queryKey: key });
    },
  });
  const selected = candidate || baseline;
  const detail = useQuery({
    queryKey: [...key, "detail", selected],
    enabled: !!selected,
    queryFn: () => request(`${path}/${selected}`),
  });
  const comparison = useQuery({
    queryKey: [...key, "compare", baseline, candidate],
    enabled: !!baseline && !!candidate && baseline !== candidate,
    queryFn: () =>
      request(`${path}/compare?baseline=${baseline}&candidate=${candidate}`),
  });
  const snapshots = rows(list.data?.baselines);
  function freeze() {
    const body = pending || {
      request_id: crypto.randomUUID().replaceAll("-", ""),
      label: label.trim(),
      expected_revision: project.revision,
    };
    setPending(body);
    save.mutate(body);
  }
  return (
    <div className="stack quality-comparison" data-quality-comparison>
      <Panel
        title="质量基线与比较"
        description="保存同一需求的制作版本，分别查看质量证据与运行投入。"
      >
        <p className="muted">
          快照保存当时的需求、制作版本及已有调用证据。此操作不启动生成或模型审核。
        </p>
        <form
          className="quality-freeze"
          onSubmit={(e) => {
            e.preventDefault();
            freeze();
          }}
        >
          <Field label="快照名称">
            <input
              value={label}
              maxLength={80}
              required={!pending}
              disabled={!!pending || save.isPending}
              placeholder="例如：修改分镜提示词前"
              onChange={(e) => setLabel(e.target.value)}
            />
          </Field>
          <button
            className="button primary"
            type="submit"
            disabled={save.isPending || (!pending && !label.trim())}
          >
            {save.isPending
              ? "正在冻结…"
              : pending
                ? "重试保存此快照"
                : "冻结当前版本"}
          </button>
          {pending && !save.isPending && (
            <button
              className="button"
              type="button"
              onClick={() => {
                setPending(null);
                save.reset();
                client.invalidateQueries({ queryKey: key });
                client.invalidateQueries({
                  queryKey: ["workspace", project.id],
                });
                client.invalidateQueries({ queryKey: ["drama", project.id] });
                client.invalidateQueries({
                  queryKey: ["long-episode", project.id],
                });
              }}
            >
              同步并重新准备
            </button>
          )}
        </form>
        <ErrorNotice error={save.error} />
        <ErrorNotice error={list.error} />
        {list.isPending ? (
          <Loading />
        ) : list.error && !list.data ? null : snapshots.length === 0 ? (
          <Empty title="尚未保存质量基线">
            先冻结当前版本，修改制作方案后再保存一个版本，即可比较。
          </Empty>
        ) : (
          <>
            <div className="quality-selectors">
              {(
                [
                  ["基线版本", baseline, setBaseline],
                  ["候选版本", candidate, setCandidate],
                ] as const
              ).map(([name, current, set]) => (
                <Field key={name} label={name}>
                  <StudioSelect value={current} onChange={(e) => set(e.target.value)}>
                    <option value="">请选择版本</option>
                    {snapshots.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.label} · {date(row.created_at)} · r
                        {row.project_revision}
                      </option>
                    ))}
                  </StudioSelect>
                </Field>
              ))}
            </div>
            <p className="muted">
              已保存 {snapshots.length} 个版本。快照不会随项目后续修改而改变。
            </p>
          </>
        )}
        {!!list.data?.unreadable_baselines && (
          <p role="status">
            有 {list.data.unreadable_baselines}{" "}
            份历史基线缺失或校验失败，已保留原文件，请检查服务日志和备份。
          </p>
        )}
        {baseline && baseline === candidate && (
          <p role="status">请选择两份不同的基线。</p>
        )}
      </Panel>
      <ErrorNotice error={comparison.error || detail.error} />
      {comparison.isFetching && <Loading />}
      {comparison.data && baseline !== candidate && (
        <Panel title="版本比较" description="质量结论与运行投入分别展示。">
          <p className="quality-conclusion">尚未建立真实效果改善结论</p>
          {!comparison.data.same_inputs &&
            rows(comparison.data.reasons).map((reason, i) => (
              <p role="status" key={i}>
                {String(reason)}
              </p>
            ))}
          <p className="muted">{comparison.data.note} 匿名效果评测尚未运行。</p>
          <div className="quality-pair">
            <Snapshot value={comparison.data.baseline} title="基线" />
            <Snapshot value={comparison.data.candidate} title="候选" />
          </div>
          <h3>审核记录变化</h3>
          {rows(comparison.data.review_changes).length ? (
            rows(comparison.data.review_changes).map((row, i) => (
              <article className="quality-change" key={i}>
                <h4>
                  {dimensions[row.scope] || row.scope} {row.shot_id}
                </h4>
                <div className="quality-pair">
                  <Review review={row.before} title="基线记录" />
                  <Review review={row.after} title="候选记录" />
                </div>
              </article>
            ))
          ) : (
            <p>两份快照的已有审核证据相同或均未记录。</p>
          )}
        </Panel>
      )}
      {detail.isPending && selected && <Loading />}
      {detail.data && (
        <Panel
          title="所选版本的证据"
          description="已有审核记录未在本页重新验证，不能代替新的独立评测。"
        >
          <Snapshot value={detail.data} title="" />
          <h3>作品质量记录</h3>
          {rows(detail.data.reviews).length ? (
            rows(detail.data.reviews).map((review, i) => (
              <Review
                key={i}
                review={review}
                title={`${dimensions[review.scope] || review.scope} ${review.shot_id || ""}`}
              />
            ))
          ) : (
            <p>
              尚无作品审核证据。故事、人物、动作、声音及整片效果均不能据此判为合格。
            </p>
          )}
          <details>
            <summary>
              调用与制作记录（{rows(detail.data.records).length}）
            </summary>
            {rows(detail.data.records).map((record, i) => (
              <article className="quality-call" key={i}>
                <strong>
                  {record.shot_id || roleName(record.role || "") || "制作调用"}{" "}
                  · {record.stage || "角色调用"}
                </strong>
                <p>
                  实际返回模型：{record.model || "未知"} · 配置模型：
                  {record.configured_model || "未记录"}
                </p>
                <p>
                  状态：{record.status || "未知"} · 耗时：
                  {value(record.elapsed_seconds, " 秒")}
                </p>
                {record.prompt && (
                  <p>
                    提示词版本：{record.prompt.version || "未知"} ·{" "}
                    {record.prompt.status === "registered"
                      ? "已登记"
                      : "未登记"}
                  </p>
                )}
                {record.prompt?.input_sha256 && (
                  <code>输入 {record.prompt.input_sha256}</code>
                )}
                {record.sampling && (
                  <p>
                    采样参数：
                    {Object.entries(record.sampling)
                      .filter(([, v]) => v != null)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(" · ") || "未记录"}
                  </p>
                )}
              </article>
            ))}
          </details>
        </Panel>
      )}
      <Panel title="如何建立效果结论">
        <ol>
          <li>保存优化前后的版本，确认需求和固定材料一致。</li>
          <li>
            按现有冻结匿名评测协议预先确定样例、模型、提示词和预算；保留失败样本。
          </li>
          <li>
            由独立评审比较故事与分镜，并检查实际画面、动作、声音和成片。运行更快或审核分数更高都不能单独证明效果改善。
          </li>
        </ol>
        <p className="muted">
          本阶段保存和比较已有证据；真实模型样例需单独选择并授权预算。匿名评测仍通过已有离线流程执行。
        </p>
      </Panel>
    </div>
  );
}

function Snapshot({
  value: snapshot,
  title,
}: {
  value: RecordData;
  title: string;
}) {
  const metrics = snapshot.metrics || {};
  return (
    <section
      className="quality-snapshot"
      aria-label={(title ? title + "：" : "") + snapshot.label}
    >
      <h3>
        {title && `${title} · `}
        {snapshot.label}
      </h3>
      <p className="muted">
        {date(snapshot.created_at)} · 项目 r{snapshot.project_revision} · 制作 v
        {snapshot.versions?.production_revision ?? "未知"}
      </p>
      <dl className="quality-metrics">
        <div>
          <dt>已记录操作</dt>
          <dd>{value(metrics.observed_operations)}</dd>
        </div>
        <div>
          <dt>其中失败</dt>
          <dd>{value(metrics.failed_operations)}</dd>
        </div>
        <div>
          <dt>操作耗时合计</dt>
          <dd>{value(metrics.elapsed_seconds_sum, " 秒")}</dd>
        </div>
        <div>
          <dt>制作累计用时</dt>
          <dd>{value(metrics.production_elapsed_seconds, " 秒")}</dd>
        </div>
        <div>
          <dt>JSON 结构重试</dt>
          <dd>{value(metrics.schema_retries)}</dd>
        </div>
        <div>
          <dt>提案返修轮次</dt>
          <dd>{value(metrics.review_repairs?.creation_review)}</dd>
        </div>
        <div>
          <dt>剧本返修轮次</dt>
          <dd>{value(metrics.review_repairs?.script_review)}</dd>
        </div>
        <div>
          <dt>规划返修轮次</dt>
          <dd>{value(metrics.review_repairs?.plan_review)}</dd>
        </div>
      </dl>
      <p className="muted">
        截至冻结时的累计记录；耗时已知 {metrics.elapsed_known_count ?? 0}/
        {metrics.observed_operations ?? 0}{" "}
        项，操作耗时合计包含并行工作，不是端到端用时。
      </p>
      {rows(metrics.costs).length ? (
        rows(metrics.costs).map((cost) => (
          <p key={cost.currency}>
            {cost.currency} · 已确认部分：
            {cost.confirmed_count
              ? (cost.confirmed_micros / 1e6).toFixed(4)
              : "未知"}
            {" · "}估算部分：
            {cost.estimated_count
              ? (cost.estimated_micros / 1e6).toFixed(4)
              : "未知"}
          </p>
        ))
      ) : (
        <p>费用：未知（未记录可汇总的云任务费用）</p>
      )}
      {!!metrics.unconfirmed_cost_count && (
        <p>尚有 {metrics.unconfirmed_cost_count} 项云任务费用未确认。</p>
      )}
      <p className="muted">
        费用范围为已有云任务记录，未计价的文本或本地模型调用不按免费计。
      </p>
      {rows(snapshot.gaps).map((gap, i) => (
        <p role="status" key={i}>
          {String(gap)}
        </p>
      ))}
      <details>
        <summary>冻结身份</summary>
        <p>需求与固定材料</p>
        <code>{snapshot.input_sha256}</code>
        <p>项目内容</p>
        <code>{snapshot.project_sha256}</code>
        <p>调用证据</p>
        <code>{snapshot.source_sha256}</code>
      </details>
    </section>
  );
}

function Review({ review, title }: { review?: RecordData; title: string }) {
  if (!review)
    return (
      <div>
        <strong>{title}</strong>
        <p>未记录</p>
      </div>
    );
  return (
    <article className="quality-review">
      <h4>
        {title} · {statuses[review.status] || "已有记录"}
      </h4>
      {review.summary && <p>{review.summary}</p>}
      {review.scores && (
        <p>
          {Object.entries(review.scores)
            .map(([key, score]) => `${dimensions[key] || key}：${value(score)}`)
            .join(" · ")}
        </p>
      )}
      {rows(review.issues).map((issue, i) => (
        <div key={i}>
          <p>{issue.description || issue.detail || issue.code}</p>
          {issue.evidence && <blockquote>{issue.evidence}</blockquote>}
          {issue.suggestion && <p>建议：{issue.suggestion}</p>}
        </div>
      ))}
      <p className="muted">历史审核记录，未重新验证。</p>
    </article>
  );
}
