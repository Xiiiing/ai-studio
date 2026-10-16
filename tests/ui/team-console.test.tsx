import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TeamConsole, TeamOverview } from "../../ui/factory/src/TeamConsole";
import type { RecordData } from "../../ui/factory/src/api";

const id = "012345abcdef";
const task = (overrides: RecordData = {}) => ({
  id: "video:S01",
  label: "第 1 镜视频",
  kind: "video",
  scope: "S01",
  shot_id: "S01",
  owner: "video_generator",
  department: "visual",
  status: "running",
  generation: 2,
  attempt: 1,
  input_version: "storyboard-v3",
  dependencies: ["image:S01"],
  inputs: [{ label: "动作要求", value: "表情克制，保留服装" }],
  artifacts: [],
  review_status: "pending",
  adoption_status: "unselected",
  priority: 0,
  hold_after: false,
  locked: false,
  model: "actual-model-v1",
  attempts: [],
  ...overrides,
});
const departments = [
  ["production", "制片统筹"],
  ["story", "故事编剧"],
  ["director", "导演分镜"],
  ["visual", "美术与影像"],
  ["sound", "声音制作"],
  ["post", "后期制作"],
  ["quality", "独立质检"],
];
function team() {
  return {
    schema: 1,
    enabled: true,
    control_revision: 0,
    departments: departments.map(([id, label]) => ({
      id,
      label,
      lead: "video_generator",
      responsibility: `${label}职责`,
      members: id === "visual" ? ["video_generator"] : [],
    })),
    members: [
      {
        id: "video_generator",
        role: "video_generator",
        label: "视频制作",
        department: "visual",
        leader: false,
        status: "running",
        current_tasks: ["video:S01"],
      },
    ],
    task_summary: {
      total: 2,
      running: 1,
      waiting: 0,
      blocked: 0,
      completed: 1,
    },
    events: [
      {
        id: "e1",
        at: "2026-10-16T04:00:00Z",
        message: "首帧已交付，进入视频制作",
        kind: "handoff",
        task_id: "image:S01",
      },
    ],
    instructions: [],
    locks: [],
    actions: [],
  };
}
function mount(fallback?: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <TeamConsole projectId={id}>
        {fallback && <div>已有执行回执</div>}
      </TeamConsole>
    </QueryClientProvider>,
  );
  return client;
}
function mockApi() {
  const image = task({
    id: "image:S01",
    label: "第 1 镜首帧",
    status: "completed",
    review_status: "passed",
    artifacts: [
      { label: "已确认首帧", url: `/media/production/factory-${id}/frame.png` },
    ],
    dependencies: [],
    owner: "image_generator",
  });
  return vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    const data = path.pathname.endsWith("/team")
      ? team()
      : path.pathname.endsWith("/tasks/image%3AS01")
        ? { task: image }
        : path.pathname.endsWith("/tasks/video%3AS01")
          ? { task: task() }
          : {
              tasks:
                path.searchParams.get("status") === "completed"
                  ? [image]
                  : [task(), image],
              total: 2,
              offset: 0,
              limit: 30,
            };
    return new Response(JSON.stringify(data), {
      headers: { "Content-Type": "application/json" },
    });
  });
}
afterEach(() => vi.unstubAllGlobals());
describe("team command console", () => {
  it("shows real departments, independent statuses, evidence, and event drilldown", async () => {
    const fetch = mockApi();
    vi.stubGlobal("fetch", fetch);
    mount();
    expect(
      await screen.findByRole("heading", { name: "团队指挥台" }),
    ).toBeVisible();
    for (const [, label] of departments)
      expect(screen.getByRole("heading", { name: label })).toBeVisible();
    const detail = within(
      screen.getByRole("complementary", { name: "制作任务详情" }),
    );
    expect(await detail.findByText("actual-model-v1")).toBeVisible();
    expect(detail.getByText("storyboard-v3")).toBeVisible();
    expect(await detail.findByText("表情克制，保留服装")).toBeVisible();
    expect(detail.getByText("待审核")).toBeVisible();
    expect(detail.getByText("未采用")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "介入并下达要求" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "首帧已交付，进入视频制作" }),
    );
    expect(
      await detail.findByRole("heading", { name: "第 1 镜首帧" }),
    ).toBeVisible();
    expect(detail.getByRole("link", { name: "已确认首帧" })).toHaveAttribute(
      "href",
      expect.stringContaining(`/factory-${id}/frame.png`),
    );
    expect(
      fetch.mock.calls.every(([, options]: any[]) => options.method === "GET"),
    ).toBe(true);
  });
  it("sends filters to the server and retains the selected task across refresh and filtering", async () => {
    const fetch = mockApi();
    vi.stubGlobal("fetch", fetch);
    const client = mount();
    await screen.findByRole("heading", { name: "团队指挥台" });
    const detail = within(
      screen.getByRole("complementary", { name: "制作任务详情" }),
    );
    await detail.findByRole("heading", { name: "第 1 镜视频" });
    await userEvent.click(
      screen.getByRole("button", { name: /第 1 镜视频.*执行中/ }),
    );
    await userEvent.selectOptions(screen.getByLabelText("部门"), "visual");
    await userEvent.selectOptions(
      screen.getByLabelText("执行状态"),
      "completed",
    );
    await userEvent.type(screen.getByLabelText("镜头编号"), "S01");
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([url]) =>
          String(url).includes(
            "department=visual&status=completed&shot_id=S01",
          ),
        ),
      ).toBe(true),
    );
    await client.invalidateQueries();
    expect(detail.getByRole("heading", { name: "第 1 镜视频" })).toBeVisible();
    expect(screen.getByLabelText("镜头编号")).toHaveValue("S01");
  });
  it("preserves legacy receipts if the server does not expose team data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "not found" }), { status: 404 }),
      ),
    );
    mount(true);
    expect(screen.getByText("已有执行回执")).toBeVisible();
    await waitFor(() =>
      expect(screen.queryByText("团队指挥台")).not.toBeInTheDocument(),
    );
  });
  it("keeps existing receipts accessible when live team is available", async () => {
    vi.stubGlobal("fetch", mockApi());
    mount(true);
    await screen.findByRole("heading", { name: "团队指挥台" });
    await userEvent.click(screen.getByText("工作流与历史执行回执"));
    expect(screen.getByText("已有执行回执")).toBeVisible();
  });
  it("provides the project overview summary with a single team entry", async () => {
    vi.stubGlobal("fetch", mockApi());
    const client = new QueryClient();
    const onOpen = vi.fn();
    render(
      <QueryClientProvider client={client}>
        <TeamOverview projectId={id} onOpen={onOpen} />
      </QueryClientProvider>,
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "打开团队指挥台" }),
    );
    expect(onOpen).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("团队任务概况")).toHaveTextContent("1执行中");
  });
});
