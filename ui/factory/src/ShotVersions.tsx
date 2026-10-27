import { useState } from "react";
import { Image } from "lucide-react";
import { text, ownedMedia, money, type RecordData } from "./api";
import { Badge, Empty, ErrorNotice, Readable } from "./ui";
import "./shot-workspace.css";

function candidateId(candidate: RecordData) {
  return candidate.id || candidate.stage + ":" + candidate.take;
}
function origin(candidate: RecordData) {
  return candidate.producer_selected
    ? "出品人已选定"
    : candidate.plan_current
      ? "当前参数候选"
      : "历史参数候选";
}
function reviewSummary(candidate: RecordData) {
  const qa = candidate.qa || {},
    summary = qa.semantic?.summary || qa.summary;
  if (typeof summary === "string" && summary.trim()) return summary;
  if (candidate.error) return text(candidate.error).slice(0, 220);
  if (qa.eligible_for_selection === true) return "审核通过，可确认采用此版本。";
  if (
    [
      qa.status,
      qa.semantic?.status,
      qa.technical?.status,
      candidate.status,
    ].some((status) =>
      ["failed", "fail", "blocked", "rejected", "error"].includes(status),
    )
  )
    return "此版本未通过审核，查看完整记录后处理。";
  if (
    [candidate.status, qa.semantic?.status].some((status) =>
      ["semantic_pending", "reviewing", "not_run", "pending"].includes(status),
    )
  )
    return "画面审核尚未完成，等待审核结论。";
  if (qa.eligible_for_selection === false)
    return "此版本尚未满足选片条件，请查看完整记录。";
  if (Object.keys(qa).length) return "已收到审核记录，完整结论见下方。";
  return candidate.media_url
    ? "尚无审核结论，素材可预览。"
    : "尚无可预览素材，等待制作回执。";
}

