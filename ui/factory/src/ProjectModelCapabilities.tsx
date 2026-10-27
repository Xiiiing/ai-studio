import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request, type RecordData } from "./api";
import { ErrorNotice, Loading, Panel } from "./ui";
import { ModelCapabilities } from "./ModelCapabilities";

const kinds = { image: "图像", video: "视频", audio: "配音" } as const;

function FrozenProfiles({ snapshot }: { snapshot?: RecordData | null }) {
  const profiles = snapshot?.profiles;
  if (!profiles || typeof profiles !== "object")
    return <p className="muted">没有记录能力快照，不能用当前配置补写历史。</p>;
  return (
    <div className="stack" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
      {Object.entries(kinds).map(([kind, label]) =>
        profiles[kind] ? (
          <div key={kind}>
            <h4>
              {label} · {String(profiles[kind].model || "未记录模型")}
            </h4>
            <ModelCapabilities value={profiles[kind]} />
          </div>
        ) : (
          <p key={kind}>{label}能力未记录</p>
        ),
      )}
    </div>
  );
}

export function ProjectModelCapabilities({ project }: { project: RecordData }) {
  return <ProjectCapabilitySettings key={project.id} project={project} />;
}

function ProjectCapabilitySettings({ project }: { project: RecordData }) {
  const client = useQueryClient();
  const path = `/projects/${project.id}/model-capabilities`;
  const key = ["project-model-capabilities", project.id];
  const query = useQuery({
    queryKey: key,
    queryFn: () => request(path),
    refetchInterval: 5000,
  });
  const [confirmedAt, setConfirmedAt] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<RecordData | null>(null);
  const sending = useRef(false);
  const data = query.data;
  const confirmed = confirmedAt !== null && confirmedAt === data?.revision;
  const snapshot = data?.snapshot;
  const history = Array.isArray(data?.history) ? data.history : [];
  const allowed = data?.can_adopt === true && !query.isError;

  async function adopt(retry = false) {
    if (sending.current || (!retry && (!allowed || !confirmed || pending)))
      return;
    const body =
      retry && pending
        ? pending
        : {
            expected_revision: confirmedAt,
            request_id: crypto.randomUUID().replaceAll("-", ""),
            confirmed: true,
          };
    if (retry && !pending) return;
    sending.current = true;
    setBusy(true);
    setPending(body);
    setError("");
    try {
      await request(path, body);
      setPending(null);
      setConfirmedAt(null);
      await Promise.all(
        [
          key,
          ["workspace", project.id],
          ["project", project.id],
          ["drama", project.id],
          ["long-episode", project.id],
        ].map((queryKey) => client.invalidateQueries({ queryKey })),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <Panel
      title="模型能力与版本"
      description="查看本项目固定的适配器能力，在空闲时明确启用或更新。"
    >
      <div className="stack" data-project-model-capabilities>
        <ErrorNotice error={error || query.error} />
        {query.isPending ? (
          <Loading />
        ) : !data ? (
          <>
            <p className="muted">
              暂时无法读取项目能力记录，尚不能判断当前采用版本。
            </p>
            <button
              type="button"
              className="button"
              onClick={() => void query.refetch()}
            >
              重新读取能力记录
            </button>
          </>
        ) : (
          <>
            <p>
              {data?.enabled
                ? "本项目使用固定的模型能力快照。"
                : "本项目沿用原制作合约，尚未启用能力快照。"}
            </p>
            <p className="muted">
              启用或更新只检查此项目后续制作参数，不启动模型、生成任务或费用授权；原稿、素材和审核历史保留。
            </p>
            <details open={!!snapshot}>
              <summary>本项目当前能力</summary>
              <FrozenProfiles snapshot={snapshot} />
            </details>
            {data?.candidate && (
              <details>
                <summary>准备采用的能力</summary>
                <FrozenProfiles snapshot={data.candidate} />
              </details>
            )}
            {!allowed && (
              <p className="muted">
                {String(
                  data?.blocked_reason ||
                    "当前不能更新能力，请先暂停并核对在途任务。",
                )}
              </p>
            )}
            <label className="check">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={!allowed || busy || !!pending}
                onChange={(event) =>
                  setConfirmedAt(
                    event.target.checked && typeof data?.revision === "number"
                      ? data.revision
                      : null,
                  )
                }
              />
              我确认采用此项目当前配置的能力，已有成果保留
            </label>
            <div className="pipeline-actions">
              <button
                type="button"
                className="button primary"
                disabled={!allowed || !confirmed || busy || !!pending}
                onClick={() => void adopt()}
              >
                {busy
                  ? "正在保存…"
                  : data?.enabled
                    ? "更新本项目能力版本"
                    : "启用本项目能力校验"}
              </button>
              {pending && !busy && (
                <>
                  <button
                    type="button"
                    className="button"
                    onClick={() => void adopt(true)}
                  >
                    重试此操作
                  </button>
                  <button
                    type="button"
                    className="button"
                    onClick={() => {
                      setPending(null);
                      setConfirmedAt(null);
                      setError("");
                      void client.invalidateQueries({ queryKey: key });
                    }}
                  >
                    刷新后重新准备
                  </button>
                </>
              )}
            </div>
            <details>
              <summary>历史能力版本（{history.length}）</summary>
              {history.length ? (
                history.map((record: RecordData, index: number) => (
                  <details key={index}>
                    <summary>历史版本 {index + 1}</summary>
                    <FrozenProfiles snapshot={record} />
                  </details>
                ))
              ) : (
                <p className="muted">尚无已替换的能力版本。</p>
              )}
            </details>
          </>
        )}
      </div>
    </Panel>
  );
}
