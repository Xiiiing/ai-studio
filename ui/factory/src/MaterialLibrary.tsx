import { StudioMenu } from "./StudioMenu";
import { StudioSelect } from "./StudioSelect";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request, rows, mediaUrl, type RecordData } from "./api";
import {
  Heading,
  PageFocus,
  Panel,
  Field,
  ErrorNotice,
  FloatingErrorNotice,
  Empty,
  Loading,
  Modal,
  Badge,
} from "./ui";

export const materialKinds: Record<string, string> = {
  character: "人物",
  scene: "场景",
  style: "画风",
  composition: "组合参考",
};

function validateMaterialFile(file: File) {
  if (
    file.size > 8 * 1024 * 1024 ||
    !["image/png", "image/jpeg", "image/webp"].includes(file.type)
  )
    throw new Error("请选择不超过8 MiB的PNG/JPEG/WebP图片。");
}

export async function readMaterialFile(file: File): Promise<string> {
  validateMaterialFile(file);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取图片"));
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.readAsDataURL(file);
  });
}

export function MaterialLibrary({
  project,
  notify,
}: {
  project?: RecordData;
  notify: (message: string) => void;
}) {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<RecordData | null>(null);
  const [editorSession, setEditorSession] = useState(0);
  const [editorInitialDocument, setEditorInitialDocument] =
    useState<RecordData>();
  function openEditor(asset: RecordData, initialDocument?: RecordData) {
    setEditorSession((session) => session + 1);
    setEditing(asset);
    setEditorInitialDocument(initialDocument);
  }
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const [generation, setGeneration] = useState("");
  const [preview, setPreview] = useState<{ url: string; title: string } | null>(
    null,
  );
  const query = useQuery({
    queryKey: ["materials", project?.id, search, kind, offset],
    queryFn: () =>
      request(
        "/library?" +
          new URLSearchParams({
            ...(project ? { project_id: project.id } : {}),
            q: search,
            kind,
            offset: String(offset),
          }),
      ),
  });
  const selected = useQuery({
    queryKey: ["material-selection", project?.id, project?.revision],
    refetchInterval: 5000,
    enabled: !!project,
    queryFn: () => request("/projects/" + project!.id + "/materials"),
  });
  const tasks = useQuery({
    queryKey: ["material-tasks", project?.id],
    refetchInterval: 5000,
    queryFn: () =>
      request("/library/tasks" + (project ? "?project_id=" + project.id : "")),
  });
  async function run(work: () => Promise<unknown>, message: string) {
    setBusy(true);
    setFailure("");
    try {
      await work();
      notify(message);
    } catch (error) {
      setFailure((error as Error).message);
    } finally {
      await client.invalidateQueries();
      setBusy(false);
    }
  }
  async function select(asset: RecordData, role: string, shotIds: string[]) {
    const refs = rows(selected.data?.selected)
      .map((row) => row.binding)
      .filter(
        (ref) =>
          !(
            ref.kind === asset.kind &&
            (asset.kind === "style" || ref.role === role)
          ),
      );
    refs.push({
      asset_id: asset.asset_id,
      version: asset.version,
      role,
      ...(shotIds.length ? { shot_ids: shotIds } : {}),
    });
    await request("/projects/" + project!.id + "/materials", {
      action: "select",
      expected_revision: selected.data!.revision,
      references: refs.map(({ asset_id, version, role, shot_ids }) => ({
        asset_id,
        version,
        role,
        ...(shot_ids ? { shot_ids } : {}),
      })),
    });
  }
  function startAI(operation: string, asset?: RecordData) {
    const style = rows(selected.data?.selected).find(
      (row) => row.binding.kind === "style",
    )?.binding;
    let document = asset?.data.document;
    if (operation === "compose" && document && project) {
      const selection = rows(selected.data?.selected);
      document = {
        ...document,
        members: rows(document.members).map((member) => {
          const character = selection.find(
            (row) =>
              row.binding.kind === "character" &&
              row.binding.role === member.role,
          );
          return character
            ? {
                ...member,
                asset_id: character.binding.asset_id,
                version: character.binding.version,
              }
            : member;
        }),
      };
      const scene = selection.find(
        (row) =>
          row.binding.kind === "scene" &&
          (row.binding.asset_id === document.scene?.asset_id ||
            row.asset.data.source?.asset_id === document.scene?.asset_id),
      );
      if (scene)
        document.scene = {
          asset_id: scene.binding.asset_id,
          version: scene.binding.version,
        };
    }
    return run(
      () =>
        request("/library/tasks", {
          request_id: crypto.randomUUID(),
          project_id: project?.id,
          scope: project ? "project" : "global",
          operation,
          ...(asset
            ? { asset_id: asset.asset_id, version: asset.version }
            : {
                kind: kind || "character",
                document: {
                  name: generation.slice(0, 100),
                  description: generation,
                },
              }),
          ...(style && operation !== "describe"
            ? { style: { asset_id: style.asset_id, version: style.version } }
            : {}),
          ...(operation === "views"
            ? { views: ["正面全身", "侧面全身", "背面全身"] }
            : {}),
          ...(operation === "compose" ? { document } : {}),
          prompt: generation,
        }),
      "AI 素材任务已建立，候选与费用状态在下方显示。",
    );
  }
  async function prepareDrama() {
    const selection = rows(selected.data?.selected);
    const style = selection.find(
      (row) => row.binding.kind === "style",
    )?.binding;
    if (!style) throw new Error("请先选择本剧共同画风。");
    for (const row of selection.filter((row) =>
      ["character", "scene"].includes(row.binding.kind),
    )) {
      if (
        row.asset.data.style?.asset_id === style.asset_id &&
        row.asset.data.style?.version === style.version
      )
        continue;
      await request("/library/tasks", {
        request_id: crypto.randomUUID(),
        project_id: project!.id,
        scope: "project",
        operation: rows(row.asset.data.images).length ? "restyle" : "generate",
        asset_id: row.binding.asset_id,
        version: row.binding.version,
        style: { asset_id: style.asset_id, version: style.version },
        prompt: generation,
      });
    }
  }
  async function adoptCandidates() {
    const selection = rows(selected.data?.selected);
    const style = selection.find(
      (row) => row.binding.kind === "style",
    )?.binding;
    const refs = selection.map((row) => {
      const candidate = rows(tasks.data?.tasks).find(
        (task) =>
          task.status === "completed" &&
          ["restyle", "generate", "compose"].includes(task.operation) &&
          task.result.asset_id &&
          task.source?.asset_id === row.binding.asset_id &&
          task.source?.version === row.binding.version &&
          (!style ||
            (task.style?.asset_id === style.asset_id &&
              task.style?.version === style.version)),
      );
      const { asset_id, version, role, shot_ids } = row.binding;
      return {
        asset_id: candidate?.result.asset_id || asset_id,
        version: candidate?.result.version || version,
        role,
        ...(shot_ids ? { shot_ids } : {}),
      };
    });
    if (
      !refs.some(
        (ref, index) =>
          ref.asset_id !== selection[index].binding.asset_id ||
          ref.version !== selection[index].binding.version,
      )
    )
      throw new Error("还没有可采用的新造型候选。");
    await request("/projects/" + project!.id + "/materials", {
      action: "select",
      expected_revision: selected.data!.revision,
      references: refs,
    });
  }
  return (
    <div className="stack">
      {project ? (
        <div className="panel-head">
          <div>
            <h2>本剧素材与定稿</h2>
            <p>
              选用人物、场景和共同画风，集中确认后用于后续镜头制作。剧内修改保留独立版本。
            </p>
          </div>
        </div>
      ) : (
        <Heading
          eyebrow="MATERIAL LIBRARY"
          title="统一素材库"
          description="保存可跨剧复用的人物、场景、画风与同框参考。"
        />
      )}
      {query.data && (!project || selected.data) && (
        <PageFocus
          label={project ? "本剧已选" : "本页素材"}
          value={`${project ? rows(selected.data?.selected).length : rows(query.data.assets).length} 项`}
          detail={
            project
              ? "选择固定版本的人物、场景与画风，用于本剧后续镜头制作。"
              : "当前页的素材结果；可搜索、分类并翻页查看，版本与原始素材保持关联。"
          }
        />
      )}
      <FloatingErrorNotice error={failure} />
      {selected.error && (
        <div>
          <FloatingErrorNotice error={selected.error} />
          <button className="button" onClick={() => selected.refetch()}>
            重新读取本剧选择
          </button>
        </div>
      )}
      <div className="form-row material-library-toolbar">
        <Field label="搜索素材">
          <input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
          />
        </Field>
        <Field label="素材分类">
          <StudioSelect
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">全部分类</option>
            {Object.entries(materialKinds).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </StudioSelect>
        </Field>
        <button
          className="button primary"
          disabled={busy}
          onClick={() =>
            openEditor({
              kind: kind || "character",
              data: { document: { name: "", description: "" } },
            })
          }
        >
          新建素材
        </button>
      </div>
      <Panel
        title="AI 素材制作"
        description="输入人物或场景设定可从零生成；也可对已有素材识图建档、转换画风和补视图。候选经确认后采用。"
      >
        <Field label="AI制作要求">
          <textarea
            maxLength={4000}
            value={generation}
            onChange={(e) => setGeneration(e.target.value)}
            placeholder="例如：成年短发女性，圆脸，灰色外套，柔和水彩画风。"
          />
        </Field>
        <button
          className="button"
          disabled={busy || !generation.trim()}
          onClick={() => startAI("generate")}
        >
          按文字生成{materialKinds[kind || "character"]}
        </button>
      </Panel>
      {editing && (
        <MaterialEditor
          key={editorSession}
          asset={editing}
          initialDocument={editorInitialDocument}
          project={project}
          roles={materialRoles(project, selected.data)}
          candidates={rows(query.data?.assets)}
          busy={busy}
          onCancel={() => setEditing(null)}
          onKind={(kind) => setEditing((old) => (old ? { ...old, kind } : old))}
          save={(document, uploads, scope, uploaded) =>
            run(async () => {
              uploads.forEach(validateMaterialFile);
              let response = { asset: editing };
              if (
                !editing.asset_id ||
                JSON.stringify(document) !==
                  JSON.stringify(editing.data.document)
              )
                response = await request("/library", {
                  action: editing.asset_id ? "revise" : "create",
                  asset_id: editing.asset_id,
                  expected_version: editing.version,
                  kind: editing.kind,
                  project_id: project?.id,
                  document,
                  scope,
                });
              setEditing(response.asset);
              // Each upload is independently versioned and bounded; a failed later file leaves earlier views available.
              for (const file of uploads) {
                response = await request("/library", {
                  action: "upload",
                  project_id: project?.id,
                  asset_id: response.asset.asset_id,
                  expected_version: response.asset.version,
                  image_base64: await readMaterialFile(file),
                  label: file.name,
                });
                setEditing(response.asset);
                uploaded(file);
              }
              setEditing(null);
            }, "素材已保存，可继续添加视图或选入项目。")
          }
        />
      )}
      {project && selected.data && (
        <Panel
          title="全剧定稿"
          description="确认前请检查人物身份、画风及同框对应。更新选择后，旧定稿继续使用，直到明确确认新版。"
        >
          {rows(selected.data.selected).map((row) => (
            <div
              className="row-between"
              key={row.binding.kind + row.binding.role}
            >
              <span>
                {materialKinds[row.binding.kind]} ·{" "}
                {row.asset.data.document.name} · v{row.binding.version} ·{" "}
                {row.binding.role}
              </span>
              <button
                className="button"
                disabled={busy}
                onClick={() =>
                  run(
                    () =>
                      request("/projects/" + project.id + "/materials", {
                        action: "select",
                        expected_revision: selected.data!.revision,
                        references: rows(selected.data!.selected)
                          .filter((other) => other !== row)
                          .map((other) => {
                            const { asset_id, version, role, shot_ids } =
                              other.binding;
                            return {
                              asset_id,
                              version,
                              role,
                              ...(shot_ids ? { shot_ids } : {}),
                            };
                          }),
                      }),
                    "已更新待定稿选择。",
                  )
                }
              >
                移出本批
              </button>
            </div>
          ))}
          <p>
            {selected.data.package
              ? "已有定稿，确认新的选择会显示并更新相关镜头的制作版本。"
              : "尚未定稿。"}
          </p>
          <div className="form-row">
            <button
              className="button"
              disabled={busy || !rows(selected.data.selected).length}
              onClick={() =>
                run(prepareDrama, "全剧人物和场景造型任务已建立。")
              }
            >
              按共同画风准备全剧造型
            </button>
            <button
              className="button"
              disabled={busy}
              onClick={() =>
                run(
                  adoptCandidates,
                  "已将完成的造型候选加入待定稿，请集中检查。",
                )
              }
            >
              将完成造型加入待定稿
            </button>
          </div>
          <button
            className="button primary"
            disabled={busy || !rows(selected.data.selected).length}
            onClick={() =>
              run(
                () =>
                  request("/projects/" + project.id + "/materials", {
                    action: "confirm",
                    expected_revision: selected.data!.revision,
                    confirmed: true,
                  }),
                "本批视觉素材已定稿，后续制作使用固定版本。",
              )
            }
          >
            确认本批视觉定稿
          </button>
          {project.status === "paused" && selected.data.package && (
            <button
              className="button"
              disabled={busy}
              onClick={() =>
                run(
                  () =>
                    request("/projects/" + project.id + "/action", {
                      action: "resume",
                      expected_revision: selected.data!.revision,
                    }),
                  "已恢复制作，团队将读取本剧定稿素材。",
                )
              }
            >
              开始 / 恢复制作
            </button>
          )}
        </Panel>
      )}
      {query.error && (
        <div>
          <FloatingErrorNotice error={query.error} />
          <button className="button" onClick={() => query.refetch()}>
            重新读取素材库
          </button>
        </div>
      )}
      {!query.data ? (
        query.isPending ? (
          <Loading />
        ) : null
      ) : !rows(query.data.assets).length ? (
        <Empty title={search || kind ? "没有匹配的素材" : "还没有素材"}>
          {search || kind
            ? "试试其他名称或分类。"
            : "上传参考图片，或先建立人物和场景设定。"}
        </Empty>
      ) : (
        <div className="asset-grid">
          {rows(query.data.assets).map((asset) => (
            <MaterialCard
              key={asset.asset_id + ":" + asset.version}
              asset={asset}
              project={project}
              selected={selected.data}
              busy={busy}
              preview={(url, title) => setPreview({ url, title })}
              edit={() => {
                if (project && asset.library_key === "global")
                  run(async () => {
                    const response = await request("/library", {
                      action: "clone",
                      project_id: project.id,
                      asset_id: asset.asset_id,
                      expected_version: asset.version,
                      scope: "project",
                    });
                    openEditor(response.asset);
                  }, "已建立剧内副本，可独立修改。");
                else openEditor(asset);
              }}
              run={run}
              select={select}
              startAI={(operation) => startAI(operation, asset)}
            />
          ))}
        </div>
      )}
      <Panel title="AI 素材任务">
        {tasks.error && (
          <div>
            <FloatingErrorNotice error={tasks.error} />
            <button className="button" onClick={() => tasks.refetch()}>
              重新读取素材任务
            </button>
          </div>
        )}
        {tasks.isPending ? (
          <Loading />
        ) : tasks.data && !rows(tasks.data.tasks).length ? (
          <p>尚无素材制作任务。</p>
        ) : null}
        {rows(tasks.data?.tasks).map((task) => (
          <MaterialTask
            key={task.id}
            task={task}
            project={project}
            busy={busy}
            run={run}
            applyDraft={() =>
              run(async () => {
                const asset = await request(
                  "/library/assets/" +
                    task.source.asset_id +
                    "?" +
                    new URLSearchParams({
                      version: String(task.source.version),
                      ...(project ? { project_id: project.id } : {}),
                    }),
                );
                if (project && asset.library_key === "global") {
                  const copy = await request("/library", {
                    action: "clone",
                    project_id: project.id,
                    asset_id: asset.asset_id,
                    expected_version: asset.version,
                    scope: "project",
                  });
                  openEditor(copy.asset, task.result.document);
                } else openEditor(asset, task.result.document);
              }, "已带入识图草稿，请检查并保存。")
            }
          />
        ))}
      </Panel>
      {(offset > 0 || query.data?.has_more) && (
        <div className="form-row">
          <span>第 {Math.floor(offset / 50) + 1} 页</span>
          <button
            className="button"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            上一页
          </button>
          <button
            className="button"
            disabled={!query.data?.has_more}
            onClick={() => setOffset(offset + 50)}
          >
            下一页
          </button>
        </div>
      )}
      {preview && (
        <Modal
          id="material-preview-dialog"
          title={preview.title}
          onClose={() => setPreview(null)}
        >
          <img
            className="material-full-preview"
            src={preview.url}
            alt={preview.title}
          />
          <a className="button" href={preview.url} download>
            下载参考原图
          </a>
        </Modal>
      )}
    </div>
  );
}

function materialRoles(
  project?: RecordData,
  selection?: RecordData,
): RecordData[] {
  const characters = rows(selection?.characters).length
    ? rows(selection?.characters)
    : rows(
        (project?.production_profile || project?.episode?.production_profile)
          ?.characters,
      );
  return characters.length
    ? characters
    : [
        { character_id: "female", name: "角色一" },
        { character_id: "male", name: "角色二" },
      ];
}

function MaterialCard({
  asset,
  project,
  selected,
  busy,
  edit,
  run,
  select,
  startAI,
  preview,
}: {
  asset: RecordData;
  project?: RecordData;
  selected?: RecordData;
  busy: boolean;
  edit: () => void;
  run: (work: () => Promise<unknown>, message: string) => Promise<void>;
  select: (asset: RecordData, role: string, shots: string[]) => Promise<void>;
  startAI: (operation: string) => Promise<void>;
  preview: (url: string, title: string) => void;
}) {
  const roles = materialRoles(project, selected);
  const [role, setRole] = useState(
    asset.kind === "character" ? roles[0].character_id : asset.asset_id,
  );
  const selectedRole =
    asset.kind === "character" &&
    !roles.some((character) => character.character_id === role)
      ? roles[0].character_id
      : role;
  const [shots, setShots] = useState<string[]>([]);
  const document = asset.data.document;
  const binding = rows(selected?.selected)
    .map((row) => row.binding)
    .find(
      (ref) =>
        ref.asset_id === asset.asset_id &&
        ref.version === asset.version &&
        ref.role === selectedRole,
    );
  const selectedHere =
    !!binding &&
    JSON.stringify([...(binding.shot_ids || [])].sort()) ===
      JSON.stringify([...shots].sort());
  return (
    <Panel
      title={document.name + " · v" + asset.version}
      description={
        materialKinds[asset.kind] +
        " · " +
        (asset.library_key === "global" ? "统一库" : "剧内库") +
        " · " +
        (asset.status === "approved" ? "已确认" : "候选")
      }
    >
      {binding && (
        <Badge status="approved" label={"本批已选 · " + binding.role} />
      )}
      <div className="material-views">
        {rows(asset.data.images).map((image, index) => (
          <figure key={image.path}>
            <button
              className="material-preview-trigger"
              aria-label={"放大查看 " + document.name + " · " + image.label}
              onClick={() =>
                preview(
                  mediaUrl("/media/" + image.path),
                  document.name + " · " + image.label,
                )
              }
            >
              <img
                src={mediaUrl("/media/" + image.path)}
                alt={document.name + " · " + image.label}
                loading="lazy"
              />
            </button>
            <figcaption>
              {image.label}
              {index === 0 ? " · 主参考" : ""}
            </figcaption>
            {(!project || asset.library_key !== "global") && (
              <div>
                {index > 0 && (
                  <button
                    className="button"
                    disabled={busy}
                    onClick={() =>
                      run(
                        () =>
                          request("/library", {
                            action: "set_primary",
                            project_id: project?.id,
                            asset_id: asset.asset_id,
                            expected_version: asset.version,
                            image_index: index,
                          }),
                        "已追加版本并设置主参考，旧版本保持不变。",
                      )
                    }
                  >
                    设为主参考
                  </button>
                )}
                <button
                  className="button"
                  disabled={busy}
                  onClick={() =>
                    run(
                      () =>
                        request("/library", {
                          action: "remove_image",
                          project_id: project?.id,
                          asset_id: asset.asset_id,
                          expected_version: asset.version,
                          image_index: index,
                        }),
                      "已从新版本移除此图，历史图片继续保留。",
                    )
                  }
                >
                  从新版移除此图
                </button>
              </div>
            )}
          </figure>
        ))}
      </div>
      <p>{document.description}</p>
      <p>{document.identity_constraints || document.visual_rules}</p>
      {asset.data.review && (
        <p>
          视觉审核：{asset.data.review.status} ·{" "}
          {asset.data.review.summary || asset.data.review.detail}
        </p>
      )}
      <div className="form-row">
        <button className="button" disabled={busy} onClick={edit}>
          编辑 / 添加图片
        </button>
        {asset.status !== "approved" && (
          <button
            className="button"
            disabled={busy || !rows(asset.data.images).length}
            onClick={() =>
              run(
                () =>
                  request("/library", {
                    action: "approve",
                    project_id: project?.id,
                    asset_id: asset.asset_id,
                    expected_version: asset.version,
                    confirmed: true,
                  }),
                "此版本已确认。",
              )
            }
          >
            确认参考
          </button>
        )}
      </div>
      <StudioMenu
        label="更多素材操作"
        align="start"
        items={[
          ...(project
            ? [
                {
                  key: "clone",
                  label:
                    asset.library_key === "global"
                      ? "复制为剧内造型"
                      : "保存到统一库",
                  disabled: busy,
                  onSelect: () =>
                    run(
                      () =>
                        request("/library", {
                          action: "clone",
                          project_id: project.id,
                          asset_id: asset.asset_id,
                          expected_version: asset.version,
                          scope:
                            asset.library_key === "global"
                              ? "project"
                              : "global",
                        }),
                      "已保存独立副本，来源与原素材保持可追溯。",
                    ),
                },
              ]
            : []),
          {
            key: "describe",
            label: "AI 识图建档",
            disabled: busy || !rows(asset.data.images).length,
            onSelect: () => startAI("describe"),
          },
          ...(project && asset.kind !== "style"
            ? [
                {
                  key: "restyle",
                  label: "按本剧画风转换",
                  disabled: busy,
                  onSelect: () => startAI("restyle"),
                },
              ]
            : []),
          ...(asset.kind === "character"
            ? [
                {
                  key: "views",
                  label: "补充三视图",
                  disabled: busy || !rows(asset.data.images).length,
                  onSelect: () => startAI("views"),
                },
              ]
            : []),
          ...(asset.kind === "composition"
            ? [
                {
                  key: "compose",
                  label: "生成同框候选",
                  disabled: busy,
                  onSelect: () => startAI("compose"),
                },
              ]
            : []),
        ]}
      />
      {project && selected && (
        <div className="stack">
          {asset.kind === "character" && (
            <Field label={document.name + "对应角色"}>
              <StudioSelect
                value={selectedRole}
                onChange={(e) => setRole(e.target.value)}
              >
                {roles.map((r: RecordData) => (
                  <option key={r.character_id} value={r.character_id}>
                    {r.name}（{r.character_id}）
                  </option>
                ))}
              </StudioSelect>
            </Field>
          )}
          {(asset.kind === "scene" || asset.kind === "composition") && (
            <Field label={document.name + "适用镜头"}>
              <StudioSelect
                multiple
                value={shots}
                onChange={(e) =>
                  setShots(
                    Array.from(
                      e.target.selectedOptions,
                      (option) => option.value,
                    ),
                  )
                }
              >
                {rows(selected.shots).map((shot) => (
                  <option key={shot.id} value={shot.id}>
                    {shot.id} · {shot.prompt}
                  </option>
                ))}
              </StudioSelect>
              <small>
                也可由分镜根据素材设定关联，人物按每镜实际出镜角色引用。
              </small>
            </Field>
          )}
          <button
            className="button primary"
            disabled={busy || selectedHere}
            onClick={() =>
              run(
                () => select(asset, selectedRole, shots),
                "已选入本批，集中确认后正式采用。",
              )
            }
          >
            {selectedHere ? "本批已选" : "选入本剧"}
          </button>
        </div>
      )}
      {rows(asset.affected_shots).length > 0 && (
        <details>
          <summary>采用此版的影响范围</summary>
          {rows(asset.affected_shots).map((impact) => (
            <p key={impact.project_id}>
              {impact.project_id}：{impact.shot_ids.join("、")}
            </p>
          ))}
        </details>
      )}
    </Panel>
  );
}

function MaterialEditor({
  asset,
  initialDocument,
  project,
  candidates,
  roles,
  busy,
  save,
  onCancel,
  onKind,
}: {
  asset: RecordData;
  initialDocument?: RecordData;
  project?: RecordData;
  candidates: RecordData[];
  roles: RecordData[];
  busy: boolean;
  save: (
    document: RecordData,
    uploads: File[],
    scope: string,
    uploaded: (file: File) => void,
  ) => Promise<void>;
  onCancel: () => void;
  onKind: (kind: string) => void;
}) {
  const [document, setDocument] = useState<RecordData>(
    initialDocument || asset.data.document,
  );
  const [files, setFiles] = useState<File[]>([]);
  const [scope, setScope] = useState(project ? "project" : "global");
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => {
    form.current
      ?.closest(".panel")
      ?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    form.current
      ?.querySelector<HTMLInputElement>(
        '[aria-label="素材名称"], input[maxlength="100"]',
      )
      ?.focus({ preventScroll: true });
  }, []);
  const edit = (field: string, value: string) =>
    setDocument((old) => ({ ...old, [field]: value }));
  return (
    <Panel
      title={
        asset.asset_id ? "追加素材版本" : "新建" + materialKinds[asset.kind]
      }
    >
      <form
        ref={form}
        className="stack material-editor"
        onSubmit={(e) => {
          e.preventDefault();
          save(document, files, scope, (file) =>
            setFiles((pending) => pending.filter((other) => other !== file)),
          );
        }}
      >
        <fieldset className="material-editor-fields stack" disabled={busy}>
          {!asset.asset_id && (
            <Field label="新素材类型">
              <StudioSelect
                value={asset.kind}
                onChange={(e) => {
                  setDocument({
                    name: document.name,
                    description: document.description || "",
                  });
                  onKind(e.target.value);
                }}
              >
                {Object.entries(materialKinds).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </StudioSelect>
            </Field>
          )}
          <Field label="素材名称">
            <input
              required
              maxLength={100}
              value={document.name}
              onChange={(e) => edit("name", e.target.value)}
            />
          </Field>
          <Field label="素材说明">
            <textarea
              maxLength={4000}
              value={document.description || ""}
              onChange={(e) => edit("description", e.target.value)}
            />
          </Field>
          {asset.kind === "character" && (
            <>
              <Field label="不变的身份与外观">
                <textarea
                  maxLength={4000}
                  value={document.identity_constraints || ""}
                  onChange={(e) => edit("identity_constraints", e.target.value)}
                />
              </Field>
              <Field label="默认服装">
                <input
                  value={document.default_wardrobe || ""}
                  onChange={(e) => edit("default_wardrobe", e.target.value)}
                />
              </Field>
            </>
          )}
          {asset.kind === "style" && (
            <Field label="共同画风规则">
              <textarea
                maxLength={4000}
                value={document.visual_rules || ""}
                onChange={(e) => edit("visual_rules", e.target.value)}
              />
            </Field>
          )}
          {asset.kind === "composition" && (
            <CompositionEditor
              document={document}
              setDocument={setDocument}
              candidates={candidates}
              project={project}
              roles={roles}
              image={rows(asset.data.images)[0]}
            />
          )}
          <Field label="添加参考图片">
            <input
              type="file"
              multiple
              accept="image/png,image/jpeg,image/webp"
              onChange={(e) => {
                const added = Array.from(e.target.files || []);
                setFiles((pending) => [
                  ...pending,
                  ...added.filter(
                    (file) =>
                      !pending.some(
                        (other) =>
                          other.name === file.name &&
                          other.size === file.size &&
                          other.lastModified === file.lastModified,
                      ),
                  ),
                ]);
                e.target.value = "";
              }}
            />
            <small>PNG/JPEG/WebP，单张不超过8 MiB，64–4096像素。</small>
          </Field>
          {files.length > 0 && (
            <div className="material-pending-files" aria-live="polite">
              <p>待上传 {files.length} 张；保存失败后可继续上传剩余图片。</p>
              {files.map((file, index) => (
                <div className="row-between" key={index}>
                  <span>{file.name}</span>
                  <button
                    type="button"
                    className="button"
                    onClick={() =>
                      setFiles((pending) =>
                        pending.filter((other) => other !== file),
                      )
                    }
                    aria-label={"移除待上传图片 " + file.name}
                  >
                    移除
                  </button>
                </div>
              ))}
            </div>
          )}
          {asset.asset_id && (
            <p className="muted">
              文字和 {rows(asset.data.images).length} 张图片已保存至 v
              {asset.version}。
            </p>
          )}
          {!asset.asset_id && project && (
            <Field label="保存位置">
              <StudioSelect
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              >
                <option value="project">本剧素材库</option>
                <option value="global">统一素材库</option>
              </StudioSelect>
            </Field>
          )}
          <div className="form-row">
            <button className="button primary" type="submit" disabled={busy}>
              {busy ? "正在保存…" : "保存素材"}
            </button>
            <button
              className="button"
              type="button"
              disabled={busy}
              onClick={onCancel}
            >
              取消
            </button>
          </div>
        </fieldset>
      </form>
    </Panel>
  );
}

function MaterialTask({
  task,
  project,
  busy,
  run,
  applyDraft,
}: {
  task: RecordData;
  project?: RecordData;
  busy: boolean;
  run: (work: () => Promise<unknown>, message: string) => Promise<void>;
  applyDraft: () => Promise<void>;
}) {
  const [unpriced, setUnpriced] = useState(false);
  const states: Record<string, string> = {
    queued: "排队中",
    running: "制作中",
    waiting_authorization: "等待费用授权",
    completed: "候选已完成",
    failed: "失败",
    needs_attention: "需要核对",
    paused: "已暂停",
    pause_requested: "正在暂停",
  };
  const control = (action: string) =>
    run(
      () =>
        request("/library/tasks/" + task.id, {
          action,
          project_id: project?.id,
          expected_lease: task.lease,
          signature: task.signature,
          confirmed: true,
          allow_unpriced: unpriced,
        }),
      "任务操作已记录。",
    );
  return (
    <article className="material-task stack">
      <strong>
        {task.name} · {states[task.status] || task.status}
      </strong>
      <small>
        {task.model} · {task.id}
      </small>
      <ErrorNotice error={task.error} />
      {task.result.review && (
        <p>
          视觉审核：{task.result.review.status} · {task.result.review.summary}
        </p>
      )}
      {task.status === "waiting_authorization" && (
        <>
          <p>
            本批最多提交 {task.quote.submissions} 次；
            {task.quote.estimated_total_micros === null
              ? "供应商价格未知"
              : "估算 " +
                (task.quote.estimated_total_micros / 1000000).toFixed(2) +
                " " +
                task.quote.currency}
            。估算不代表供应商账单上限。
          </p>
          {task.quote.estimated_total_micros === null && (
            <label className="check">
              <input
                type="checkbox"
                checked={unpriced}
                onChange={(e) => setUnpriced(e.target.checked)}
              />
              我接受本批价格未知，按限定次数提交
            </label>
          )}
          <button
            className="button"
            disabled={
              busy || (task.quote.estimated_total_micros === null && !unpriced)
            }
            onClick={() => control("authorize")}
          >
            授权此批素材生成
          </button>
        </>
      )}
      {task.result.document && (
        <>
          <p>{task.result.document.description}</p>
          <button className="button" disabled={busy} onClick={applyDraft}>
            采用识图草稿并编辑
          </button>
        </>
      )}
      {task.result.asset_id && (
        <p>候选已保存到素材库，确认并选入本剧后使用。</p>
      )}
      {["queued", "running"].includes(task.status) && (
        <button
          className="button"
          disabled={busy}
          onClick={() => control("pause")}
        >
          暂停素材任务
        </button>
      )}
      {["paused", "failed", "needs_attention"].includes(task.status) && (
        <button
          className="button"
          disabled={busy}
          onClick={() => control("resume")}
        >
          恢复原任务
        </button>
      )}
    </article>
  );
}

function CompositionEditor({
  document,
  setDocument,
  candidates,
  project,
  roles,
  image,
}: {
  document: RecordData;
  setDocument: (value: RecordData) => void;
  candidates: RecordData[];
  project?: RecordData;
  image?: RecordData;
  roles: RecordData[];
}) {
  const [active, setActive] = useState(0);
  const [start, setStart] = useState<number[] | null>(null);
  const [end, setEnd] = useState<number[] | null>(null);
  const [memberSearch, setMemberSearch] = useState("");
  const lookup = useQuery({
    queryKey: ["composition-candidates", project?.id, memberSearch],
    queryFn: () =>
      request(
        "/library?" +
          new URLSearchParams({
            q: memberSearch,
            ...(project ? { project_id: project.id } : {}),
          }),
      ),
  });
  const members = rows(document.members);
  const available = lookup.data ? rows(lookup.data.assets) : candidates;
  const characters = available.filter((asset) => asset.kind === "character");
  const scenes = available.filter((asset) => asset.kind === "scene");
  const updateMember = (index: number, patch: RecordData) =>
    setDocument({
      ...document,
      members: members.map((member, i) =>
        i === index ? { ...member, ...patch } : member,
      ),
    });
  const point = (event: React.PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
      Math.max(0, Math.min(1, (event.clientY - box.top) / box.height)),
    ];
  };
  return (
    <div className="stack">
      <p>
        关联同框人物和稳定角色；上传同框图片后，可再次编辑并拖动框选人物区域。
      </p>
      <ErrorNotice error={lookup.error} />
      <Field label="查找组合人物或场景">
        <input
          value={memberSearch}
          onChange={(e) => setMemberSearch(e.target.value)}
          placeholder="按名称搜索，选择后可继续搜索其他素材"
        />
      </Field>
      {members.map((member, index) => (
        <div key={index} className="form-row">
          <Field label={"同框人物" + (index + 1)}>
            <StudioSelect
              value={member.asset_id + ":" + member.version}
              onChange={(e) => {
                const [asset_id, version] = e.target.value.split(":");
                updateMember(index, { asset_id, version: Number(version) });
              }}
            >
              {!characters.some(
                (asset) =>
                  asset.asset_id === member.asset_id &&
                  asset.version === member.version,
              ) && (
                <option value={member.asset_id + ":" + member.version}>
                  {member.asset_id} · v{member.version}
                </option>
              )}
              {characters.map((asset) => (
                <option
                  key={asset.asset_id}
                  value={asset.asset_id + ":" + asset.version}
                >
                  {asset.data.document.name} · v{asset.version}
                </option>
              ))}
            </StudioSelect>
          </Field>
          <Field label={"对应角色" + (index + 1)}>
            <div className="studio-role-input">
              <input
                value={member.role}
                onChange={(e) => updateMember(index, { role: e.target.value })}
              />
              {!!roles.length && (
                <StudioMenu
                  label="已有角色"
                  ariaLabel={"选择对应角色" + (index + 1)}
                  items={roles.map((role: RecordData) => ({
                    key: role.character_id,
                    label: role.name + "（" + role.character_id + "）",
                    selected: member.role === role.character_id,
                    onSelect: () =>
                      updateMember(index, { role: role.character_id }),
                  }))}
                />
              )}
            </div>
          </Field>
          <button
            type="button"
            className="button"
            aria-pressed={active === index}
            data-region-target={index}
            onClick={() => {
              setActive(index);
              setStart(null);
              setEnd(null);
            }}
          >
            框选此人物
          </button>
          <fieldset className="region-coordinates">
            <legend>{"人物" + (index + 1) + "区域坐标（0–1）"}</legend>
            {(["左", "上", "右", "下"] as const).map((label, coordinate) => (
              <Field key={label} label={"人物" + (index + 1) + label + "边界"}>
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="any"
                  value={member.box?.[coordinate] ?? (coordinate < 2 ? 0 : 1)}
                  onChange={(event) => {
                    if (event.target.value === "") return;
                    const box = [...(member.box || [0, 0, 1, 1])];
                    box[coordinate] = Math.max(
                      0,
                      Math.min(1, Number(event.target.value)),
                    );
                    updateMember(index, { box });
                  }}
                />
              </Field>
            ))}
            <button
              type="button"
              className="button small"
              disabled={!member.box}
              onClick={() => {
                const next = { ...member };
                delete next.box;
                setDocument({
                  ...document,
                  members: members.map((value, i) =>
                    i === index ? next : value,
                  ),
                });
                setStart(null);
                setEnd(null);
              }}
            >
              清除框选
            </button>
          </fieldset>
          <button
            type="button"
            className="button"
            onClick={() => {
              setStart(null);
              setEnd(null);
              setActive((current) =>
                Math.max(0, Math.min(current, members.length - 2)),
              );
              setDocument({
                ...document,
                members: members.filter((_, i) => i !== index),
              });
            }}
          >
            移除
          </button>
        </div>
      ))}

      <button
        type="button"
        className="button"
        disabled={members.length >= 4 || !characters.length}
        onClick={() =>
          setDocument({
            ...document,
            members: [
              ...members,
              {
                asset_id: characters[0].asset_id,
                version: characters[0].version,
                role:
                  roles[members.length]?.character_id ||
                  "character_" + (members.length + 1),
              },
            ],
          })
        }
      >
        添加同框人物
      </button>
      <Field label="组合中的场景">
        <StudioSelect
          value={
            document.scene
              ? document.scene.asset_id + ":" + document.scene.version
              : ""
          }
          onChange={(e) => {
            const next = { ...document };
            if (!e.target.value) delete next.scene;
            else {
              const [asset_id, version] = e.target.value.split(":");
              next.scene = { asset_id, version: Number(version) };
            }
            setDocument(next);
          }}
        >
          <option value="">未指定</option>
          {document.scene &&
            !scenes.some(
              (scene) =>
                scene.asset_id === document.scene.asset_id &&
                scene.version === document.scene.version,
            ) && (
              <option
                value={document.scene.asset_id + ":" + document.scene.version}
              >
                {document.scene.asset_id} · v{document.scene.version}
              </option>
            )}
          {scenes.map((asset) => (
            <option
              key={asset.asset_id}
              value={asset.asset_id + ":" + asset.version}
            >
              {asset.data.document.name}
            </option>
          ))}
        </StudioSelect>
      </Field>
      {image && (
        <>
          <p role="status">
            {members[active]
              ? "正在标注：" + members[active].role
              : "请先添加同框人物"}
          </p>
          <div className="material-region">
            <img
              src={mediaUrl("/media/" + image.path)}
              alt="同框人物区域标注"
            />
            <svg
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              aria-label="拖动框选同框人物"
              onPointerDown={(e) => {
                if (!members[active]) return;
                e.currentTarget.setPointerCapture(e.pointerId);
                setStart(point(e));
                setEnd(point(e));
              }}
              onPointerMove={(e) => {
                if (start) setEnd(point(e));
              }}
              onPointerCancel={() => {
                setStart(null);
                setEnd(null);
              }}
              onPointerUp={(e) => {
                if (!start || !members[active]) return;
                const end = point(e);
                if (
                  Math.abs(end[0] - start[0]) > 0.01 &&
                  Math.abs(end[1] - start[1]) > 0.01
                )
                  updateMember(active, {
                    box: [
                      Math.min(start[0], end[0]),
                      Math.min(start[1], end[1]),
                      Math.max(start[0], end[0]),
                      Math.max(start[1], end[1]),
                    ],
                  });
                setStart(null);
                setEnd(null);
              }}
            >
              {start && end && (
                <rect
                  className="region-draft"
                  x={Math.min(start[0], end[0]) * 100}
                  y={Math.min(start[1], end[1]) * 100}
                  width={Math.abs(end[0] - start[0]) * 100}
                  height={Math.abs(end[1] - start[1]) * 100}
                  fill="transparent"
                  stroke="currentColor"
                  strokeWidth=".7"
                  strokeDasharray="2 1"
                />
              )}
              {members.map(
                (member, index) =>
                  member.box && (
                    <g key={index}>
                      <rect
                        x={member.box[0] * 100}
                        y={member.box[1] * 100}
                        width={(member.box[2] - member.box[0]) * 100}
                        height={(member.box[3] - member.box[1]) * 100}
                        fill="transparent"
                        className={
                          index === active ? "region-active" : "region-saved"
                        }
                        stroke="currentColor"
                        strokeWidth=".7"
                      />
                      <text
                        x={member.box[0] * 100 + 1}
                        y={member.box[1] * 100 + 4}
                        fill="currentColor"
                        fontSize="3"
                      >
                        {member.role}
                      </text>
                    </g>
                  ),
              )}
            </svg>
          </div>
        </>
      )}
    </div>
  );
}