export function ShotVersions({
  projectId,
  candidates,
  revision,
  disabled,
  onSubmit,
  truncated,
  operationImpact,
}: {
  projectId: string;
  candidates: RecordData[];
  revision: number;
  disabled: boolean;
  onSubmit: (body: RecordData) => void;
  truncated?: boolean;
  operationImpact?: RecordData;
}) {
  const [previewId, setPreviewId] = useState("");
  const [compared, setCompared] = useState<string[]>([]);
  const [comparing, setComparing] = useState(false);
  // The API orders each stage newest first. Stable keys preserve video playback
  // and explicit candidate consent while the production board is polled.
  const ordered = [...candidates].sort(
    (a, b) => Number(b.stage === "video") - Number(a.stage === "video"),
  );
  const defaults = ["video", "image"].flatMap((stage) => {
    const group = ordered.filter((candidate) => candidate.stage === stage);
    const current =
      group.find((candidate) => candidate.producer_selected) ||
      group.find(
        (candidate) =>
          candidate.plan_current && ownedMedia(projectId, candidate.media_url),
      ) ||
      group.find((candidate) => ownedMedia(projectId, candidate.media_url)) ||
      group[0];
    return current ? [current] : [];
  });
  const preview = ordered.find(
    (candidate) => candidateId(candidate) === previewId,
  );
  const activeCompared = compared.filter((id) =>
    ordered.some((candidate) => candidateId(candidate) === id),
  );
  const showComparison = comparing && activeCompared.length === 2;
  const displayed = showComparison
    ? activeCompared.map((id) =>
        ordered.find((candidate) => candidateId(candidate) === id)!,
      )
    : defaults.map((candidate) =>
        preview && preview.stage === candidate.stage ? preview : candidate,
      );
  const currentUpdates = ["video", "image"].flatMap((stage) => {
    const latest = ordered.find(
      (item) => item.stage === stage && item.plan_current,
    );
    return latest &&
      !displayed.some((item) => candidateId(item) === candidateId(latest))
      ? [latest]
      : [];
  });
  if (!ordered.length)
    return (
      <Empty title="此镜头尚无候选">
        素材生成后可比较画面、视频与审核记录。
      </Empty>
    );
  return (
    <section className="shot-versions" aria-label="镜头素材与版本">
      <header className="shot-preview-heading">
        <h3>{showComparison ? "版本对比" : "当前镜头素材"}</h3>
        {(previewId || showComparison) && (
          <button
            className="button"
            onClick={() => {
              setPreviewId("");
              setComparing(false);
            }}
          >
            返回当前素材
          </button>
        )}
      </header>
      <div
        className={
          "candidate-grid shot-preview-grid" +
          (showComparison ? " is-comparing" : "")
        }
        data-shot-preview={showComparison ? "compare" : "current"}
      >
        {displayed.map((candidate) => (
          <Candidate
            key={candidateId(candidate)}
            candidate={candidate}
            projectId={projectId}
            revision={revision}
            disabled={disabled || !candidate.selectable}
            onSubmit={onSubmit}
            impact={operationImpact?.["select_" + candidate.stage]}
          />
        ))}
      </div>
      {!showComparison &&
        displayed.some((candidate) => !candidate.plan_current) && (
          <p className="shot-history-notice">
            预览中包含历史参数素材，当前参数的制作与审核状态请在版本记录中核对。
          </p>
        )}
      {!showComparison && currentUpdates.length > 0 && (
        <section className="candidate-review" aria-label="当前参数审核">
          <h3>当前参数审核</h3>
          {currentUpdates.map((candidate) => (
            <div key={candidateId(candidate)}>
              <strong>
                {candidate.stage === "video" ? "视频" : "首帧"} ·{" "}
                {candidate.take}
              </strong>{" "}
              <Badge status={candidate.status} />
              <p>{reviewSummary(candidate)}</p>
            </div>
          ))}
        </section>
      )}
      <section className="shot-version-history" aria-label="候选版本记录">
        <header className="shot-preview-heading">
          <h3>
            版本记录 <span>{ordered.length} 个版本</span>
          </h3>
          <button
            className="button"
            disabled={activeCompared.length !== 2}
            onClick={() => setComparing(true)}
          >
            对比所选版本
            {activeCompared.length ? `（${activeCompared.length}/2）` : ""}
          </button>
        </header>
        <p className="muted">
          勾选两个版本对比，或打开单个版本查看审核与选片操作。
        </p>
        <ul className="shot-version-list">
          {ordered.map((candidate) => {
            const id = candidateId(candidate),
              checked = activeCompared.includes(id);
            return (
              <li key={id} data-shot-version={id}>
                <label className="shot-compare-choice">
                  <input
                    type="checkbox"
                    aria-label={`对比 ${candidate.stage === "video" ? "视频" : "首帧"} ${candidate.take}`}
                    checked={checked}
                    disabled={!checked && activeCompared.length >= 2}
                    onChange={(event) =>
                      setCompared(
                        event.target.checked
                          ? [...activeCompared, id]
                          : activeCompared.filter((value) => value !== id),
                      )
                    }
                  />
                </label>
                <button
                  className="shot-version-open"
                  aria-label={`查看 ${candidate.stage === "video" ? "视频" : "首帧"} ${candidate.take}`}
                  aria-pressed={displayed.some(
                    (item) => candidateId(item) === id,
                  )}
                  onClick={() => {
                    setPreviewId(id);
                    setComparing(false);
                  }}
                >
                  <strong>
                    {candidate.stage === "video" ? "视频" : "首帧"} ·{" "}
                    {candidate.take}
                  </strong>
                  <small>{origin(candidate)}</small>
                  <span>
                    {candidate.model ||
                      candidate.receipt?.model ||
                      "模型未记录"}
                  </span>
                </button>
                <Badge status={candidate.status} />
              </li>
            );
          })}
        </ul>
        {truncated && (
          <p className="muted">仅显示接口返回的最近候选，历史素材继续保留。</p>
        )}
      </section>
    </section>
  );
}

