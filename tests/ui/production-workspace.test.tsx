import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import {
  AutomationSettings,
  ProductionWorkspace,
  defaultAutomationBudget,
  type AutomationConfig,
} from "../../ui/factory/src/ProductionWorkspace";
import { useState } from "react";

function project(extra: Record<string, unknown> = {}) {
  return {
    id: "012345abcdef",
    title: "测试整剧",
    episode_count: 3,
    status: "producing",
    plan_version: 1,
    automation: {
      enabled: true,
      episode_numbers: [1, 2],
      budget: defaultAutomationBudget,
    },
    automation_state: {
      status: "producing",
      stage: "production",
      reason: "正在制作授权剧集",
      episode_numbers: [1, 2],
      attention: [],
    },
    workflow: {
      graph: {
        active_node: "production",
        nodes: [
          { id: "planning", label: "全剧设定与分集大纲", status: "succeeded" },
          { id: "review", label: "全剧规划独立审核", status: "succeeded" },
          { id: "production", label: "按集制作与团队协作", status: "running" },
          { id: "acceptance", label: "逐集验收与成果", status: "pending" },
        ],
      },
      tasks: [
        {
          id: "task-1",
          stage_id: "production",
          title: "第 1 集剧本创作",
          role: "screenwriter",
          status: "running",
          model: "fixture-model",
          output: { summary: "正在准备剧本" },
        },
      ],
      stages: [],
    },
    episodes: [
      {
        id: "episode-1",
        episode_number: 1,
        title: "第 1 集",
        status: "producing",
        progress: 42,
        outline: { synopsis: "主角发现线索。" },
      },
    ],
    ...extra,
  };
}
function mount(
  extra: Record<string, unknown> = {},
  props: Record<string, unknown> = {},
) {
  const onOperation = vi.fn(),
    onReview = vi.fn(),
    onTab = vi.fn();
  render(
    <ProductionWorkspace
      project={project(extra)}
      busy={false}
      onOperation={onOperation}
      onReview={onReview}
      onTab={onTab}
      {...props}
    />,
  );
  return { onOperation, onReview, onTab };
}

it("shows the real authorized scope, recorded stages and activity without inventing additional completed work", async () => {
  mount();
  expect(screen.getByText(/已授权第 1、2 集/)).toBeVisible();
  expect(screen.getByText("已出片 0 / 3 集 · 已验收 0 集")).toBeVisible();
  expect(screen.getByText("第 1 集剧本创作")).toBeVisible();
  expect(screen.getByText("fixture-model")).toBeVisible();
  const stages = screen.getByRole("navigation", { name: "制作阶段" });
  expect(within(stages).getAllByRole("button")).toHaveLength(4);
  expect(
    within(stages).getByRole("button", { name: /按集制作与团队协作/ }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    screen.getByRole("progressbar", { name: "第 1 集制作进度" }),
  ).toHaveAttribute("aria-valuenow", "42");
  await userEvent.click(
    within(stages).getByRole("button", { name: /全剧规划独立审核/ }),
  );
  expect(
    screen.getByText("当前阶段尚无执行记录。完成的任务会在这里显示。"),
  ).toBeVisible();
  expect(screen.queryByText("第 1 集剧本创作")).not.toBeInTheDocument();
});

it("pauses and resumes the parent project directly", async () => {
  const { onOperation } = mount({
    pause_requested: true,
    automation_state: { status: "paused", reason: "已暂停" },
  });
  await userEvent.click(screen.getByRole("button", { name: "继续制作" }));
  expect(onOperation).toHaveBeenCalledWith("actions", { action: "resume" });
});

it("keeps a blocked episode's reason and repair action together", async () => {
  const { onOperation } = mount({
    episodes: [
      {
        id: "episode-2",
        episode_number: 2,
        status: "blocked",
        progress: 30,
        error: "素材审核返修额度用尽",
      },
    ],
  });
  const attention = screen
    .getByRole("heading", { name: "在这里处理" })
    .closest("section")!;
  expect(within(attention).getByText("素材审核返修额度用尽")).toBeVisible();
  await userEvent.click(
    within(attention).getByRole("button", { name: "继续此集" }),
  );
  expect(onOperation).toHaveBeenCalledWith("actions", {
    action: "retry",
    episode_id: "episode-2",
  });
});

it("offers explicit old-project takeover with the displayed per-unit budget", async () => {
  const { onOperation } = mount({
    automation: undefined,
    automation_state: { status: "disabled" },
  });
  expect(onOperation).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "设置连续制作" }));
  expect(screen.getByText(/并非整剧总额/)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "开启连续制作" }));
  expect(onOperation).toHaveBeenCalledWith("actions", {
    action: "enable_automation",
    automation: {
      enabled: true,
      episode_numbers: [1, 2, 3],
      budget: defaultAutomationBudget,
    },
  });
});

