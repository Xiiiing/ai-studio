import { StudioMenu } from "./StudioMenu";
import { SelectionGroup } from "./SelectionGroup";

export const projectPages = [
  ["overview", "制作概览"],
  ["decisions", "待办决策"],
  ["team", "团队与回执"],
  ["prompts", "提示词工作台"],
  ["generation", "生成提示词流水线"],
  ["agent", "Agent 执行循环"],
  ["shots", "镜头制作台"],
  ["script", "剧本与分镜"],
  ["quality", "制作与质检"],
  ["comparison", "质量比较"],
  ["creative-quality", "审核与返修依据"],
  ["model-capabilities", "模型能力与版本"],
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
  const section =
    tab === "series"
      ? "materials"
      : ["quality", "comparison", "creative-quality"].includes(tab)
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
            ["creative-quality", "审核与返修依据"],
          ]
        : [];
  return (
    <div className="project-navigation-bar">
      <SelectionGroup
        as="nav"
        className="project-navigation project-primary-navigation"
        aria-label="项目内容"
        variant="underline"
        value={section}
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
      </SelectionGroup>
      <div className="project-more">
        <StudioMenu
          label="更多"
          ariaLabel={
            "更多项目页面" +
            ([
              "team",
              "prompts",
              "generation",
              "agent",
              "costs",
              "model-capabilities",
            ].includes(tab)
              ? "：" + projectPages.find(([id]) => id === tab)?.[1]
              : "")
          }
          triggerClassName="project-more-trigger"
          items={[
            "team",
            "prompts",
            "generation",
            "agent",
            "costs",
            "model-capabilities",
          ].map((value) => ({
            key: value,
            label: projectPages.find(([id]) => id === value)?.[1],
            selected: tab === value,
            onSelect: () => onTab(value),
          }))}
        />
      </div>
      {!!children.length && (
        <SelectionGroup
          as="nav"
          className="project-subnavigation"
          aria-label={section === "materials" ? "素材页面" : "审核页面"}
          variant="underline"
          value={tab}
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
        </SelectionGroup>
      )}
    </div>
  );
}
