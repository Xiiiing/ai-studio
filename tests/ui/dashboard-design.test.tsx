import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import App from "../../ui/factory/src/App";

const id = "012345abcdef";
const title = "长夜之后仍然有人守护这座城市：一部关于重逢与选择的原创剧集";
const current = {
  id,
  title,
  revision: 1,
  status: "awaiting_final",
  stage: "final_decision",
  current_activity: "等待成片验收",
  shots: [],
  attention: {
    blocking: true,
    title: "终片等待验收",
    summary: "已完成的成片等待你的决定。",
    target_tab: "decisions",
  },
};

function setup(options: { empty?: boolean; attention?: boolean } = {}) {
  const project = options.attention === false
    ? { ...current, attention: undefined }
    : current;
  const projects = options.empty ? [] : [project];
  const overview = {
    projects,
    attention_projects: options.empty || options.attention === false ? [] : [project],
    metrics: { running: 7, attention: 2, queued: 3, waiting_remote: 5, delivered: 11 },
    activity: [],
    queue: {},
  };
  const writes: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method && !["GET", "HEAD"].includes(init.method)) writes.push(String(path));
    if (String(path).startsWith("/api/factory/console"))
      return new Response(JSON.stringify(overview));
    if (String(path).endsWith("/workspace"))
      return new Response(JSON.stringify({
        project,
        operator: { attention: project.attention, actions: [] },
        workflow: { nodes: [], edges: [] },
        flow: { nodes: [], task_board: { tasks: [] } },
      }));
    if (String(path).endsWith("/production-profiles"))
      return new Response(JSON.stringify({ presets: {} }));
    return new Response("{}");
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  return { writes };
}

it("总览焦点展示真实待办项目，保留原始四项指标与制作区块", async () => {
  setup();
  await screen.findByRole("heading", { name: "制片总览" });
  const focus = document.querySelector(".dashboard-focus")!;
  expect(focus).toBeInTheDocument();
  expect(focus.querySelector(".dashboard-focus-copy")).toHaveTextContent(title);
  expect(focus).toHaveTextContent("等待成片验收");
  expect([...document.querySelectorAll("[data-metric]")].map((e) => e.textContent)).toEqual(["7", "2", "8", "11"]);
  for (const name of ["制作中的任务", "待办", "最近动态", "制作资源"])
    expect(screen.getByRole("heading", { name })).toBeInTheDocument();
  const decoration = focus.querySelector(".dashboard-focus-art")!;
  expect(decoration).toHaveAttribute("aria-hidden", "true");
  expect(decoration.querySelector("button,a,input,[tabindex] ")).toBeNull();
  expect(focus.textContent).not.toMatch(/节省|52\.6|节能/);
});

it("总览焦点直接进入待办指定页，打开动作不提交制作请求", async () => {
  const { writes } = setup();
  const button = await screen.findByRole("button", { name: "打开当前项目" });
  await userEvent.click(button);
  await waitFor(() => expect(location.hash).toBe(`#project=${id}&tab=decisions`));
  expect(writes).toEqual([]);
});

it("没有阻塞待办时焦点项目打开项目总览", async () => {
  setup({ attention: false });
  await userEvent.click(await screen.findByRole("button", { name: "打开当前项目" }));
  await waitFor(() => expect(location.hash).toBe(`#project=${id}&tab=overview`));
});

it("空工作室的焦点进入原新建剧流程，不捏造制作指标", async () => {
  const { writes } = setup({ empty: true });
  await screen.findByRole("heading", { name: "制片总览" });
  const focus = document.querySelector(".dashboard-focus") as HTMLElement;
  expect(focus).toHaveTextContent("开始第一部作品");
  await userEvent.click(within(focus).getByRole("button", { name: "新建一部剧" }));
  expect(await screen.findByRole("dialog", { name: "新建一部剧" })).toBeVisible();
  expect(writes).toEqual([]);
});
