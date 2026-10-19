import { useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";

export const projectPages = [
  ["overview", "制作概览"],
  ["decisions", "待办决策"],
  ["team", "团队与回执"],
  ["prompts", "提示词工作台"],
  ["shots", "镜头制作台"],
  ["script", "剧本与分镜"],
  ["quality", "制作与质检"],
  ["comparison", "质量比较"],
  ["delivery", "成片交付"],
  ["series", "系列资产"],
  ["materials", "素材与定稿"],
  ["costs", "云任务与费用"],
] as const;
const primary = [
  ["overview", "概览"],
  ["script", "剧本"],
  ["materials", "素材"],
  ["shots", "镜头"],
  ["decisions", "审核"],
  ["delivery", "交付"],
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
  const more = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (more.current) more.current.open = false;
  }, [tab]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!more.current?.contains(event.target as Node) && more.current)
        more.current.open = false;
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);
  const section =
    tab === "series"
      ? "materials"
      : ["quality", "comparison"].includes(tab)
        ? "decisions"
        : tab;
  const children =
    section === "materials"
      ? [
          ["materials", "项目素材与定稿"],
          ["series", "系列资产"],
        ]
      : section === "decisions"
        ? [
            ["decisions", "待办决策"],
            ["quality", "质检记录"],
            ["comparison", "质量比较"],
          ]
        : [];
  return (
    <div className="project-navigation-bar">
      <nav
        className="project-navigation project-primary-navigation"
        aria-label="项目内容"
      >
        {primary.map(([value, label]) => (
          <button
            key={value}
            data-tab={value}
            aria-label={label}
            className={section === value ? "active" : ""}
            aria-current={section === value ? "page" : undefined}
            onClick={() => onTab(value)}
          >
            {label}
            {value === "decisions" && attention && (
              <i aria-label="有待处理事项" />
            )}
          </button>
        ))}
      </nav>
      <details
        className="project-more"
        ref={more}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node))
            event.currentTarget.open = false;
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.currentTarget.open = false;
            event.currentTarget.querySelector("summary")?.focus();
          }
        }}
      >
        <summary
          aria-label={
            "更多项目页面" +
            (["team", "prompts", "costs"].includes(tab)
              ? "：" + projectPages.find(([id]) => id === tab)?.[1]
              : "")
          }
        >
          更多
          <ChevronDown size={14} aria-hidden="true" />
        </summary>
        <nav className="project-navigation-menu" aria-label="更多项目页面">
          {["team", "prompts", "costs"].map((value) => (
            <button
              key={value}
              data-project-page={value}
              aria-current={tab === value ? "page" : undefined}
              onClick={() => {
                if (more.current) more.current.open = false;
                onTab(value);
                more.current?.querySelector("summary")?.focus();
              }}
            >
              {projectPages.find(([id]) => id === value)?.[1]}
            </button>
          ))}
        </nav>
      </details>
      {!!children.length && (
        <nav
          className="project-subnavigation"
          aria-label={section === "materials" ? "素材页面" : "审核页面"}
        >
          {children.map(([value, label]) => (
            <button
              key={value}
              data-project-page={value}
              aria-current={tab === value ? "page" : undefined}
              onClick={() => onTab(value)}
            >
              {label}
            </button>
          ))}
        </nav>
      )}
    </div>
  );
}
