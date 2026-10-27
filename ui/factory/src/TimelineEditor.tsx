import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, type RecordData } from "./api";
import "./timeline-editor.css";

type Clip = {
  id: string;
  source_id: string;
  in_frame: number;
  out_frame: number;
  source_gain: number;
  transition: { kind: string; frames: number };
};
type Track = {
  id: string;
  source_id: string;
  start_frame: number;
  in_frame: number;
  out_frame: number;
  gain: number;
  anchor_clip_id: string;
};
type Cue = {
  id: string;
  start_frame: number;
  end_frame: number;
  text: string;
  source_text: string;
  anchor_clip_id: string;
};
export type EditDocument = {
  schema: string;
  fps: number;
  resolution: number[];
  clips: Clip[];
  audio: Track[];
  subtitles: Cue[];
  confirmed_duration_frames: number;
  caption_text_confirmed: boolean;
};
export function editPositions(clips: Clip[]) {
  let cursor = 0;
  return clips.map((c, i) => {
    const start = cursor - (i ? c.transition.frames : 0);
    cursor = start + c.out_frame - c.in_frame;
    return { id: c.id, start, end: cursor };
  });
}
export function moveEditClip(
  document: EditDocument,
  index: number,
  direction: number,
): EditDocument {
  const next = structuredClone(document);
  const target = index + direction;
  if (target < 0 || target >= next.clips.length) return next;
  const old = new Map(editPositions(next.clips).map((p) => [p.id, p.start]));
  [next.clips[index], next.clips[target]] = [
    next.clips[target],
    next.clips[index],
  ];
  next.clips[0].transition = { kind: "cut", frames: 0 };
  const now = new Map(editPositions(next.clips).map((p) => [p.id, p.start]));
  for (const t of next.audio)
    if (t.anchor_clip_id)
      t.start_frame +=
        (now.get(t.anchor_clip_id) ?? 0) - (old.get(t.anchor_clip_id) ?? 0);
  for (const c of next.subtitles)
    if (c.anchor_clip_id) {
      const delta =
        (now.get(c.anchor_clip_id) ?? 0) - (old.get(c.anchor_clip_id) ?? 0);
      c.start_frame += delta;
      c.end_frame += delta;
    }
  next.subtitles.sort((a, b) => a.start_frame - b.start_frame);
  return next;
}
export function splitEditClip(
  document: EditDocument,
  index: number,
  sourceFrame: number,
): EditDocument {
  const next = structuredClone(document),
    clip = next.clips[index];
  if (sourceFrame <= clip.in_frame || sourceFrame >= clip.out_frame)
    throw new Error("拆分点须位于源入点和出点之间");
  const positions = editPositions(next.clips),
    cut = positions[index].start + sourceFrame - clip.in_frame;
  const second = {
    ...structuredClone(clip),
    id: "c" + crypto.randomUUID().replaceAll("-", ""),
    in_frame: sourceFrame,
    transition: { kind: "cut", frames: 0 },
  };
  clip.out_frame = sourceFrame;
  next.clips.splice(index + 1, 0, second);
  for (const track of next.audio)
    if (track.anchor_clip_id === clip.id && track.start_frame >= cut)
      track.anchor_clip_id = second.id;
  for (const cue of next.subtitles)
    if (cue.anchor_clip_id === clip.id && cue.start_frame >= cut)
      cue.anchor_clip_id = second.id;
  return next;
}
export function deleteEditClip(
  document: EditDocument,
  index: number,
): EditDocument {
  const next = structuredClone(document);
  if (next.clips.length === 1) throw new Error("至少保留一个镜头片段");
  const old = new Map(editPositions(next.clips).map((p) => [p.id, p.start]));
  const [removed] = next.clips.splice(index, 1);
  next.clips[0].transition = { kind: "cut", frames: 0 };
  const now = new Map(editPositions(next.clips).map((p) => [p.id, p.start]));
  next.audio = next.audio.filter((t) => t.anchor_clip_id !== removed.id);
  next.subtitles = next.subtitles.filter(
    (c) => c.anchor_clip_id !== removed.id,
  );
  for (const track of next.audio)
    if (track.anchor_clip_id)
      track.start_frame +=
        (now.get(track.anchor_clip_id) ?? 0) -
        (old.get(track.anchor_clip_id) ?? 0);
  for (const cue of next.subtitles)
    if (cue.anchor_clip_id) {
      const delta =
        (now.get(cue.anchor_clip_id) ?? 0) - (old.get(cue.anchor_clip_id) ?? 0);
      cue.start_frame += delta;
      cue.end_frame += delta;
    }
  next.subtitles.sort((a, b) => a.start_frame - b.start_frame);
  return next;
}
function FrameField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <label>
      {label}
      <input
        type="number"
        step="1"
        min="0"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}
