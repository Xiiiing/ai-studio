import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Panel, Badge } from "./ui";
import { request, rows, type RecordData } from "./api";

const kinds: Record<string, string> = {
  STRUCTURAL: "结构错误",
  OBJECTIVE: "客观问题",
  CRAFT: "创作建议",
  AESTHETIC: "审美选择",
};
export function CreativeQualityPanel({ project }: { project: RecordData }) {
  const client = useQueryClient();
  const [confirmed, setConfirmed] = useState(false);
  const [reviewAuthorized, setReviewAuthorized] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const enabled =
    project.creative_quality_contract?.version ===
    "ai-studio.creative-quality.v1";
  const complete = enabled;
  const quality = useQuery({
    queryKey: ["creative-quality", project.id, project.revision],
    queryFn: () => request(`/projects/${project.id}/creative-quality`),
    initialData: project.creative_quality,
    retry: false,
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.recheck?.task?.status)
        ? 1500
        : false,
  });
  const review =
    quality.data?.review ||
    project.draft_review ||
    project.script_review?.review ||
    {};
  const recheck = quality.data?.recheck?.task;
  async function adopt() {
    setBusy(true);
    setError("");
    try {
      await request(`/projects/${project.id}/creative-quality`, {
        expected_revision: project.revision,
        request_id: crypto.randomUUID(),
        confirmed,
      });
      await client.invalidateQueries();
    } catch (err) {
      setError(err instanceof Error ? err.message : "启用失败，请刷新后核对");
    } finally {
      setBusy(false);
    }
  }
  async function reviewExisting() {
    setBusy(true);
    setError("");
    try {
      await request(`/projects/${project.id}/creative-quality/review`, {
        expected_revision: project.revision,
        request_id: crypto.randomUUID(),
        confirmed: true,
        authorize_review: reviewAuthorized,
      });
      setReviewAuthorized(false);
      await client.invalidateQueries();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "复核请求未完成，请核对已有任务",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel
      title="审核与返修依据"
      description="客观问题按证据定向返修；创作建议与审美选择保留给作者判断。"
    >
      {!complete ? (
        <div>
          <p>
            此项目沿用原审核契约。新契约只在空闲边界启用，保留历史，重新审核后才能继续制作。
          </p>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            确认采用新契约并重新审核当前稿件
          </label>
          <button disabled={!confirmed || busy} onClick={adopt}>
            启用新审核契约
          </button>
        </div>
      ) : (
        <p>
          {project.creative_quality_review_required
            ? "当前稿件需要按新契约重新审核"
            : "已采用有来源的审核契约"}
        </p>
      )}
      {enabled && (
        <div aria-label="复核现有稿件与成片">
          <p>
            复核只读取现有稿件与文件；不重写、不生成、不重制。缺少新契约元数据时会列出缺口，需在原修改入口另行处理。
          </p>
          <label>
            <input
              type="checkbox"
              checked={reviewAuthorized}
              onChange={(e) => setReviewAuthorized(e.target.checked)}
            />
            授权本次已有内网文字与视觉模型复核
          </label>
          <button
            disabled={
              !reviewAuthorized ||
              busy ||
              ["queued", "running"].includes(recheck?.status)
            }
            onClick={reviewExisting}
          >
            复核现有稿件与成片
          </button>
          {recheck && (
            <p role="status">
              复核状态：
              {(
                {
                  queued: "排队中",
                  running: "正在读取现有证据",
                  pass: "复核通过，成片仍需重新人工终审",
                  fail: "未通过，原稿与素材保留",
                  uncertain: "证据不足，原稿与素材保留",
                  needs_metadata: "需明确补齐原稿元数据",
                  unknown: "结果未知，等待核对原调用",
                  failed: "未完成，原稿与素材保留",
                } as Record<string, string>
              )[recheck.status] || recheck.status}
            </p>
          )}
          {recheck?.message && <p>{recheck.message}</p>}
          {recheck?.error && <p role="alert">{recheck.error}</p>}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {rows(review.issues).map((issue, i) => (
        <details key={`${issue.code}-${i}`}>
          <summary>
            <Badge
              status={issue.severity}
              label={kinds[issue.nature] || "历史问题（未记录分类）"}
            />{" "}
            {issue.description}
          </summary>
          <p>
            位置：
            {issue.location || rows(issue.shot_ids).join("、") || "未记录"}
          </p>
          <p>证据：{issue.evidence || "未记录"}</p>
          {issue.impact && <p>影响：{issue.impact}</p>}
          <p>修订目标：{issue.repair_target || issue.suggestion}</p>
          <p>
            {issue.requires_choice
              ? "需要作者选择"
              : issue.auto_repair
                ? "可在授权范围内定向返修"
                : "不会单独触发自动返修"}
          </p>
          {rows(issue.evidence_sources).map((source, n) => (
            <details key={n}>
              <summary>{source.location} · 当前来源</summary>
              <pre>{source.text}</pre>
            </details>
          ))}
        </details>
      ))}
      {!rows(review.issues).length && (
        <p>尚无当前问题记录；没有记录不代表已经审核通过。</p>
      )}
    </Panel>
  );
}
