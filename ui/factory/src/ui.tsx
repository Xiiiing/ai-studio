import {
  useEffect,
  useRef,
  useId,
  Children,
  isValidElement,
  cloneElement,
  Fragment,
  type ReactNode,
  type ReactElement,
} from "react";
import { X, AlertCircle, Inbox, ArrowUpRight } from "lucide-react";
import { names, text, mediaUrl, ownedMedia, type RecordData } from "./api";
export function Badge({ status, label }: { status?: string; label?: string }) {
  return (
    <span className={"badge tone-" + status}>
      <i />
      {label || names[status || ""] || status || "未记录"}
    </span>
  );
}
export function Panel({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={"panel " + className}>
      {title && (
        <header className="panel-head">
          <div>
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
export function Heading({
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action}
    </header>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Inbox size={28} />
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
export function ErrorNotice({
  error,
  children,
}: {
  error?: any;
  children?: ReactNode;
}) {
  return error ? (
    <div className="notice error" role="alert">
      <AlertCircle size={18} />
      <div>
        {error instanceof Error ? error.message : text(error)}
        {children}
      </div>
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      正在读取制作记录…
    </div>
  );
}
export function Modal({
  id,
  title,
  children,
  onClose,
}: {
  id: string;
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    d?.showModal();
    return () => {
      d?.close();
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog ref={ref} id={id} aria-labelledby={titleId} onCancel={onClose}>
      <header className="dialog-head">
        <h2 id={titleId}>{title}</h2>
        <button
          type="button"
          className="icon-button"
          aria-label="关闭"
          data-action="close-dialog"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </header>
      <div className="dialog-body">{children}</div>
    </dialog>
  );
}
const labels: Record<string, string> = {
  title: "标题",
  logline: "一句话故事",
  synopsis: "故事梗概",
  characters: "人物",
  cast: "人物设定",
  shots: "分镜",
  dialogue: "对白",
  text: "内容",
  speaker: "角色",
  action: "动作",
  description: "描述",
  motivation: "人物动机",
  conflict: "冲突",
  ending: "结局",
  hook: "开场钩子",
  score: "评分",
  status: "状态",
  issues: "问题",
  suggestion: "修改建议",
  evidence: "依据",
  summary: "结论",
  passed: "通过",
  seconds: "秒数",
  world: "世界观",
  visual_rules: "视觉规范",
  scenes: "场景",
  props: "道具",
  name: "名称",
  dramatic_beat: "剧情节拍",
  camera_motion: "运镜",
  identity_constraints: "身份约束",
  default_wardrobe: "默认服装",
  technical: "技术检查",
  semantic: "画面审核",
  character_names: "人物姓名",
  char_lead: "主角",
  char_partner: "搭档",
  story_structure: "故事结构",
  setup: "开场设定",
  inciting_incident: "诱发事件",
  turning_point: "剧情转折",
  climax: "高潮",
  resolution: "结局落点",
  width: "画面宽度",
  height: "画面高度",
  fps: "帧率",
  duration_seconds: "时长",
  severity: "严重程度",
  detail: "问题说明",
};
export function Readable({ value, depth = 0 }: { value: any; depth?: number }) {
  if (value == null || value === "")
    return <span className="muted">尚未生成</span>;
  if (typeof value === "boolean") return <span>{value ? "是" : "否"}</span>;
  if (typeof value !== "object")
    return <span className="preserve-lines">{text(value)}</span>;
  if (depth > 4) return <pre>{text(value)}</pre>;
  if (Array.isArray(value))
    return (
      <div className="readable-list">
        {value.map((v, i) => (
          <div key={i}>
            <Readable value={v} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  return (
    <dl className="readable">
      {Object.entries(value)
        .filter(
          ([k]) =>
            !/^(api_key|token|secret|prompt|negative_prompt|seed|model)$/i.test(
              k,
            ),
        )
        .map(([k, v]) => (
          <div key={k}>
            <dt>{labels[k] || k}</dt>
            <dd>
              <Readable value={v} depth={depth + 1} />
            </dd>
          </div>
        ))}
    </dl>
  );
}
export function Artifact({
  item,
  projectId,
}: {
  item: RecordData;
  projectId?: string;
}) {
  const raw =
    item.artifact_url ||
    item.url ||
    item.download_url ||
    (item.path?.startsWith("production/") ? "/media/" + item.path : item.path);
  const href = projectId ? ownedMedia(projectId, raw) : mediaUrl(raw);
  return href ? (
    <a
      className="artifact-link"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
    >
      {item.label || item.name || "查看产物"}
      <ArrowUpRight size={14} />
    </a>
  ) : (
    <span className="muted">{item.label || item.name || "暂无产物"}</span>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  const generated = useId();
  let controlId = generated;
  function connect(nodes: ReactNode): ReactNode {
    return Children.map(nodes, (node) => {
      if (!isValidElement(node)) return node;
      const element = node as ReactElement<any>;
      if (element.type === Fragment)
        return cloneElement(element, {}, connect(element.props.children));
      if (
        typeof element.type === "string" &&
        ["input", "textarea", "select"].includes(element.type)
      ) {
        controlId = element.props.id || generated;
        return cloneElement(element, {
          id: controlId,
          "aria-describedby": hint
            ? generated + "-hint"
            : element.props["aria-describedby"],
        });
      }
      if (
        typeof element.type === "string" &&
        element.type !== "label" &&
        element.props.children
      )
        return cloneElement(element, {}, connect(element.props.children));
      return node;
    });
  }
  const controls = connect(children);
  return (
    <div className="field">
      <label htmlFor={controlId}>{label}</label>
      {controls}
      {hint && <small id={generated + "-hint"}>{hint}</small>}
    </div>
  );
}