it("requires human review of finished film and exposes planning edits in place", async () => {
  const episode = {
    id: "episode-1",
    episode_number: 1,
    title: "第 1 集",
    status: "awaiting_final",
    delivery: { video_url: "/media/movie.mp4" },
  };
  const { onReview, onOperation } = mount({ episodes: [episode] });
  await userEvent.click(
    screen.getAllByRole("button", { name: "观看与验收第 1 集" })[0],
  );
  expect(onReview).toHaveBeenCalledWith(episode);
  expect(onOperation).not.toHaveBeenCalled();
});

it("shows actionable planning issues and embedded confirmation without navigating away", () => {
  mount(
    {
      plan_version: 0,
      status: "awaiting_plan",
      plan_review: { passed: true },
      episodes: [],
    },
    {
      planEditor: (
        <form aria-label="全剧规划编辑">
          <textarea aria-label="全剧主线" defaultValue="规划草稿" />
        </form>
      ),
    },
  );
  expect(screen.getByText("全剧规划已通过审核")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "全剧主线" })).toBeVisible();
});

it("keeps advanced tools collapsed and preserves their project context callbacks", async () => {
  const { onTab } = mount();
  expect(
    screen.getByText("高级工具与完整记录").closest("details"),
  ).not.toHaveAttribute("open");
  await userEvent.click(screen.getByText("高级工具与完整记录"));
  await userEvent.click(screen.getByRole("button", { name: "提示词工作台" }));
  expect(onTab).toHaveBeenCalledWith("prompts");
});

it("does not fabricate stage records for a legacy snapshot", () => {
  mount({ workflow: undefined });
  expect(screen.getByText("尚无阶段记录")).toBeVisible();
  expect(
    within(screen.getByRole("navigation", { name: "制作阶段" })).queryAllByRole(
      "button",
    ),
  ).toHaveLength(0);
});

function SettingsHarness() {
  const [value, setValue] = useState<AutomationConfig>({
    enabled: true,
    episode_numbers: [1, 2, 3],
    budget: defaultAutomationBudget,
  });
  return (
    <>
      <AutomationSettings count={3} value={value} onChange={setValue} />
      <output data-testid="settings">{JSON.stringify(value)}</output>
    </>
  );
}
it("allows a strict episode subset and budget edits without changing the authorization scope implicitly", async () => {
  render(<SettingsHarness />);
  await userEvent.click(screen.getByRole("button", { name: "指定集数" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "第 2 集" }));
  const budget = screen.getByRole("spinbutton", {
    name: "GPU 预算（卡分钟 / 单元）",
  });
  await userEvent.clear(budget);
  await userEvent.type(budget, "45");
  expect(JSON.parse(screen.getByTestId("settings").textContent!)).toMatchObject(
    { episode_numbers: [1, 3], budget: { gpu_minutes: 45 } },
  );
  await userEvent.click(screen.getByRole("button", { name: "全剧 3 集" }));
  expect(
    JSON.parse(screen.getByTestId("settings").textContent!).episode_numbers,
  ).toEqual([1, 2, 3]);
});

