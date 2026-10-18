import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { Menu, MoreHorizontal, RefreshCw } from "lucide-react";
import { ThemeControl } from "./Theme";
import { StatusIndicator } from "./StatusIndicator";

type HeaderTools = {
  navigationOpen: boolean;
  navigationTrigger: RefObject<HTMLButtonElement | null>;
  toggleNavigation: () => void;
  syncError: boolean;
  syncing: boolean;
  refresh: () => void;
};
const ToolsContext = createContext<HeaderTools | null>(null);
export const PageHeaderProvider = ToolsContext.Provider;

function WorkspaceTools({ tools }: { tools: HeaderTools }) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const syncLabel = tools.syncError
    ? "同步中断"
    : tools.syncing
      ? "正在同步"
      : "已同步";
  return (
    <div
      className="page-tools"
      ref={container}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node))
          setOpen(false);
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="icon-button page-tools-trigger"
        aria-label="页面工具"
        title="页面工具"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        <MoreHorizontal size={20} aria-hidden="true" />
        {tools.syncError && (
          <span className="page-tools-alert">
            <StatusIndicator status="failed" />
          </span>
        )}
      </button>
      <div
        id={id}
        className={"page-tools-content" + (open ? " is-open" : "")}
        role="group"
        aria-label="页面工具操作"
      >
        <ThemeControl />
        <span
          className={
            "sync-state " + (tools.syncError ? "error-text" : "sync-compact")
          }
          title={syncLabel}
          role="status"
        >
          <StatusIndicator
            status={
              tools.syncError
                ? "failed"
                : tools.syncing
                  ? "running"
                  : "succeeded"
            }
          />
          <span className={tools.syncError ? "" : "sr-only"}>{syncLabel}</span>
        </span>
        <button
          type="button"
          className="icon-button"
          data-action="refresh"
          aria-label="刷新项目"
          title="刷新"
          onClick={tools.refresh}
        >
          <RefreshCw size={20} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  action,
  parent,
  meta,
  className = "page-heading",
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  parent?: ReactNode;
  meta?: ReactNode;
  className?: string;
}) {
  const tools = useContext(ToolsContext);
  return (
    <header className={className + " shared-page-header"}>
      <div className="header-title-row">
        {tools && (
          <button
            ref={tools.navigationTrigger}
            type="button"
            className="icon-button mobile-navigation"
            data-action="toggle-navigation"
            aria-label={tools.navigationOpen ? "关闭导航" : "展开导航"}
            aria-expanded={tools.navigationOpen}
            aria-controls="studio-navigation"
            onClick={tools.toggleNavigation}
          >
            <Menu size={20} aria-hidden="true" />
          </button>
        )}
        {parent}
        <h1 title={title}>
          <span className="header-title-text">{title}</span>
        </h1>
      </div>
      {action && <div className="header-business-actions">{action}</div>}
      {tools && <WorkspaceTools tools={tools} />}
      {description && <p className="header-description">{description}</p>}
      {meta && <div className="header-meta">{meta}</div>}
    </header>
  );
}