function Candidate({
  candidate: c,
  revision,
  disabled: externallyDisabled,
  onSubmit,
  projectId,
  impact,
}: {
  projectId: string;
  candidate: RecordData;
  revision: number;
  disabled: boolean;
  onSubmit: (body: RecordData) => void;
  impact?: RecordData;
}) {
  const [confirmed, setConfirmed] = useState(false),
    [base, setBase] = useState(revision);
  const disabled =
    externallyDisabled ||
    !!impact?.unresolved_tasks?.length ||
    (impact != null && impact.source_revision !== revision);
  const stale = confirmed && base !== revision,
    url = ownedMedia(projectId, c.media_url);
  return (
    <article
      className="candidate-card"
      data-candidate-id={candidateId(c)}
      aria-label={`${c.stage === "video" ? "视频" : "首帧"} ${c.take} 预览`}
    >
      <header className="row-between">
        <strong>
          {c.take} · {c.stage === "video" ? "视频" : "画面"}
        </strong>
        <Badge status={c.status} />
      </header>
      {url ? (
        c.stage === "video" ? (
          <video controls playsInline preload="metadata" src={url} />
        ) : (
          <img src={url} alt={"候选 " + c.take} loading="lazy" />
        )
      ) : (
        <div className="media-empty">
          <Image size={24} />
          尚无可预览素材
        </div>
      )}
      <div className="candidate-body">
        <p>{origin(c)}</p>
        <section className="candidate-review" aria-label="审核结论">
          <strong>审核结论</strong>
          <p>{reviewSummary(c)}</p>
          <div className="candidate-review-status">
            {c.qa?.technical?.status && (
              <span>
                技术 <Badge status={c.qa.technical.status} />
              </span>
            )}
            {c.qa?.semantic?.status && (
              <span>
                画面 <Badge status={c.qa.semantic.status} />
              </span>
            )}
          </div>
        </section>
        <small>模型：{c.model || c.receipt?.model || "未记录"}</small>
        {c.source_signature && (
          <small title={c.source_signature}>
            来源签名：{String(c.source_signature).slice(0, 16)}
          </small>
        )}
        <details>
          <summary>参考素材与来源</summary>
          <Readable
            value={{
              references: c.asset_references || [],
              shot_revision: c.receipt?.shot_revision,
              source_signature: c.source_signature,
            }}
          />
        </details>
        {c.cost && (
          <small>
            估算 {money(c.cost.estimated_cost_micros, c.cost.currency)} · 确认{" "}
            {money(c.cost.confirmed_cost_micros, c.cost.currency)}
          </small>
        )}
        <details>
          <summary>评分、参数与失败原因</summary>
          <Readable value={c.qa} />
          {c.error && <ErrorNotice error={c.error} />}
          <Readable value={c.parameters} />
          <p>{c.prompt}</p>
        </details>
        <form
          data-shot-operation="select"
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit({
              action: "select",
              stage: c.stage,
              take: c.take,
              confirmed: true,
              expected_revision: base,
            });
          }}
        >
          {impact && (
            <p className="form-help">
              采用此候选影响：
              {(impact.affected || [])
                .map(
                  (row: RecordData) =>
                    `${row.shot_id}（${(row.stages || []).map((s: string) => (s === "image" ? "首帧" : "视频")).join("、")}）`,
                )
                .join("；")}
              。原素材保留。
            </p>
          )}
          <label className="check">
            <input
              type="checkbox"
              name="confirmed"
              required
              disabled={disabled}
              checked={confirmed}
              onChange={(e) => {
                setBase(revision);
                setConfirmed(e.target.checked);
              }}
            />
            确认采用此候选，保留原素材
          </label>
          {stale && (
            <p className="error-text">候选状态已更新，请重新勾选确认。</p>
          )}
          <button
            className="button full"
            disabled={disabled || stale || !confirmed}
          >
            设为选定素材
          </button>
        </form>
      </div>
    </article>
  );
}