it("submits planning feedback directly for bounded AI repair", async () => {
  const { onOperation } = mount({
    plan_version: 0,
    status: "blocked",
    plan_phase: "exhausted",
    episodes: [],
  });
  await userEvent.type(
    screen.getByRole("textbox", { name: "补充创作要求" }),
    "故事发生在成年人的大学重逢，请按此修复歧义。",
  );
  await userEvent.click(screen.getByRole("button", { name: "继续规划与返修" }));
  expect(onOperation).toHaveBeenCalledWith("actions", {
    action: "retry",
    feedback: "故事发生在成年人的大学重逢，请按此修复歧义。",
  });
});

it("exposes safe in-place plan editing after a confirmed project is paused", () => {
  mount(
    {
      status: "paused",
      pause_requested: true,
      draft_plan: { storyline: "新草稿" },
      plans: [{ document: { storyline: "已确认旧稿" } }],
    },
    {
      planEditor: (
        <form aria-label="全剧规划编辑">
          <textarea aria-label="全剧主线" defaultValue="新草稿" />
        </form>
      ),
    },
  );
  expect(screen.getByRole("textbox", { name: "全剧主线" })).toBeVisible();
  expect(
    screen.queryByRole("textbox", { name: "补充创作要求" }),
  ).not.toBeInTheDocument();
});

it("reauthorizes an existing scope to unblock dependencies and preserves current budget defaults", async () => {
  const { onOperation } = mount({
    automation: {
      enabled: true,
      episode_numbers: [2],
      budget: { gpu_minutes: 45, wall_minutes: 60, max_attempts: 2 },
    },
    automation_state: {
      status: "blocked",
      reason: "前集不在授权范围内",
      episode_numbers: [2],
      attention: [{ code: "dependency_scope", message: "第 2 集依赖第 1 集" }],
    },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "调整制作范围与预算" }),
  );
  expect(screen.getByRole("checkbox", { name: "第 2 集" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "第 1 集" })).not.toBeChecked();
  await userEvent.click(screen.getByRole("checkbox", { name: "第 1 集" }));
  await userEvent.click(screen.getByRole("button", { name: "保存制作授权" }));
  expect(onOperation).toHaveBeenCalledWith("actions", {
    action: "enable_automation",
    automation: {
      enabled: true,
      episode_numbers: [1, 2],
      budget: { gpu_minutes: 45, wall_minutes: 60, max_attempts: 2 },
    },
  });
});

it("opens current episode artifacts inline with a read-only request", async () => {
  const fetchMock = vi.fn(
    async (_url: string, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          project: {
            script: { synopsis: "编剧已保存的当前剧本" },
            shots: [
              {
                id: "S01",
                image_url: "/media/shot.png",
                dramatic_beat: "主角打开旧信",
              },
            ],
          },
        }),
      ),
  );
  vi.stubGlobal("fetch", fetchMock);
  const { onOperation } = mount();
  expect(fetchMock).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "查看当前产物" }));
  expect(await screen.findByText("编剧已保存的当前剧本")).toBeVisible();
  expect(fetchMock.mock.calls[0][0]).toBe(
    "/api/factory/projects/episode-1/workspace",
  );
  expect(fetchMock.mock.calls[0][1]?.method).toBe("GET");
  await userEvent.click(screen.getByRole("button", { name: "分镜与素材" }));
  expect(screen.getByRole("img", { name: "分镜 S01" })).toBeVisible();
  expect(onOperation).not.toHaveBeenCalled();
});

it("requires a safe global pause before adjusting a begun episode's budget", async () => {
  const { onOperation } = mount({
    automation_state: {
      status: "blocked",
      attention: [{ code: "episode_blocked", message: "预算用尽" }],
    },
  });
  await userEvent.click(
    screen.getByRole("button", { name: "调整制作范围与预算" }),
  );
  expect(
    screen.getByRole("spinbutton", { name: "GPU 预算（卡分钟 / 单元）" }),
  ).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "暂停后调整预算" }));
  expect(onOperation).toHaveBeenCalledWith("actions", { action: "pause" });
});
