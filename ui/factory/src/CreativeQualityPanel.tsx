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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const enabled =
    project.creative_quality_contract?.version ===
    "ai-studio.creative-quality.v1";
  const review = project.draft_review || project.script_review?.review || {};
  const quality = useQuery({ queryKey: ["creative-quality", project.id, project.revision], queryFn: () => request(`/projects/${project.id}/creative-quality`), initialData: project.creative_quality, retry: false });
  const coverage = quality.data?.coverage || project.creative_quality?.coverage;
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
  return (
    <Panel
      title="审核与返修依据"
      description="客观问题按证据定向返修；创作建议与审美选择保留给作者判断。"
    >
      {!enabled ? (
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
      {coverage && (
        <div aria-label="剧情覆盖追踪">
          <h3>剧情覆盖追踪</h3>
          <p>
            引用检查：
            {coverage.status === "mapped"
              ? "映射完整"
              : coverage.status === "fail"
                ? "需要修复"
                : "尚无必要事件映射"}{" "}
            · 语义审核：
            {coverage.semantic_status === "text_pass"
              ? "当前正文审核通过"
              : coverage.semantic_status === "text_fail"
                ? "正文尚未落实必要事件"
                : "尚未建立当前版本结论"}
          </p>
          <p>
            引用完整不代表画面已经发生；成片效果仍需实际媒体证据及人工观看。
          </p>
          {rows(coverage.events).map((event) => (
            <details key={event.id}>
              <summary>
                {event.id} · {event.description}
              </summary>
              <p>
                承载镜头：
                {rows(coverage.mapping)
                  .filter((m) => m.event_ids?.includes(event.id))
                  .map((m) => m.shot_id)
                  .join("、") || "缺少承载"}
              </p>
              <p>
                原文来源：
                {rows(event.source_refs).join("、") || "批准的故事目标"}
              </p>
            </details>
          ))}
          {rows(coverage.missing).length > 0 && (
            <p role="alert">
              缺少必要事件：{rows(coverage.missing).join("、")}
            </p>
          )}
          {rows(coverage.invalid).map((value, i) => (
            <p role="alert" key={i}>
              {String(value)}
            </p>
          ))}
        </div>
      )}
      {!rows(review.issues).length && (
        <p>尚无当前问题记录；没有记录不代表已经审核通过。</p>
      )}
    </Panel>
  );
}
