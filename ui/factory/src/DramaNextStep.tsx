import { useState } from "react";
import { type RecordData, rows } from "./api";

export function DramaNextStep({
  project: p,
  tab,
  busy,
  onTab,
  repair,
  manualChanges = false,
}: {
  project: RecordData;
  tab: string;
  busy: boolean;
  onTab: (tab: string) => void;
  repair: (feedback: string) => Promise<boolean>;
  manualChanges?: boolean;
}) {
  const [feedback, setFeedback] = useState("");
  function focusPlanSubmit() {
    const submit = document.querySelector<HTMLButtonElement>(
      '.drama-plan-editor button[type="submit"]',
    );
    submit?.scrollIntoView({ block: "center" });
    submit?.focus();
  }
  if (
    ![
      "overview",
      "workflow",
      "plan",
      "episodes",
      "batches",
      "results",
    ].includes(tab) ||
    p.status === "cancelled" ||
    p.automation?.enabled
  )
    return null;
  const planning =
    !p.plan_version ||
    p.status === "awaiting_plan" ||
    String(p.stage || "").startsWith("plan_");
  const running = !!p.workflow?.active_job;
  const stopped =
    planning &&
    !running &&
    (["blocked", "failed", "paused"].includes(p.status) ||
      p.plan_phase === "exhausted");
  const awaiting = planning && !running && p.status === "awaiting_plan";
  const drafts = rows(p.episodes).filter((e) => e.status === "draft");
  const unresolved = rows(p.episodes).filter((e) =>
    ["blocked", "failed", "paused", "waiting_dependency"].includes(e.status),
  );
  const title = stopped
    ? "下一步：处理全剧规划"
    : awaiting
      ? "下一步：确认全剧规划"
      : planning
        ? "正在规划与审核"
        : unresolved.length
          ? "下一步：处理剧集阻塞"
          : drafts.length
            ? "下一步：选择剧集制作"
            : "下一步：查看剧集制作与验收";
  return (
    <section className="drama-next-step" aria-label="制作下一步">
      <h2>{title}</h2>
      <p>
        {stopped
          ? "当前停在全剧规划，视频制作尚未开始。任务已经暂停，继续等待不会自动推进。"
          : awaiting
            ? "规划审核已通过。检查并确认规划后，才能建立剧集、选择集数和制作预算。"
            : planning
              ? "设定、分集大纲和独立审核正在进行，尚未开始生成视频。"
              : unresolved.length
                ? "已有进度和素材保留。进入剧集查看具体原因，再对对应剧集继续或返修。"
                : drafts.length
                  ? "全剧规划已确认。选择要制作的剧集并确认预算后，系统开始生成。"
                  : "进入剧集查看当前步骤、需要你确认的事项及已完成的视频。"}
      </p>
      {stopped && (
        <p className="muted">
          已完成 {p.plan_attempt || 0} / {p.plan_max_attempts || 3} 轮规划检查；
          {p.plan_review?.issues?.length || 0}{" "}
          项审核问题。失败说明、原有草稿与审核记录继续保留。
        </p>
      )}
      {stopped && tab === "plan" ? (
        <form
          className="drama-form"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!busy && !manualChanges && (await repair(feedback.trim())))
              setFeedback("");
          }}
        >
          <label>
            <span>补充本轮规划要求（可选）</span>
            <textarea
              rows={2}
              maxLength={4000}
              value={feedback}
              disabled={busy}
              onChange={(event) => setFeedback(event.target.value)}
              placeholder="说明必须保留的人物身份、这两集各自要发生的事，或针对下方审核问题补充要求。"
            />
          </label>
          <div className="drama-actions">
            <button
              className="button primary"
              type="submit"
              disabled={busy || manualChanges}
            >
              {busy ? "正在提交…" : "修复规划并继续"}
            </button>
            <span className="muted">
              带上现有问题进行有界返修，通过后仍需你确认规划。
            </span>
          </div>
          {manualChanges && (
            <p role="status">
              下方有未提交的规划修改。请先提交修改并重新审核，避免自动返修覆盖你的编辑；本轮补充要求继续保留。
            </p>
          )}
          {manualChanges && (
            <button type="button" className="button" onClick={focusPlanSubmit}>
              前往提交规划修改
            </button>
          )}
        </form>
      ) : (
        <div className="drama-actions">
          {stopped || awaiting ? (
            <button
              className="button primary"
              onClick={() =>
                awaiting && tab === "plan" ? focusPlanSubmit() : onTab("plan")
              }
            >
              {stopped
                ? "查看问题并修复规划"
                : tab === "plan"
                  ? "前往确认规划"
                  : "检查并确认规划"}
            </button>
          ) : planning ? (
            tab !== "workflow" && (
              <button className="button" onClick={() => onTab("workflow")}>
                查看执行进度
              </button>
            )
          ) : (
            tab !== "episodes" && (
              <button
                className="button primary"
                onClick={() => onTab("episodes")}
              >
                {drafts.length && !unresolved.length
                  ? "选择剧集并设置预算"
                  : "查看剧集与处理步骤"}
              </button>
            )
          )}
        </div>
      )}
    </section>
  );
}
