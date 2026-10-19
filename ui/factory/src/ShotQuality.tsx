import { SelectionGroup } from "./SelectionGroup";
import { useState } from "react";
import { rows, type RecordData } from "./api";
import { Badge, Empty, Panel, Readable } from "./ui";

function assessment(shot: RecordData) {
  return shot.qa || shot.quality || shot.review || {};
}
function needsAttention(shot: RecordData) {
  const qa = assessment(shot);
  return [
    shot.status,
    qa.status,
    qa.technical?.status,
    qa.semantic?.status,
  ].some((status) =>
    [
      "failed",
      "fail",
      "blocked",
      "error",
      "rejected",
      "needs_attention",
    ].includes(status),
  );
}
export function ShotQuality({
  shots,
  onShot,
}: {
  shots: RecordData[];
  onShot: (id: string) => void;
}) {
  const [filter, setFilter] = useState("all"),
    [page, setPage] = useState(0);
  const filtered =
    filter === "attention" ? shots.filter(needsAttention) : shots;
  const pages = Math.max(1, Math.ceil(filtered.length / 6));
  const current = Math.min(page, pages - 1);
  return (
    <Panel
      title="镜头质检与返修"
      description="先查看审核结论与提示，技术数据和每次尝试可按需展开。"
      className="quality-panel"
    >
      <div className="quality-toolbar">
        <p>
          {shots.length} 镜记录 · {shots.filter(needsAttention).length} 镜需关注
        </p>
        <SelectionGroup
          className="segmented"
          aria-label="质检筛选"
          value={filter}
        >
          <button
            aria-pressed={filter === "all"}
            className={filter === "all" ? "selected" : ""}
            onClick={() => {
              setFilter("all");
              setPage(0);
            }}
          >
            全部镜头
          </button>
          <button
            aria-pressed={filter === "attention"}
            className={filter === "attention" ? "selected" : ""}
            onClick={() => {
              setFilter("attention");
              setPage(0);
            }}
          >
            需关注
          </button>
        </SelectionGroup>
      </div>
      <div className="quality-cards">
        {filtered.slice(current * 6, current * 6 + 6).map((shot) => {
          const qa = assessment(shot),
            attempts = rows(shot.attempts);
          const notes = [
            ...rows(qa.issues),
            ...rows(qa.semantic?.issues),
            ...rows(qa.technical?.issues),
          ];
          const summary = qa.semantic?.summary || qa.summary;
          return (
            <article
              className="quality-shot"
              key={shot.id}
              data-quality-shot={shot.id}
            >
              <header>
                <button
                  className="text-link"
                  onClick={() => onShot(shot.id)}
                  aria-label={"打开镜头 " + shot.id}
                >
                  {shot.id}
                  <span>查看镜头</span>
                </button>
                <Badge status={shot.status} />
              </header>
              <div className="quality-results">
                {qa.technical?.status && (
                  <span>
                    技术检查 <Badge status={qa.technical.status} />
                  </span>
                )}
                {qa.semantic?.status && (
                  <span>
                    画面审核 <Badge status={qa.semantic.status} />
                  </span>
                )}
                <small>
                  {attempts.length || shot.attempt_count || 0} 次素材尝试
                </small>
              </div>
              {summary && <p className="quality-summary">{summary}</p>}
              {!!notes.length && (
                <details className="quality-notes">
                  <summary>{notes.length} 条审核提示</summary>
                  <ul>
                    {notes.map((note, i) => (
                      <li key={i}>
                        {typeof note === "string"
                          ? note
                          : note.detail ||
                            note.message ||
                            note.summary ||
                            note.code ||
                            "查看完整审核记录"}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <details className="quality-evidence">
                <summary>完整质检记录</summary>
                <div
                  className="evidence-content"
                  tabIndex={0}
                  aria-label={shot.id + " 完整质检记录"}
                >
                  <Readable value={Object.keys(qa).length ? qa : null} />
                </div>
              </details>
              {!!attempts.length && (
                <details className="quality-evidence">
                  <summary>素材尝试记录（{attempts.length}）</summary>
                  <div
                    className="evidence-content"
                    tabIndex={0}
                    aria-label={shot.id + " 素材尝试记录"}
                  >
                    <Readable value={attempts} />
                  </div>
                </details>
              )}
            </article>
          );
        })}
      </div>
      {!filtered.length && (
        <Empty
          title={
            filter === "attention"
              ? "当前没有审核未通过的镜头"
              : "尚无镜头质检记录"
          }
        />
      )}
      {pages > 1 && (
        <nav className="pagination" aria-label="质检镜头分页">
          <span>
            第 {current + 1} / {pages} 页 · 共 {filtered.length} 镜
          </span>
          <div className="button-row">
            <button
              className="button small"
              disabled={current === 0}
              onClick={() => setPage(current - 1)}
            >
              上一页镜头
            </button>
            <button
              className="button small"
              disabled={current + 1 >= pages}
              onClick={() => setPage(current + 1)}
            >
              下一页镜头
            </button>
          </div>
        </nav>
      )}
    </Panel>
  );
}