export function TimelineEditor({ projectId }: { projectId: string }) {
  const query = useQuery({
    queryKey: ["edits", projectId],
    queryFn: () => request<RecordData>(`/projects/${projectId}/edits`),
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.task?.status)
        ? 1500
        : false,
  });
  const [document, setDocument] = useState<EditDocument | null>(null),
    [version, setVersion] = useState(""),
    [label, setLabel] = useState("剪辑版本"),
    [draftRevision, setDraftRevision] = useState(0);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [dirty, setDirty] = useState(false),
    [watched, setWatched] = useState(false),
    [reviewAuthorized, setReviewAuthorized] = useState(false);
  const data = query.data,
    versions = data?.versions ?? [],
    selected = versions.find((v: RecordData) => v.id === version);
  const active = ["queued", "running"].includes(data?.task?.status),
    sources = data?.catalog?.sources ?? {};
  useEffect(() => {
    setDocument(null);
    setVersion("");
    setDirty(false);
    setError("");
    setWatched(false);
  }, [projectId]);
  useEffect(() => {
    if (data && !document && data.catalog?.default) {
      setDocument(structuredClone(data.catalog.default));
      setDraftRevision(data.revision);
    }
  }, [data, document]);
  const change = (fn: (draft: EditDocument) => void) => {
    if (!document) return;
    const next = structuredClone(document);
    fn(next);
    setDocument(next);
    setDirty(true);
    setWatched(false);
  };
  const replace = (fn: () => EditDocument) => {
    try {
      setDocument(fn());
      setDirty(true);
      setWatched(false);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  async function action(name: string, extra: RecordData = {}) {
    if (!data) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<RecordData>(`/projects/${projectId}/edits`, {
        action: name,
        expected_revision: name === "save" ? draftRevision : data.revision,
        request_id: crypto.randomUUID(),
        ...extra,
      });
      await query.refetch();
      if (name === "save") {
        const newest = result.versions.at(-1);
        setVersion(newest.id);
        setDocument(structuredClone(newest.document));
        setDraftRevision(result.revision);
        setDirty(false);
        setWatched(false);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function load(id: string) {
    const next = versions.find((v: RecordData) => v.id === id);
    setVersion(id);
    setDocument(structuredClone(next?.document ?? data?.catalog?.default));
    setDraftRevision(data?.revision ?? 0);
    setDirty(false);
    setWatched(false);
    setError("");
  }
  if (query.isPending) return <p>正在读取剪辑素材…</p>;
  if (query.error)
    return (
      <p role="alert">
        {query.error.message}
        <button onClick={() => void query.refetch()}>重试</button>
      </p>
    );
  if (!document)
    return (
      <p role="status">{data?.error ?? "请先完成原始成片，再打开时间线。"}</p>
    );
  const positions = editPositions(document.clips),
    total = positions.at(-1)?.end ?? 0;
  const saved = Boolean(selected && !dirty),
    disabled = busy || active;
  return (
    <section className="timeline-editor" aria-label="时间线剪辑">
      <div className="timeline-intro">
        <h2>时间线剪辑</h2>
        <p>
          以24fps剪辑当前单集。原稿和素材保留；字幕改字不会重新配音。新剪辑须重新检查并观看验收。
        </p>
      </div>
      <div className="timeline-tools">
        <label>
          历史版本
          <select
            aria-label="历史剪辑版本"
            value={version}
            disabled={disabled}
            onChange={(e) => load(e.target.value)}
          >
            <option value="">从原始成片开始</option>
            {versions.map((v: RecordData) => (
              <option key={v.id} value={v.id}>
                {v.label} · {v.id.slice(0, 6)}
                {v.id === data?.adopted_version_id ? " · 已采用" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          新版本名称
          <input
            value={label}
            maxLength={80}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <button
          disabled={disabled || !data?.editable}
          onClick={() =>
            void action("save", {
              document,
              label,
              parent_id: version || undefined,
            })
          }
        >
          保存为新版本
        </button>
        {dirty && <span role="status">草稿未保存</span>}
        {draftRevision !== data?.revision && dirty && (
          <div role="alert">
            项目已更新，草稿仍保留。
            <button
              onClick={() => {
                setDraftRevision(data?.revision ?? 0);
                setError("已采用最新项目版本，请核对源素材与草稿后保存。");
              }}
            >
              核对后使用当前版本
            </button>
          </div>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      <div className="timeline-overview" aria-label="视频轨道">
        {positions.map((p, i) => (
          <a
            key={p.id}
            href={`#timeline-clip-${p.id}`}
            style={{ flex: Math.max(1, p.end - p.start) }}
          >
            {i + 1}
            <small>{((p.end - p.start) / 24).toFixed(2)}s</small>
          </a>
        ))}
      </div>
      <p>
        当前片长{" "}
        <strong>
          {total} 帧 / {(total / 24).toFixed(3)}秒
        </strong>
        ，已确认 {document.confirmed_duration_frames} 帧。
        <button
          disabled={disabled}
          onClick={() =>
            change((d) => {
              d.confirmed_duration_frames = total;
            })
          }
        >
          确认新片长作为交付规格
        </button>
      </p>
      <fieldset disabled={disabled}>
        <legend>视频与转场</legend>
        <div className="timeline-clip-list">
          {document.clips.map((clip, index) => (
            <article
              className="timeline-clip"
              id={`timeline-clip-${clip.id}`}
              key={clip.id}
            >
              <h3>
                片段 {index + 1} ·{" "}
                {sources[clip.source_id]?.global_shot_id ?? clip.source_id}
              </h3>
              <video
                controls
                preload="metadata"
                src={sources[clip.source_id]?.media_url}
                aria-label={`源镜头${index + 1}`}
              />
              <div className="timeline-fields">
                <FrameField
                  label="源入点（帧）"
                  value={clip.in_frame}
                  onChange={(v) =>
                    change((d) => {
                      d.clips[index].in_frame = v;
                    })
                  }
                />
                <FrameField
                  label="源出点（帧）"
                  value={clip.out_frame}
                  onChange={(v) =>
                    change((d) => {
                      d.clips[index].out_frame = v;
                    })
                  }
                />
                <label>
                  原声音量
                  <input
                    type="number"
                    min="0"
                    max="2"
                    step="0.05"
                    value={clip.source_gain}
                    onChange={(e) =>
                      change((d) => {
                        d.clips[index].source_gain = Number(e.target.value);
                      })
                    }
                  />
                </label>
                <label>
                  进入转场
                  <select
                    aria-label="进入转场"
                    value={clip.transition.kind}
                    disabled={index === 0}
                    onChange={(e) =>
                      change((d) => {
                        const kind = e.target.value;
                        d.clips[index].transition = {
                          kind,
                          frames:
                            kind === "cut"
                              ? 0
                              : Math.min(
                                  12,
                                  clip.out_frame - clip.in_frame - 1,
                                ),
                        };
                      })
                    }
                  >
                    <option value="cut">硬切</option>
                    <option value="dissolve">叠化</option>
                    <option value="fadeblack">淡入淡出</option>
                  </select>
                </label>
                <FrameField
                  label="转场帧数"
                  value={clip.transition.frames}
                  onChange={(v) =>
                    change((d) => {
                      d.clips[index].transition.frames = v;
                    })
                  }
                />
              </div>
              <div className="timeline-tools">
                <button
                  disabled={index === 0}
                  onClick={() =>
                    replace(() => moveEditClip(document, index, -1))
                  }
                >
                  向前移动
                </button>
                <button
                  disabled={index === document.clips.length - 1}
                  onClick={() =>
                    replace(() => moveEditClip(document, index, 1))
                  }
                >
                  向后移动
                </button>
                <button
                  disabled={clip.out_frame - clip.in_frame < 2}
                  onClick={() =>
                    replace(() =>
                      splitEditClip(
                        document,
                        index,
                        Math.floor((clip.in_frame + clip.out_frame) / 2),
                      ),
                    )
                  }
                >
                  从中点拆分
                </button>
                <button
                  onClick={() => replace(() => deleteEditClip(document, index))}
                >
                  删除片段及关联配音字幕
                </button>
              </div>
            </article>
          ))}
        </div>
      </fieldset>
      <fieldset disabled={disabled}>
        <legend>配音与背景音乐</legend>
        <p>
          关联镜头的配音随重排移动；音轨不能越过成片结尾，避免截断对白。原声和外部配音分别调整。
        </p>
        {document.audio.map((track, index) => (
          <div className="timeline-audio" key={track.id}>
            <strong>
              {sources[track.source_id]?.kind === "music" ? "背景音乐" : "配音"}{" "}
              · {track.source_id}
            </strong>
            <audio
              controls
              preload="none"
              src={sources[track.source_id]?.media_url}
            />
            <div className="timeline-fields">
              <FrameField
                label="成片起点（帧）"
                value={track.start_frame}
                onChange={(v) =>
                  change((d) => {
                    d.audio[index].start_frame = v;
                  })
                }
              />
              <FrameField
                label="音频入点（帧）"
                value={track.in_frame}
                onChange={(v) =>
                  change((d) => {
                    d.audio[index].in_frame = v;
                  })
                }
              />
              <FrameField
                label="音频出点（帧）"
                value={track.out_frame}
                onChange={(v) =>
                  change((d) => {
                    d.audio[index].out_frame = v;
                  })
                }
              />
              <label>
                音量
                <input
                  type="number"
                  min="0"
                  max="2"
                  step="0.05"
                  value={track.gain}
                  onChange={(e) =>
                    change((d) => {
                      d.audio[index].gain = Number(e.target.value);
                    })
                  }
                />
              </label>
              <button
                onClick={() =>
                  change((d) => {
                    d.audio.splice(index, 1);
                  })
                }
              >
                移除此音轨
              </button>
            </div>
          </div>
        ))}
        <label>
          添加已有背景音乐
          <select
            aria-label="添加已有背景音乐"
            value=""
            onChange={(e) => {
              const id = e.target.value;
              if (id)
                change((d) => {
                  d.audio.push({
                    id: "a" + crypto.randomUUID().replaceAll("-", ""),
                    source_id: id,
                    start_frame: 0,
                    in_frame: 0,
                    out_frame: Math.min(sources[id].frames, total),
                    gain: 0.35,
                    anchor_clip_id: "",
                  });
                });
            }}
          >
            <option value="">选择当前项目的音乐素材</option>
            {Object.values(sources)
              .filter((s: any) => s.kind === "music")
              .map((s: any) => (
                <option key={s.id} value={s.id}>
                  {s.id}
                </option>
              ))}
          </select>
        </label>
      </fieldset>
      <fieldset disabled={disabled}>
        <legend>字幕</legend>
        <p>字幕时间不得重叠；编辑文本只影响字幕。</p>
        {document.subtitles.map((cue, index) => (
          <div className="timeline-caption" key={cue.id}>
            <label>
              字幕 {index + 1}
              <textarea
                aria-label={`字幕 ${index + 1}`}
                value={cue.text}
                maxLength={1000}
                onChange={(e) =>
                  change((d) => {
                    d.subtitles[index].text = e.target.value;
                  })
                }
              />
            </label>
            <div className="timeline-fields">
              <FrameField
                label="开始（帧）"
                value={cue.start_frame}
                onChange={(v) =>
                  change((d) => {
                    d.subtitles[index].start_frame = v;
                  })
                }
              />
              <FrameField
                label="结束（帧）"
                value={cue.end_frame}
                onChange={(v) =>
                  change((d) => {
                    d.subtitles[index].end_frame = v;
                  })
                }
              />
              <button
                onClick={() =>
                  change((d) => {
                    d.subtitles.splice(index, 1);
                  })
                }
              >
                删除字幕
              </button>
            </div>
          </div>
        ))}
        <button
          onClick={() =>
            change((d) => {
              d.subtitles.push({
                id: "s" + crypto.randomUUID().replaceAll("-", ""),
                text: "新字幕",
                source_text: "",
                start_frame: d.subtitles.at(-1)?.end_frame ?? 0,
                end_frame: Math.min(
                  total,
                  (d.subtitles.at(-1)?.end_frame ?? 0) + 24,
                ),
                anchor_clip_id: "",
              });
            })
          }
        >
          添加字幕
        </button>
        <label className="timeline-check">
          <input
            type="checkbox"
            checked={document.caption_text_confirmed}
            onChange={(e) =>
              change((d) => {
                d.caption_text_confirmed = e.target.checked;
              })
            }
          />
          已核对字幕文字；知道改字不会重新合成配音
        </label>
      </fieldset>
      <div className="timeline-tools">
        <button
          disabled={disabled || !saved}
          onClick={() => void action("preview", { version_id: version })}
        >
          生成草稿预览
        </button>
        <label className="timeline-check">
          <input
            type="checkbox"
            checked={reviewAuthorized}
            onChange={(e) => setReviewAuthorized(e.target.checked)}
          />
          授权使用本项目已配置的内网视觉模型检查当前剪辑
        </label>
        <button
          disabled={disabled || !saved}
          onClick={() =>
            void action("check", {
              version_id: version,
              authorize_review: reviewAuthorized,
            })
          }
        >
          检查当前剪辑
        </button>
        <button
          disabled={
            disabled ||
            !saved ||
            selected?.semantic?.status !== "pass" ||
            selected?.review_current !== true ||
            selected?.structural?.status !== "pass"
          }
          onClick={() => void action("export", { version_id: version })}
        >
          正式导出
        </button>
      </div>
      {data?.task && (
        <div role="status">
          剪辑任务：
          {(
            {
              queued: "排队中",
              running: "执行中",
              completed: "已完成",
              failed: "失败",
              cancelled: "已取消",
            } as Record<string, string>
          )[data.task.status] ?? data.task.status}
          {data.task.error && <p>{data.task.error}</p>}
          {active && (
            <button
              disabled={busy}
              onClick={() =>
                void action("cancel", { job_id: data.task.job_id })
              }
            >
              取消剪辑任务
            </button>
          )}
        </div>
      )}
      {selected && (
        <div className="timeline-review">
          <p>
            结构检查：{selected.structural?.status}；实际成片抽样检查：
            {selected.semantic?.status}
            。抽样不代表完整视频或声音理解，终片仍需观看。
          </p>
          {selected.structural?.issues?.map((i: RecordData, n: number) => (
            <p key={n} role="alert">
              {i.message}
            </p>
          ))}
          {selected.semantic?.checks?.map((c: RecordData, n: number) => (
            <p key={n}>
              {c.status}：{c.summary}
              {c.issues?.join("；")}
            </p>
          ))}
          {selected.source_current === false && (
            <p role="alert">
              源制作已变化；当前版本保留在历史，须重新保存并检查。
            </p>
          )}
        </div>
      )}
      {selected?.preview && (
        <div className="timeline-preview">
          <h3>当前版本草稿预览</h3>
          <video
            controls
            src={selected.preview.video_url}
            aria-label="剪辑草稿预览"
          />
          <p>草稿预览尚不代表正式交付或用户验收。</p>
        </div>
      )}
      {selected?.export && (
        <div className="timeline-preview">
          <a href={selected.export.video_url} download>
            下载已检查的新剪辑成片
          </a>
          <label className="timeline-check">
            <input
              type="checkbox"
              checked={watched}
              onChange={(e) => setWatched(e.target.checked)}
            />
            已观看此剪辑，确认采用并进入出品人终审
          </label>
          <button
            disabled={
              disabled ||
              !saved ||
              !watched ||
              selected.source_current === false ||
              selected.review_current !== true
            }
            onClick={() =>
              void action("adopt", {
                version_id: version,
                confirmed: true,
                human_reviewed: watched,
              })
            }
          >
            采用此剪辑版本
          </button>
        </div>
      )}
    </section>
  );
}
