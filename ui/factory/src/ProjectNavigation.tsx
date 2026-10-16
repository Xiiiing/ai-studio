import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, Check } from "lucide-react";

export const projectPages = [
  ["overview", "制作概览"],
  ["decisions", "待办决策"],
  ["team", "团队与回执"],
  ["shots", "镜头制作台"],
  ["script", "剧本与分镜"],
  ["quality", "制作与质检"],
  ["delivery", "成片交付"],
  ["series", "系列资产"],
  ["materials", "素材与定稿"],
  ["costs", "云任务与费用"],
] as const;
const groups = [
  { label: "创作与制作", pages: ["overview", "script", "materials", "shots"] },
  { label: "审核与交付", pages: ["decisions", "quality", "delivery"] },
  { label: "项目管理", pages: ["team", "series", "costs"] },
];

export function ProjectNavigation({
  tab,
  attention,
  onTab,
}: {
  tab: string;
  attention?: boolean;
  onTab: (tab: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [menuHeight, setMenuHeight] = useState(520);
  const ref = useRef<HTMLDivElement>(null),
    trigger = useRef<HTMLButtonElement>(null),
    menuId = useId();
  useEffect(() => {
    setOpen(false);
  }, [tab]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    ref.current
      ?.querySelector<HTMLButtonElement>(
        '.project-navigation-menu [aria-current="page"]',
      )
      ?.focus();
    const fitMenu = () => {
      const bottom = ref.current?.getBoundingClientRect().bottom || 0;
      setMenuHeight(
        Math.max(80, Math.min(520, window.innerHeight - bottom - 12)),
      );
    };
    fitMenu();
    window.addEventListener("resize", fitMenu);
    window.addEventListener("scroll", fitMenu, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("resize", fitMenu);
      window.removeEventListener("scroll", fitMenu, true);
    };
  }, [open]);
  const entries = (mobile: boolean) =>
    groups.map((group) => (
      <div className="project-navigation-group" key={group.label}>
        <small className="project-navigation-label">{group.label}</small>
        {group.pages.map((value) => {
          const label = projectPages.find(([id]) => id === value)![1];
          return (
            <button
              key={value}
              {...(mobile
                ? { "data-project-page": value }
                : { "data-tab": value })}
              className={value === tab ? "active" : ""}
              aria-current={value === tab ? "page" : undefined}
              onClick={() => {
                setOpen(false);
                onTab(value);
                if (mobile) trigger.current?.focus({ preventScroll: true });
              }}
            >
              <span>{label}</span>
              {value === "decisions" && attention && (
                <i aria-label="有待处理事项" />
              )}
              {mobile && value === tab && (
                <Check size={16} aria-hidden="true" />
              )}
            </button>
          );
        })}
      </div>
    ));
  return (
    <>
      <nav className="tabbar project-navigation" aria-label="项目内容">
        {entries(false)}
      </nav>
      <div
        className="project-navigation-mobile"
        ref={ref}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            event.preventDefault();
            setOpen(false);
            trigger.current?.focus();
          }
        }}
      >
        <nav className="project-quick-navigation" aria-label="常用项目页面">
          {["overview", "script", "materials", "shots", "delivery"].map(
            (value) => (
              <button
                key={value}
                className={value === tab ? "active" : ""}
                aria-current={value === tab ? "page" : undefined}
                data-quick-page={value}
                onClick={() => onTab(value)}
              >
                {
                  (
                    {
                      overview: "概览",
                      script: "剧本",
                      materials: "素材",
                      shots: "镜头",
                      delivery: "交付",
                    } as Record<string, string>
                  )[value]
                }
              </button>
            ),
          )}
        </nav>
        <button
          ref={trigger}
          className="project-navigation-trigger"
          data-active={
            !["overview", "script", "materials", "shots", "delivery"].includes(
              tab,
            )
          }
          aria-expanded={open}
          aria-controls={menuId}
          aria-label={
            "项目页面：" +
            (projectPages.find(([id]) => id === tab)?.[1] || "制作工作流")
          }
          onClick={() => setOpen((value) => !value)}
        >
          <span>更多</span>
          {attention && (
            <i className="project-attention-dot" aria-label="有待处理事项" />
          )}
          <ChevronDown size={18} aria-hidden="true" />
        </button>
        {open && (
          <nav
            id={menuId}
            className="project-navigation-menu"
            style={{ maxHeight: menuHeight }}
            aria-label="项目页面选择"
          >
            {entries(true)}
          </nav>
        )}
      </div>
    </>
  );
}
