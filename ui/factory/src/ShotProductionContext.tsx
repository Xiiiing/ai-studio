import { rows, type RecordData } from "./api";
import { Readable } from "./ui";

export function ShotProductionContext({
  shot,
  revision,
  stage,
}: {
  shot: RecordData;
  revision: number;
  stage: string;
}) {
  const references = rows(shot.asset_references);
  const impacts = shot.operation_impact || {};
  const impact = impacts[stage === "video" ? "edit_video" : "edit_image"];
  return (
    <section
      className="shot-production-context"
      aria-label="镜头制作依据与影响范围"
    >
      <h3>制作依据</h3>
      <p>
        当前项目版本 {revision} · 镜头版本 {shot.shot_revision || 1} ·{" "}
        {shot.seconds} 秒
      </p>
      <div className="shot-context-grid">
        <div>
          <strong>已确认参考</strong>
          {references.length ? (
            <ul>
              {references.map((ref, i) => (
                <li key={i}>
                  {ref.kind || "素材"} ·{" "}
                  {ref.name ||
                    ref.entity_id ||
                    ref.role ||
                    ref.asset_id ||
                    "参考"}
                  {ref.version != null && ` · v${ref.version}`}
                  {ref.sha256 && (
                    <small title={ref.sha256}>
                      {" "}
                      · {String(ref.sha256).slice(0, 12)}
                    </small>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              未记录已确认的素材引用，请核对本镜的制作要求。
            </p>
          )}
        </div>
        <div>
          <strong>剧情与人物状态</strong>
          <p>{shot.action || shot.dramatic_beat || "剧情目标未记录"}</p>
          {shot.continuity && <p>{shot.continuity}</p>}
          <details>
            <summary>人物状态与事件引用</summary>
            <Readable
              value={{
                character_states: shot.character_states,
                beat_ids: shot.beat_ids,
                reference_shots: shot.reference_shots,
              }}
            />
          </details>
        </div>
      </div>
      <div aria-label="局部修改影响范围" className="shot-impact">
        <strong>修改影响预览</strong>
        {!impact ? (
          <p className="muted">影响预览暂不可用，保存时仍由服务端核对依赖。</p>
        ) : (
          <>
            <p>
              {rows(impact.affected)
                .map(
                  (row) =>
                    `${row.shot_id}（${(row.stages || []).map((s: string) => (s === "image" ? "首帧" : "视频")).join("、")}）`,
                )
                .join("；")}
            </p>
            <p className="muted">
              其他 {(impact.preserved_shot_ids || []).length}{" "}
              镜复用；原候选、费用和历史保留。保存不会直接提交生成。
            </p>
            {rows(impact.unresolved_tasks).length > 0 && (
              <p className="error-text">
                受影响镜头仍有待核对云任务，核对完成前不能保存或选片。
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
