import { Bot, RefreshCw, ArrowRight } from "lucide-react";
import { Panel, Badge } from "./ui";
import { rows, type RecordData } from "./api";

export function ReviewAutomation({ project }: { project: RecordData }) {
  const state = project.review_automation;
  if (
    !state ||
    !Number.isInteger(state.round) ||
    !Number.isInteger(state.max_rounds)
  )
    return null;
  const names: RecordData = {
    writing: state.round > 1 ? "正在按审核意见返修" : "正在创作首稿",
    reviewing: "独立审稿人正在验收",
    repairing: "审核已打回，准备自动返修",
    passed: "独立审核已通过",
    blocked: "本批自动返修已结束，需要处理建议",
  };
  const phases: RecordData = {
    proposals: "故事提案",
    script: "剧本与分镜",
    story_resolution: "故事目标",
  };
  return (
    <div className="review-automation" role="status" aria-live="polite">
      <RefreshCw size={18} />
      <div>
        <strong>
          {phases[state.phase] || "创作"} ·{" "}
          {names[state.status] || "审核与返修"}
        </strong>
        <p>
          第 {state.round} / {state.max_rounds} 轮审核 · 已发起{" "}
          {state.automatic_repairs || 0} 次自动返修
        </p>
        <small>
          独立审核 → 按意见返修 →
          再次送审。通过后继续制作，达到上限再交给你决定。
        </small>
      </div>
    </div>
  );
}

export function ReviewAdvisor({
  project,
  busy,
  manualFeedback,
  onSubmit,
}: {
  project: RecordData;
  busy: boolean;
  manualFeedback: string;
  onSubmit: (body: RecordData) => void;
}) {
  const attention = project.operator?.attention;
  const advice = attention?.review_advice;
  const actions = rows(attention?.actions);
  const request = actions.find((a) => a.id === "request_review_advice");
  const continueAuto = actions.find((a) => a.id === "continue_auto_review");
  const apply = actions.find((a) => a.id === "provide_feedback");
  if (!advice && !request && !continueAuto) return null;
  const proposal = advice?.proposal;
  const current = advice?.current === true;
  const pending = current && ["queued", "requested"].includes(advice?.status);
  const usable = current && advice?.status === "ready" && proposal;
  const canApply =
    usable &&
    proposal.recommendation === "repair" &&
    apply?.enabled !== false &&
    apply;
  const hasDraft = !!manualFeedback.trim();
  return (
    <Panel
      className="review-advisor"
      title="总制片 AI 辅助决策"
      description="AI 分析当前审核问题，提出返修方案、理由和风险；你决定是否采纳。"
    >
      <div className="review-advisor-heading">
        <Bot size={20} />
        <Badge
          status={pending ? "running" : usable ? "passed" : "waiting"}
          label={
            pending
              ? advice.status === "queued"
                ? "等待 AI 分析"
                : "AI 正在分析"
              : usable
                ? "建议已返回"
                : "等待处理"
          }
        />
        {advice?.requested_model && <small>{advice.requested_model}</small>}
      </div>
      {pending && (
        <p role="status">
          总制片正在梳理原因与修改重点，当前审核记录和你的修改草稿会保留。
        </p>
      )}
      {continueAuto && (
        <div className="review-continue">
          <p>当前还有可用的自动返修轮次，可以从已有检查点继续处理。</p>
          <button
            type="button"
            className="button primary"
            disabled={busy || hasDraft || continueAuto.enabled === false}
            onClick={() =>
              onSubmit({
                action: continueAuto.id,
                expected_revision: project.revision,
              })
            }
          >
            <RefreshCw size={16} />
            {continueAuto.label}
          </button>
          {continueAuto.disabled_reason && (
            <small>{continueAuto.disabled_reason}</small>
          )}
          {hasDraft && (
            <small>
              已保留你自己的修改要求，请用下方表单提交，或清空后继续自动返修。
            </small>
          )}
        </div>
      )}
      {usable && (
        <>
          <h3>
            {proposal.recommendation === "repair"
              ? "建议在当前方向内返修"
              : "建议由你确认创作取舍"}
          </h3>
          <p className="preserve-lines">{proposal.rationale}</p>
          <details className="review-advisor-plan">
            <summary>查看 AI 返修方案</summary>
            <p className="preserve-lines">{proposal.feedback}</p>
          </details>
          {rowsOfText(proposal.risks).length > 0 && (
            <div>
              <h4>需要留意的风险</h4>
              <ul>
                {rowsOfText(proposal.risks).map((v, i) => (
                  <li key={i}>{v}</li>
                ))}
              </ul>
            </div>
          )}
          {rowsOfText(proposal.decisions).length > 0 && (
            <div>
              <h4>需要你决定的事项</h4>
              <ul>
                {rowsOfText(proposal.decisions).map((v, i) => (
                  <li key={i}>{v}</li>
                ))}
              </ul>
            </div>
          )}
          {canApply && (
            <>
              {hasDraft && (
                <p className="notice">
                  你已有自选修改要求。可以使用下方表单提交，或清空草稿后采纳 AI
                  方案。
                </p>
              )}
              <button
                className="button primary"
                type="button"
                disabled={busy || hasDraft}
                data-action="apply-review-advice"
                onClick={() =>
                  onSubmit({
                    action: apply.id,
                    feedback: proposal.feedback,
                    expected_revision: project.revision,
                  })
                }
              >
                采纳 AI 方案并继续返修
                <ArrowRight size={16} />
              </button>
              <small>提交上方 AI 返修方案，修改后仍由独立审稿人验收。</small>
            </>
          )}
        </>
      )}
      {advice && !current && (
        <p>这份建议对应较早的稿件，请重新分析当前审核问题。</p>
      )}
      {current && advice?.status === "manual" && (
        <p>
          {advice.reason_code === "previous_request_unresolved"
            ? "上次 AI 请求的结果尚未确认，已保留记录并停止自动重发。你可以使用下方审核选项处理。"
            : advice.reason_code === "supervisor_not_configured"
              ? "总制片模型尚未配置。可在“模型与连接”中配置，也可直接使用下方审核选项。"
              : "AI 暂未提供可用方案。当前审核问题和人工处理入口已保留。"}
        </p>
      )}
      {request && !pending && !usable && (
        <>
          <button
            className="button"
            type="button"
            disabled={busy || request.enabled === false}
            onClick={() =>
              onSubmit({
                action: request.id,
                expected_revision: project.revision,
              })
            }
          >
            <Bot size={16} />
            {request.label}
          </button>
          {request.disabled_reason && <small>{request.disabled_reason}</small>}
        </>
      )}
    </Panel>
  );
}

function rowsOfText(value: unknown): string[] {
  return Array.isArray(value)
    ? value
        .filter((item): item is string => typeof item === "string")
        .slice(0, 6)
    : [];
}
