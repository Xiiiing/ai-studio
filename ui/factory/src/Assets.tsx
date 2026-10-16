import { useEffect, useState, type ReactNode } from "react";
import { useShotDraft } from "./SessionDraft";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Image, Film, Save, Play, ArrowUpRight } from "lucide-react";
import {
  request,
  keys,
  rows,
  text,
  mediaUrl,
  money,
  parseMoney,
  ownedMedia,
  type RecordData,
} from "./api";
import {
  Panel,
  Empty,
  Badge,
  ErrorNotice,
  Field,
  Readable,
  Artifact,
  Modal,
  Loading,
} from "./ui";
export function Shots({
  project,
  notify,
  initialShot,
}: {
  project: RecordData;
  notify: (v: string) => void;
  initialShot?: string;
}) {
  const query = useQuery({
      queryKey: keys.shots(project.id),
      queryFn: () => request("/projects/" + project.id + "/shots"),
      refetchInterval: 5000,
    }),
    [selected, setSelected] = useState(initialShot || "");
  useEffect(() => {
    if (initialShot) setSelected(initialShot);
  }, [initialShot]);
  const shots = rows(query.data?.shots),
    shot = shots.find((s) => s.id === selected) || shots[0];
  if (!query.data)
    return (
      <>
        <ErrorNotice error={query.error} />
        {query.error ? (
          <button className="button" onClick={() => query.refetch()}>
            重新读取镜头
          </button>
        ) : (
          <Loading />
        )}
      </>
    );
  if (!shot)
    return (
      <Empty title="分镜尚未生成">
        故事方向确定后，团队会提交可制作的逐镜计划。
      </Empty>
    );
  return (
    <section className="shot-workbench">
      <aside className="shot-list" aria-label="镜头列表">
        {shots.map((s) => (
          <button
            key={s.id}
            className={"shot-list-item " + (s.id === shot.id ? "active" : "")}
            data-shot-open={s.id}
            onClick={() => setSelected(s.id)}
          >
            <strong>
              {s.id} <small>{s.seconds} 秒</small>
            </strong>
            <span>{s.dramatic_beat || s.action || s.prompt}</span>
            <Badge status={s.status} />
          </button>
        ))}
      </aside>
      <ShotDetail
        key={project.id + ":" + shot.id}
        project={project}
        board={query.data}
        shot={shot}
        error={query.error}
        notify={notify}
      />
    </section>
  );
}
function ShotDetail({
  project,
  board,
  shot,
  error,
  notify,
}: {
  project: RecordData;
  board: RecordData;
  shot: RecordData;
  error: any;
  notify: (v: string) => void;
}) {
  const client = useQueryClient(),
    [failure, setFailure] = useState("");
  const { draft, setDraft, busy, begin, finish } = useShotDraft(
    project.id,
    shot.id,
  );
  const [editorOpen, setEditorOpen] = useState(!!draft);
  const candidates = [...rows(shot.candidates)].sort(
    (a, b) => Number(b.stage === "video") - Number(a.stage === "video"),
  );
  const revision = draft?.revision ?? board.revision,
    stale = !!draft && revision !== board.revision,
    disabled = busy || !board.editable || !!error;
  async function mutate(body: RecordData) {
    if (disabled || stale || !begin()) return;
    setFailure("");
    try {
      await request("/projects/" + project.id + "/shots/" + shot.id, body);
      if (body.action === "edit") setDraft(null);
      notify(
        body.action === "select"
          ? "选片已记录，原素材继续保留。"
          : "局部版本已保存，恢复制作后处理变更镜头。",
      );
      await client.invalidateQueries();
    } catch (e) {
      setFailure((e as Error).message);
    } finally {
      finish();
    }
  }
  const edit = (name: string, value: any) =>
    setDraft((d) => ({
      revision: d?.revision ?? board.revision,
      values: { ...shot, ...d?.values, [name]: value },
    }));
  const values = draft?.values || shot;
  return (
    <div className="shot-detail">
      <header className="shot-detail-heading">
        <div className="eyebrow">SHOT {shot.id}</div>
        <h2>
          {shot.id} · {shot.dramatic_beat || shot.action || "镜头详情"}
        </h2>
        <p>
          镜头版本 {shot.shot_revision || 1} / 制作版本{" "}
          {board.production_revision} · {shot.seconds} 秒
        </p>
      </header>
      <ErrorNotice error={error || failure} />
      {!board.editable && (
        <div className="notice">
          先暂停制作，等待当前安全步骤结束后，即可精修或选片。
        </div>
      )}
      <h3 className="section-label">
        候选比较 <span>{rows(shot.candidates).length} 个版本</span>
      </h3>
      {rows(board.errors).length > 0 && (
        <ErrorNotice error={"部分候选回执不可用：" + text(board.errors)} />
      )}
      <div className="candidate-grid">
        {candidates.map((c) => (
          <Candidate
            projectId={project.id}
            key={c.id || c.stage + ":" + c.take}
            candidate={c}
            revision={board.revision}
            disabled={disabled || stale || !c.selectable}
            onSubmit={mutate}
          />
        ))}
      </div>
      {!rows(shot.candidates).length && (
        <Empty title="此镜头尚无候选">
          素材生成后可比较画面、视频与审核记录。
        </Empty>
      )}
      <details
        className="shot-editor"
        open={editorOpen}
        onToggle={(event) => setEditorOpen(event.currentTarget.open)}
      >
        <summary className="shot-editor-summary">
          <strong>单镜精修</strong>
          <span className="shot-editor-status">
            {busy
              ? "正在保存"
              : stale
                ? "草稿版本待核对"
                : draft
                  ? "有未保存修改"
                  : !board.editable
                    ? "当前只读"
                    : "提示词、运镜与局部重制"}
          </span>
        </summary>
        <p className="muted">原候选、用量与历史回执继续保留。</p>
        <form
          data-shot-operation="edit"
          data-revision={revision}
          onSubmit={(e) => {
            e.preventDefault();
            mutate({
              action: "edit",
              expected_revision: revision,
              changes: {
                prompt: values.prompt || "",
                motion_prompt: values.motion_prompt || values.prompt || "",
                camera_motion: values.camera_motion || "hold",
              },
              regenerate: !!values.regenerate,
              stage: values.stage || "image",
            });
          }}
        >
          <fieldset className="editor-fields" disabled={disabled}>
            <Field label="首帧提示词">
              <textarea
                name="prompt"
                rows={4}
                maxLength={1600}
                required
                value={values.prompt || ""}
                onChange={(e) => edit("prompt", e.target.value)}
              />
            </Field>
            <Field label="视频提示词">
              <textarea
                name="motion_prompt"
                rows={3}
                maxLength={800}
                required
                value={values.motion_prompt || values.prompt || ""}
                onChange={(e) => edit("motion_prompt", e.target.value)}
              />
            </Field>
            <div className="form-row">
              <Field label="程序运镜">
                <select
                  name="camera_motion"
                  value={values.camera_motion || "hold"}
                  onChange={(e) => edit("camera_motion", e.target.value)}
                >
                  {Object.entries({
                    hold: "静止",
                    push_in: "推进",
                    pull_out: "拉远",
                    pan_left: "左移",
                    pan_right: "右移",
                  }).map(([v, l]) => (
                    <option value={v} key={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="重制范围">
                <select
                  name="stage"
                  value={values.stage || "image"}
                  onChange={(e) => edit("stage", e.target.value)}
                >
                  <option value="image">首帧与依赖视频</option>
                  <option value="video">仅视频</option>
                </select>
              </Field>
            </div>
            <label className="check">
              <input
                name="regenerate"
                type="checkbox"
                checked={!!values.regenerate}
                onChange={(e) => edit("regenerate", e.target.checked)}
              />
              明确创建新的生成版本
            </label>
          </fieldset>
          {stale && (
            <div className="notice">
              项目已更新，草稿仍绑定旧版本。
              <button
                type="button"
                className="button"
                data-shot-discard={shot.id}
                onClick={() => setDraft(null)}
              >
                放弃草稿并读取新版本
              </button>
            </div>
          )}
          <button
            type="submit"
            className="button primary"
            disabled={disabled || stale}
          >
            <Save size={15} />
            保存局部制作版本
          </button>
          <small className="form-help">
            保存后恢复制作；新的付费云请求仍需单独授权。
          </small>
        </form>
      </details>
    </div>
  );
}
function Candidate({
  candidate: c,
  revision,
  disabled,
  onSubmit,
  projectId,
}: {
  projectId: string;
  candidate: RecordData;
  revision: number;
  disabled: boolean;
  onSubmit: (body: RecordData) => void;
}) {
  const [confirmed, setConfirmed] = useState(false),
    [base, setBase] = useState(revision);
  const stale = confirmed && base !== revision,
    url = ownedMedia(projectId, c.media_url);
  return (
    <article className="candidate-card">
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
        <p>
          {c.producer_selected
            ? "出品人已选定"
            : c.plan_current
              ? "当前参数候选"
              : "历史参数候选"}
        </p>
        <small>模型：{c.model || c.receipt?.model || "未记录"}</small>
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
export function Series({
  project,
  notify,
}: {
  project: RecordData;
  notify: (v: string) => void;
}) {
  const query = useQuery({
    queryKey: keys.series(project.id),
    queryFn: () => request("/projects/" + project.id + "/series"),
  });
  if (!project.series_key)
    return (
      <Empty title="此项目为独立单集">
        创建新剧集时填写相同的系列标识，即可复用角色、场景与道具参考。
      </Empty>
    );
  if (!query.data)
    return (
      <>
        <ErrorNotice error={query.error} />
        {query.error ? (
          <button className="button" onClick={() => query.refetch()}>
            重新读取系列资产
          </button>
        ) : (
          <Loading />
        )}
      </>
    );
  const library = query.data,
    bible = library.bible,
    empty = {
      schema_version: 1,
      world: "",
      visual_rules: "",
      characters: [
        {
          character_id: "character_01",
          name: "",
          identity_constraints: "",
          default_wardrobe: "",
        },
      ],
      scenes: [],
      props: [],
    };
  const entities = Object.entries({
    character: "characters",
    scene: "scenes",
    prop: "props",
  }).flatMap(([kind, key]) =>
    rows(bible?.document?.[key]).map((e) => ({
      id: kind + ":" + (e.character_id || e.entity_id),
      label: e.name || e.character_id || e.entity_id,
    })),
  );
  const mutatePath = "/projects/" + project.id + "/series";
  const run = (body: RecordData, label: string) => (
    <SeriesOperation
      key={
        project.id +
        ":" +
        body.action +
        ":" +
        (body.asset_id || "") +
        ":" +
        (body.version || "")
      }
      project={project}
      base={body}
      path={mutatePath}
      label={label}
      notify={notify}
      library={library}
    />
  );
  return (
    <div className="stack">
      <ErrorNotice error={query.error} />
      <Panel
        title={"系列设定 · " + project.series_key}
        description={
          "最新 v" +
          (bible?.version || 0) +
          " · 本剧集固定 v" +
          (library.pinned_bible?.version || 0)
        }
      >
        {bible && <Readable value={bible.document} />}
        <details>
          <summary>编辑并追加设定版本</summary>
          {run(
            {
              action: "save_bible",
              expected_version: bible?.version || 0,
              document: bible?.document || empty,
            },
            "保存新的 Bible 版本",
          )}
        </details>
      </Panel>
      {bible && (
        <Panel
          title="计划新的参考资产"
          description="登记图片、技术验证与人工批准依次记录；计划操作不调用生成服务。"
        >
          {run(
            { action: "plan", expected_version: 0, entities },
            "建立计划素材",
          )}
        </Panel>
      )}
      <div className="asset-grid">
        {rows(library.assets).map((a) => (
          <Panel
            key={a.asset_id + ":" + a.version}
            title={(a.data?.entity?.name || a.entity_id) + " · v" + a.version}
            action={<Badge status={a.status} />}
            className="series-asset"
          >
            {mediaUrl(
              a.data?.receipt?.path ? "/media/" + a.data.receipt.path : "",
            ) && (
              <img
                className="asset-image"
                src={mediaUrl("/media/" + a.data.receipt.path)}
                alt={a.data?.entity?.name || "系列参考图"}
              />
            )}
            <small>{a.asset_id}</small>
            {a.data?.review && (
              <details>
                <summary>技术验证记录</summary>
                <Readable value={a.data.review} />
              </details>
            )}
            {a.status === "planned" &&
              (rows(library.sources).length ? (
                run(
                  {
                    action: "register",
                    asset_id: a.asset_id,
                    version: a.version,
                    expected_asset_revision: a.revision,
                  },
                  "登记当前项目图片",
                )
              ) : (
                <p>项目还没有可登记的图片回执。</p>
              ))}
            {["generated", "reviewed"].includes(a.status) &&
              run(
                {
                  action: "review",
                  asset_id: a.asset_id,
                  version: a.version,
                  expected_asset_revision: a.revision,
                },
                "执行技术验证",
              )}
            {a.status === "reviewed" &&
              a.data?.review?.technical?.status === "pass" &&
              run(
                {
                  action: "approve",
                  asset_id: a.asset_id,
                  version: a.version,
                  expected_asset_revision: a.revision,
                },
                "明确批准此版本",
              )}
            {a.status === "approved" && (
              <>
                <details>
                  <summary>固定引用到镜头</summary>
                  {run(
                    {
                      action: "bind",
                      asset_id: a.asset_id,
                      version: a.version,
                      kind: a.kind,
                      entity_id: a.entity_id,
                    },
                    "固定引用此版本",
                  )}
                </details>
                {run(
                  {
                    action: "plan",
                    asset_id: a.asset_id,
                    expected_version: a.version,
                    kind: a.kind,
                    entity_id: a.entity_id,
                    entities: [
                      {
                        id: a.kind + ":" + a.entity_id,
                        label: a.data?.entity?.name || a.entity_id,
                      },
                    ],
                  },
                  "计划下一个版本",
                )}
              </>
            )}
            {rows(a.affected_shots).length > 0 && (
              <details>
                <summary>受影响的镜头</summary>
                <Readable value={a.affected_shots} />
              </details>
            )}
          </Panel>
        ))}
      </div>
      <Panel title="本剧固定引用">
        <Readable value={library.bindings || {}} />
      </Panel>
      {library.truncated && (
        <div className="notice">
          仅显示最新 256 个资产版本，历史素材与引用继续保留。
        </div>
      )}
    </div>
  );
}
export function SeriesOperation({
  project,
  base,
  path,
  label,
  notify,
  library,
}: {
  project: RecordData;
  base: RecordData;
  path: string;
  label: string;
  notify: (v: string) => void;
  library: RecordData;
}) {
  const client = useQueryClient(),
    [revision, setRevision] = useState(project.revision),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [sourceVersion, setSourceVersion] = useState({
    expected_version: base.expected_version,
    expected_asset_revision: base.expected_asset_revision,
  });
  const [document, setDocument] = useState(
    JSON.stringify(base.document, null, 2),
  );
  const stale =
    revision !== project.revision ||
    sourceVersion.expected_version !== base.expected_version ||
    sourceVersion.expected_asset_revision !== base.expected_asset_revision;
  return (
    <form
      className="series-form"
      onChange={() => {
        if (!dirty) {
          setRevision(project.revision);
          setDirty(true);
        }
      }}
      onSubmit={async (e) => {
        e.preventDefault();
        const fd = new FormData(e.currentTarget),
          values = Object.fromEntries(fd);
        setBusy(true);
        setError("");
        try {
          const body: RecordData = {
            action: base.action,
            expected_revision: revision,
            ...(base.asset_id ? { asset_id: base.asset_id } : {}),
            ...(base.version ? { version: base.version } : {}),
            ...(base.expected_asset_revision
              ? {
                  expected_asset_revision:
                    sourceVersion.expected_asset_revision,
                }
              : {}),
          };
          if (base.action === "save_bible") {
            body.expected_version = sourceVersion.expected_version;
            body.document = JSON.parse(String(values.document));
          }
          if (base.action === "plan") {
            const [kind, entity_id] = String(values.entity).split(":");
            Object.assign(body, {
              kind,
              entity_id,
              expected_version: sourceVersion.expected_version,
            });
          }
          if (base.action === "register") body.source_key = values.source_key;
          if (base.action === "approve")
            body.confirmed = values.confirmed === "on";
          if (base.action === "bind") {
            body.shot_ids = fd.getAll("shot_ids");
            body.role = values.role;
            if (!body.shot_ids.length)
              throw new Error("请明确选择至少一个镜头。");
          }
          await request(path, body);
          setDirty(false);
          notify("系列资产操作已记录，已有引用与素材保留。");
          await client.invalidateQueries();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <ErrorNotice error={error} />
      {base.action === "save_bible" && (
        <Field label="正式设定 JSON">
          <textarea
            name="document"
            rows={14}
            required
            value={document}
            onChange={(e) => setDocument(e.target.value)}
            spellCheck={false}
          />
        </Field>
      )}
      {base.action === "plan" && (
        <Field label="系列实体">
          <select name="entity" required>
            {rows(base.entities).map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
      )}
      {base.action === "register" && (
        <Field label="当前项目已登记图片">
          <select name="source_key" required>
            {rows(library.sources).map((s) => (
              <option key={s.key} value={s.key}>
                {s.key}
              </option>
            ))}
          </select>
        </Field>
      )}
      {base.action === "approve" && (
        <label className="check">
          <input type="checkbox" name="confirmed" required />
          我已审看并确认此版本可正式复用
        </label>
      )}
      {base.action === "bind" && (
        <>
          <fieldset>
            <legend>实际使用此资产的镜头</legend>
            <div className="shot-options">
              {rows(project.shots).map((s) => (
                <label className="check" key={s.id}>
                  <input name="shot_ids" type="checkbox" value={s.id} />
                  {s.id}
                </label>
              ))}
            </div>
          </fieldset>
          {base.kind === "character" ? (
            <Field label="对应本剧角色">
              <select name="role">
                {Object.keys(
                  (project.episode || project.script || {}).cast || {},
                ).map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
            </Field>
          ) : (
            <input type="hidden" name="role" value={base.entity_id} />
          )}
        </>
      )}
      {stale && (
        <div className="notice">
          项目已更新，草稿保留。
          <button
            type="button"
            className="button"
            onClick={() => {
              setRevision(project.revision);
              setSourceVersion({
                expected_version: base.expected_version,
                expected_asset_revision: base.expected_asset_revision,
              });
            }}
          >
            已核对当前版本
          </button>
        </div>
      )}
      <button className="button" disabled={busy || stale}>
        {label}
      </button>
    </form>
  );
}
export function Costs({
  project,
  notify,
}: {
  project: RecordData;
  notify: (v: string) => void;
}) {
  const client = useQueryClient(),
    query = useQuery({
      queryKey: keys.costs(project.id),
      queryFn: () => request("/projects/" + project.id + "/cloud-budget"),
      refetchInterval: 15000,
    });
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const ledger = query.data,
    quote = ledger?.quote || project.cloud_quote;
  async function post(path: string, payload: RecordData) {
    setBusy(true);
    setError("");
    try {
      await request("/projects/" + project.id + "/" + path, payload);
      notify(
        path === "cloud-confirm-cost"
          ? "实际费用已确认，任务与核对依据继续保留。"
          : "云请求批次授权已记录。",
      );
      await client.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!ledger)
    return (
      <>
        <ErrorNotice error={query.error} />
        {query.error ? (
          <button className="button" onClick={() => query.refetch()}>
            重新读取费用记录
          </button>
        ) : (
          <Loading />
        )}
      </>
    );
  const unknown = quote?.pricing?.unit_cost_micros == null,
    auth = rows(ledger.authorizations)
      .filter((a) => a.batch_id === quote?.id)
      .at(-1),
    editable =
      !["approved", "cancelled"].includes(project.status) &&
      (!auth ||
        String(project.needs_decision?.code || "").startsWith("cloud_"));
  return (
    <div className="stack">
      <ErrorNotice error={error || query.error} />
      <Panel
        title="云媒体费用与授权"
        description="每批请求按模型、参数和生产版本绑定授权，累计用量不会因返修清零。"
      >
        {quote ? (
          <>
            <div className="quote-summary">
              <div>
                <small>批次总估价</small>
                <strong>
                  {money(quote.estimated_total_micros, quote.pricing?.currency)}
                </strong>
              </div>
              <div>
                <small>镜头 / 预计提交</small>
                <strong>
                  {quote.shot_count} / {quote.estimated_submissions}
                </strong>
              </div>
              <div>
                <small>模型与规格</small>
                <p>
                  {quote.connection?.model} · {quote.resolution} ·{" "}
                  {quote.duration} 秒
                </p>
              </div>
            </div>
            <p className="muted">
              估价依据：{quote.pricing?.source || "单价未确认"}
              。供应商最终账单另行核对。
            </p>
            <details>
              <summary>本批次镜头与参数</summary>
              {rows(quote.lines).map((l, i) => (
                <article key={i}>
                  <h3>
                    {l.shot_id} · {l.take}
                  </h3>
                  <p>{l.prompt}</p>
                  <small>参数签名 {l.request_signature}</small>
                </article>
              ))}
            </details>
            {editable ? (
              <CloudAuthorization
                quote={quote}
                projectRevision={project.revision}
                busy={busy}
                onSubmit={(payload) => post("cloud-authorize", payload)}
              />
            ) : (
              <div className="notice">
                本批次已有授权，新的参数与返修候选仍需重新授权。
              </div>
            )}
          </>
        ) : (
          <Empty title="暂无待授权的视频批次">
            首帧与请求参数就绪后，团队会先提交批次估价。
          </Empty>
        )}
      </Panel>
      <Panel title="累计费用账本">
        <div className="cost-totals">
          {Object.entries(ledger.totals || {}).map(
            ([currency, total]: [string, any]) => (
              <article key={currency}>
                <small>{currency}</small>
                <strong>{money(total.confirmed_cost_micros, currency)}</strong>
                <p>
                  累计估算 {money(total.estimated_cost_micros, currency)} ·{" "}
                  {total.unconfirmed_tasks} 项未确认
                </p>
              </article>
            ),
          )}
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>镜头 / 尝试</th>
                <th>模型</th>
                <th>供应商任务</th>
                <th>状态</th>
                <th>估算</th>
                <th>确认费用</th>
              </tr>
            </thead>
            <tbody>
              {rows(ledger.tasks).map((t, i) => (
                <tr key={t.intent_id || i}>
                  <td>
                    {t.shot_id} / {t.attempt}
                  </td>
                  <td>{t.model}</td>
                  <td>{t.task_id || "尚未确认"}</td>
                  <td>
                    <Badge status={t.status} />
                  </td>
                  <td>{money(t.estimated_cost_micros, t.currency)}</td>
                  <td>{money(t.confirmed_cost_micros, t.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <FeeConfirmation
          tasks={rows(ledger.tasks)}
          busy={busy}
          onSubmit={(payload) => post("cloud-confirm-cost", payload)}
        />
      </Panel>
    </div>
  );
}

export function CloudAuthorization({
  quote,
  projectRevision,
  busy,
  onSubmit,
}: {
  quote: RecordData;
  projectRevision: number;
  busy: boolean;
  onSubmit: (payload: RecordData) => void;
}) {
  const makeSnapshot = () => ({
    batch: quote.id,
    revision: projectRevision,
    unknown: quote.pricing?.unit_cost_micros == null,
    count: quote.estimated_submissions,
    cost:
      quote.estimated_total_micros == null
        ? ""
        : String(quote.estimated_total_micros / 1e6),
    currency: quote.pricing?.currency || "CNY",
  });
  const [draft, setDraft] = useState(makeSnapshot),
    [confirmed, setConfirmed] = useState(false),
    [error, setError] = useState("");
  const stale = draft.batch !== quote.id || draft.revision !== projectRevision;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (stale) return;
        try {
          if (draft.unknown && !confirmed)
            throw new Error("请明确确认未知费用风险。");
          onSubmit({
            batch_id: draft.batch,
            expected_revision: draft.revision,
            max_submissions: Number(draft.count),
            max_estimated_cost_micros: draft.unknown
              ? null
              : parseMoney(draft.cost),
            confirm_unknown_cost: draft.unknown && confirmed,
          });
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <ErrorNotice error={error} />
      <div className="form-row">
        <Field label="本批次累计最多提交次数">
          <input
            type="number"
            name="max_submissions"
            min={1}
            max={1000}
            required
            value={draft.count}
            onChange={(e) =>
              setDraft((d) => ({ ...d, count: Number(e.target.value) }))
            }
          />
        </Field>
        {!draft.unknown && (
          <Field label={"累计估价额度 / " + draft.currency}>
            <input
              name="max_cost"
              required
              inputMode="decimal"
              value={draft.cost}
              onChange={(e) =>
                setDraft((d) => ({ ...d, cost: e.target.value }))
              }
            />
          </Field>
        )}
      </div>
      {draft.unknown && (
        <label className="check">
          <input
            type="checkbox"
            name="confirm_unknown_cost"
            required
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          我已了解单价未确认，仍授权此批请求
        </label>
      )}
      {stale && (
        <div className="notice">
          批次或项目版本已更新，原授权草稿仍保留。
          <button
            className="button"
            type="button"
            onClick={() => {
              setDraft(makeSnapshot());
              setConfirmed(false);
              setError("");
            }}
          >
            重新核对当前批次
          </button>
        </div>
      )}
      <button className="button primary" disabled={busy || stale}>
        明确授权本批次
      </button>
    </form>
  );
}
export function FeeConfirmation({
  tasks,
  busy,
  onSubmit,
}: {
  tasks: RecordData[];
  busy: boolean;
  onSubmit: (payload: RecordData) => void;
}) {
  const [draft, setDraft] = useState({
      intent_id: "",
      currency: "",
      amount: "",
      evidence: "",
    }),
    [error, setError] = useState("");
  const selected = tasks.find((t) => t.intent_id === draft.intent_id),
    stale =
      !!draft.intent_id &&
      (!selected ||
        selected.confirmed_cost_micros != null ||
        selected.currency !== draft.currency);
  const pending = tasks.filter(
    (t) => t.intent_id && t.confirmed_cost_micros == null,
  );
  if (!pending.length && !draft.intent_id) return null;
  return (
    <details>
      <summary>按供应商账单确认实际费用</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (stale) return;
          try {
            onSubmit({
              intent_id: draft.intent_id,
              amount_micros: parseMoney(draft.amount),
              currency: draft.currency,
              evidence: draft.evidence,
            });
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <ErrorNotice error={error} />
        <Field label="任务">
          <select
            name="intent_id"
            required
            value={draft.intent_id}
            onChange={(e) => {
              const task = tasks.find((t) => t.intent_id === e.target.value);
              setDraft({
                intent_id: e.target.value,
                currency: task?.currency || "",
                amount: "",
                evidence: "",
              });
            }}
          >
            <option value="">明确选择供应商任务</option>
            {pending.map((t) => (
              <option key={t.intent_id} value={t.intent_id}>
                {t.shot_id} · {t.task_id || t.intent_id}
              </option>
            ))}
            {stale && (
              <option value={draft.intent_id}>
                {draft.intent_id} · 状态已更新
              </option>
            )}
          </select>
        </Field>
        <div className="form-row">
          <Field label="实际金额">
            <input
              name="amount"
              required
              inputMode="decimal"
              value={draft.amount}
              onChange={(e) =>
                setDraft((d) => ({ ...d, amount: e.target.value }))
              }
            />
          </Field>
          <Field label="币种">
            <input name="currency" required readOnly value={draft.currency} />
          </Field>
        </div>
        <Field label="核对依据">
          <textarea
            name="evidence"
            rows={3}
            required
            value={draft.evidence}
            onChange={(e) =>
              setDraft((d) => ({ ...d, evidence: e.target.value }))
            }
          />
        </Field>
        {stale && (
          <div className="notice">
            此任务已被确认或币种发生变化。原金额和依据保留，请重新选择并核对任务。
          </div>
        )}
        <button className="button" disabled={busy || stale || !draft.intent_id}>
          确认实际费用
        </button>
      </form>
    </details>
  );
}
