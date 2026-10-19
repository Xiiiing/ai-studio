import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { CreateDrama, DramaDetail } from "../../ui/factory/src/Dramas";
import { defaultAutomationBudget } from "../../ui/factory/src/ProductionWorkspace";

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify({ id: "012345abcdef", revision: 1 }), {
        status: 201,
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
});
function create() {
  const onCreated = vi.fn();
  const result = render(
    <CreateDrama onClose={vi.fn()} onCreated={onCreated} onLegacy={vi.fn()} />,
  );
  return { ...result, onCreated };
}
const submitted = () => JSON.parse(fetchMock.mock.calls[0][1].body);

it("starts with a theme and authorizes the displayed full-drama defaults", async () => {
  create();
  expect(
    screen.getByRole("button", { name: "创建并开始制作" }),
  ).toBeVisible();
  await userEvent.type(
    screen.getByRole("textbox", { name: "创作方向" }),
    "成年修表师寻找家族真相，最终达成和解。",
  );
  await userEvent.click(
    screen.getByRole("button", { name: /创建并开始制作|建立整剧项目/ }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(submitted()).toMatchObject({
    title: "成年修表师寻找家族真相，最终达成和解",
    episode_count: 6,
    duration_seconds: 300,
    format: "animation",
    automation: {
      enabled: true,
      episode_numbers: [1, 2, 3, 4, 5, 6],
      budget: defaultAutomationBudget,
    },
  });
});

it("keeps the all-drama scope aligned when episode count changes", async () => {
  create();
  const count = screen.getByRole("spinbutton", { name: "总集数" });
  await userEvent.clear(count);
  await userEvent.type(count, "2");
  await userEvent.type(
    screen.getByRole("textbox", { name: "创作方向" }),
    "成年人在旧车站重逢的故事。",
  );
  await userEvent.click(
    screen.getByRole("button", { name: /创建并开始制作|建立整剧项目/ }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(submitted().automation.episode_numbers).toEqual([1, 2]);
});

it("submits only explicitly selected episodes and blocks an empty authorization", async () => {
  create();
  await userEvent.type(
    screen.getByRole("textbox", { name: "创作方向" }),
    "成年人在旧车站重逢的故事。",
  );
  await userEvent.click(screen.getByRole("button", { name: "指定集数" }));
  for (let number = 1; number <= 6; number++)
    await userEvent.click(
      screen.getByRole("checkbox", { name: `第 ${number} 集` }),
    );
  await userEvent.click(
    screen.getByRole("button", { name: /创建并开始制作|建立整剧项目/ }),
  );
  expect(fetchMock).not.toHaveBeenCalled();
  expect(screen.getByText("请至少选择一集作为本次制作范围。")).toBeVisible();
  await userEvent.click(screen.getByRole("checkbox", { name: "第 3 集" }));
  await userEvent.click(
    screen.getByRole("button", { name: /创建并开始制作|建立整剧项目/ }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(submitted().automation.episode_numbers).toEqual([3]);
});

it("leaves the legacy engine manual when the user selects step-by-step creation", async () => {
  create();
  await userEvent.type(
    screen.getByRole("textbox", { name: "创作方向" }),
    "成年人在旧车站重逢的故事。",
  );
  await userEvent.click(screen.getByRole("button", { name: "手动分步" }));
  await userEvent.click(
    screen.getByRole("button", { name: /创建并开始制作|建立整剧项目/ }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(submitted()).not.toHaveProperty("automation");
});

it("imports the source script and retains it in the automatic creation request", async () => {
  const { container } = create();
  fireEvent.change(container.querySelector("select")!, {
    target: { value: "script" },
  });
  const source = "第一集：两位成年人在旧车站重逢，并寻找一封旧信。";
  const file = new File([source], "剧本.md", { type: "text/markdown" });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode(source).buffer,
  });
  await userEvent.upload(screen.getByLabelText("导入剧本"), file);
  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "剧本正文" })).toHaveValue(
      source,
    ),
  );
  await userEvent.click(
    screen.getByRole("button", { name: /创建并开始制作|建立整剧项目/ }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(submitted()).toMatchObject({
    source_text: source,
    source_name: "剧本.md",
    automation: { enabled: true },
  });
});

it.each(["overview", "workflow", "production"])(
  "keeps the %s bookmark in the unified production section",
  async (activeTab) => {
    fetchMock.mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            id: "012345abcdef",
            title: "旧项目",
            status: "ready",
            episode_count: 2,
            duration_seconds: 60,
            plan_version: 1,
            episodes: [],
            plans: [],
            workflow: {},
          }),
        ),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <DramaDetail
          id="012345abcdef"
          activeTab={activeTab}
          onBack={vi.fn()}
          openEpisode={vi.fn()}
          onTab={vi.fn()}
        />
      </QueryClientProvider>,
    );
    const nav = await screen.findByRole("navigation", { name: "整剧工作区" });
    expect(within(nav).getByRole("button", { name: "制作" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("button", { name: "素材" })).toBeVisible();
    expect(screen.getByRole("region", { name: "连续制作状态" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "打开制作流程" }),
    ).not.toBeInTheDocument();
  },
);
