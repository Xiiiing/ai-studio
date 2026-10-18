import { lazy, Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Maximize2, FileText, RotateCcw } from "lucide-react";
import { rows, text, roleName, date, type RecordData } from "./api";
import { documentUrl } from "./DocumentPath";
export { documentUrl } from "./DocumentPath";
import { Badge, Modal, Readable } from "./ui";
import { ContextManifest } from "./ContextManifest";

export const MAX_PREVIEW_BYTES = 2 * 1024 * 1024;
class PreviewError extends Error {
  constructor(
    message: string,
    public tooLarge = false,
  ) {
    super(message);
  }
}
export async function readDocument(url: string, signal?: AbortSignal) {
  const response = await fetch(url, {
    signal,
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
  });
  if (!response.ok)
    throw new PreviewError(
      response.status === 404
        ? "文件缺失，可能尚未保存或已被移除。"
        : "文件读取失败，请重试。",
    );
  if (Number(response.headers.get("Content-Length")) > MAX_PREVIEW_BYTES) {
    await response.body?.cancel();
    throw new PreviewError("文件超过 2 MiB 预览上限，请下载原文件阅读。", true);
  }
  if (!response.body) throw new PreviewError("文件读取失败，请重试。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0,
    result = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_PREVIEW_BYTES) {
        await reader.cancel();
        throw new PreviewError(
          "文件超过 2 MiB 预览上限，请下载原文件阅读。",
          true,
        );
      }
      result += decoder.decode(value, { stream: true });
    }
    return result + decoder.decode();
  } finally {
    reader.releaseLock();
  }
}
const MarkdownBody = lazy(() => import("./MarkdownBody"));
export function DocumentValue({
  value,
  projectId,
  baseUrl,
  raw = false,
}: {
  value: any;
  projectId?: string;
  baseUrl?: string;
  raw?: boolean;
}) {
  if (raw) return <pre className="document-raw">{text(value)}</pre>;
  if (typeof value === "string")
    return (
      <Suspense fallback={<p role="status">正在准备文档排版…</p>}>
        <MarkdownBody value={value} projectId={projectId} baseUrl={baseUrl} />
      </Suspense>
    );
  // Execution JSON deliberately retains prompt/messages fields hidden by the business summary component.
  if (value && typeof value === "object")
    return (
      <div className="document-json">
        {Object.entries(value).map(([key, v]) => (
          <section key={key}>
            <h5>{key}</h5>
            {typeof v === "object" && v !== null ? (
              <pre className="document-raw">{text(v)}</pre>
            ) : (
              <DocumentValue
                value={v}
                projectId={projectId}
                baseUrl={baseUrl}
              />
            )}
          </section>
        ))}
      </div>
    );
  return <span>{text(value)}</span>;
}
function MediaPreview({
  url,
  media,
  label,
}: {
  url: string;
  media: string;
  label: string;
}) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  if (failed)
    return (
      <div role="alert">
        <p>媒体读取失败，文件可能缺失或格式无法预览。</p>
        <button
          className="button small"
          onClick={() => {
            setAttempt((value) => value + 1);
            setFailed(false);
          }}
        >
          <RotateCcw size={14} />
          重新读取
        </button>
      </div>
    );
  const props = {
    className: "document-media",
    src: url + (attempt ? "?reader_retry=" + attempt : ""),
    onError: () => setFailed(true),
  };
  if (media === "image") return <img {...props} alt={label} />;
  if (media === "video")
    return <video {...props} controls preload="metadata" />;
  return <audio {...props} controls preload="metadata" />;
}
export function DocumentReader({
  item,
  projectId,
}: {
  item: RecordData;
  projectId?: string;
}) {
  const [raw, setRaw] = useState(false),
    [expanded, setExpanded] = useState(false);
  const url = documentUrl(
    projectId,
    item.artifact_url || item.url || item.path,
  );
  const extension = url.split(".").pop()?.toLowerCase() || "";
  const media = /^(png|jpe?g|webp|gif)$/.test(extension)
    ? "image"
    : /^(mp4|webm)$/.test(extension)
      ? "video"
      : /^(wav|mp3|ogg|m4a)$/.test(extension)
        ? "audio"
        : "";
  const supported = /^(md|markdown|json|txt|csv|srt|vtt|log)$/.test(extension);
  const content = useQuery({
    queryKey: ["document", projectId, url, item.version || item.revision || ""],
    queryFn: ({ signal }) => readDocument(url, signal),
    enabled: !!url && !media && supported,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[1] === projectId &&
      previousQuery?.queryKey[2] === url
        ? previous
        : undefined,
  });
  const renderBody = () => {
    if (!url) return <p className="muted">未记录可访问的项目文件。</p>;
    if (media)
      return (
        <MediaPreview
          key={url}
          url={url}
          media={media}
          label={item.label || "项目图片"}
        />
      );
    if (!supported) return <p className="muted">此文件支持下载后查看。</p>;
    if (content.isPending) return <p role="status">正在读取文件…</p>;
    if (content.error)
      return (
        <div role="alert">
          <p>{content.error.message}</p>
          {!(content.error as PreviewError).tooLarge && (
            <button className="button small" onClick={() => content.refetch()}>
              <RotateCcw size={14} />
              重新读取
            </button>
          )}
        </div>
      );
    const source = content.data || "";
    if (extension === "json") {
      if (raw) return <pre className="document-raw">{source}</pre>;
      try {
        return (
          <DocumentValue
            value={JSON.parse(source)}
            raw={raw}
            projectId={projectId}
            baseUrl={url}
          />
        );
      } catch {
        return (
          <>
            <p className="muted">JSON 格式无法解析，显示保存的原文。</p>
            <pre className="document-raw">{source}</pre>
          </>
        );
      }
    }
    return extension === "md" || extension === "markdown" ? (
      <DocumentValue
        value={source}
        raw={raw}
        projectId={projectId}
        baseUrl={url}
      />
    ) : (
      <pre className="document-raw">{source}</pre>
    );
  };
  const toolbar = (
    <div className="document-toolbar">
      {!media && supported && (
        <div className="document-view-toggle" aria-label="阅读方式">
          <button
            className={!raw ? "active" : ""}
            onClick={() => setRaw(false)}
          >
            正文
          </button>
          <button className={raw ? "active" : ""} onClick={() => setRaw(true)}>
            原文
          </button>
        </div>
      )}
      <span className="document-actions">
        <button className="text-link" onClick={() => setExpanded(true)}>
          <Maximize2 size={14} />
          放大阅读
        </button>
        {url && (
          <a className="text-link" href={url} download>
            <Download size={14} />
            下载原文件
          </a>
        )}
      </span>
    </div>
  );
  return (
    <div className="document-reader">
      {toolbar}
      <div className="document-content">{renderBody()}</div>
      {expanded && (
        <Modal
          id="document-expanded"
          title={item.label || "文件阅读"}
          onClose={() => setExpanded(false)}
        >
          <div className="document-expanded">
            {toolbar}
            <div className="document-content">{renderBody()}</div>
          </div>
        </Modal>
      )}
    </div>
  );
}
export function MaterialCard({
  item,
  projectId,
  onSource,
}: {
  item: RecordData;
  projectId?: string;
  onSource?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const hasFile = !!documentUrl(
    projectId,
    item.artifact_url || item.url || item.path,
  );
  const hasValue =
    item.value !== undefined &&
    item.value !== null &&
    item.value !== "" &&
    !(typeof item.value === "object" && Object.keys(item.value).length === 0);
  return (
    <article className="material-card">
      <header>
        <strong>{item.label || "材料"}</strong>
        {hasFile && (
          <button
            className="text-link"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            <FileText size={14} />
            {open ? "收起文件" : "阅读文件"}
          </button>
        )}
      </header>
      {item.source_label && (
        <small className="material-source">
          {item.source_label}
          {item.source_node && onSource && (
            <button
              className="text-link"
              onClick={() => onSource(item.source_node)}
            >
              查看来源步骤
            </button>
          )}
        </small>
      )}
      {hasValue && (
        <div className="material-value">
          {typeof item.value === "object" ? (
            <Readable value={item.value} />
          ) : (
            <DocumentValue value={item.value} projectId={projectId} />
          )}
        </div>
      )}
      {!hasValue && !hasFile && (
        <p className="muted">
          {item.truncated
            ? "内容较长，请查看源文件。"
            : item.artifact_state === "missing"
              ? "文件缺失，尚无可读取的原文件。"
              : item.state === "not_generated"
                ? "尚未生成"
                : "未记录"}
        </p>
      )}
      {hasValue && item.artifact_state === "missing" && (
        <p className="muted">源文件缺失，以上为当前保存的材料。</p>
      )}
      {open && <DocumentReader item={item} projectId={projectId} />}
    </article>
  );
}
export function ExecutionRecord({
  execution,
  role,
  projectId,
  onLocate,
}: {
  execution: RecordData;
  role: string;
  projectId?: string;
  onLocate?: (step: string, execution: string) => void;
}) {
  return (
    <article className="execution-record" data-execution={execution.id}>
      <header>
        <strong>
          {roleName(role)} · {execution.model || "模型未记录"}
        </strong>
        <Badge status={execution.status} />
      </header>
      <p className="muted">
        {execution.scope === "current_run"
          ? "当前步骤执行"
          : execution.stage
            ? "历史步骤执行"
            : "角色的项目历史 · 未记录步骤归属"}{" "}
        · {date(execution.started_at)}
      </p>
      <ContextManifest value={execution.context_manifest} reused={execution.reused} originCall={execution.origin_call_id} />
      {execution.stage && onLocate && (
        <button
          className="text-link"
          onClick={() => onLocate(execution.stage, execution.id)}
        >
          定位步骤与记录
        </button>
      )}
      {rows(execution.attempts).map((attempt) => (
        <details key={attempt.attempt}>
          <summary>第 {attempt.attempt} 次请求与响应</summary>
          {rows(attempt.artifacts).map((item, i) => (
            <MaterialCard key={i} item={item} projectId={projectId} />
          ))}
        </details>
      ))}
      {rows(execution.artifacts).map((item, i) => (
        <MaterialCard key={i} item={item} projectId={projectId} />
      ))}
      {!rows(execution.artifacts).length && execution.receipt && (
        <MaterialCard
          item={{ label: "执行回执", artifact_url: execution.receipt }}
          projectId={projectId}
        />
      )}
      {!execution.receipt &&
        !rows(execution.artifacts).length &&
        !rows(execution.attempts).length && (
          <p className="muted">未记录请求或响应文件。</p>
        )}
    </article>
  );
}
