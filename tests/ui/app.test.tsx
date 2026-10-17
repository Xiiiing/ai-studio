import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import App from "../../ui/factory/src/App";
const id = "012345abcdef";
function setup() {
  let revision = 7;
  const calls: { path: string; body: any }[] = [];
  const project = {
    id,
    revision,
    title: "暖色时钟",
    brief: "成年修表师与邻居解开旧时钟的谜题。",
    status: "awaiting_story",
    stage: "story_decision",
    format: "motion_comic",
    duration_seconds: 30,
    shots: [],
    proposals: [
      {
        id: "A",
        title: "时钟的秘密",
        logline: "在雨夜寻找旧时钟的主人",
        eligible: true,
      },
      { id: "B", title: "未通过候选", eligible: false },
    ],
    decisions: [
      {
        id: "gate-7",
        type: "story",
        status: "pending",
        options: [{ id: "A" }],
      },
    ],
  };
  const gate = project.decisions[0];
  const attention = {
    blocking: true,
    kind: "story",
    title: "选择故事方向",
    summary: "候选故事已完成独立审核",
    target_tab: "decisions",
    gate,
    actions: [],
  };
  const operator = {
    attention,
    current_activity: "等待故事方向",
    actions: [
      {
        id: "take_control",
        label: "人工介入",
        enabled: true,
        inputs: [],
        impact: "在安全边界接管",
      },
    ],
  };
  const graph = {
    nodes: [
      {
        id: "research",
        label: "选题研究",
        owner: "researcher",
        status: "succeeded",
        members: ["researcher"],
      },
      {
        id: "story_decision",
        label: "故事决策",
        owner: "producer",
        status: "human",
        members: ["producer"],
      },
    ],
    edges: [{ source: "research", target: "story_decision" }],
    active_node: "story_decision",
  };
  const overview = {
    projects: [{ ...project, attention }],
    attention_projects: [{ ...project, attention }],
    metrics: { attention: 1, projects: 1 },
    queue: {},
    activity: [],
  };
  const fetch = vi.fn(async (path: any, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(path), body });
    if (String(path).startsWith("/api/factory/console"))
      return new Response(JSON.stringify(overview));
    if (String(path).endsWith("/workspace"))
      return new Response(
        JSON.stringify({
          project: { ...project, revision },
          revision,
          operator,
          workflow: graph,
          flow: {
            project_id: id,
            revision,
            nodes: [],
            task_board: { tasks: [] },
          },
        }),
      );
    if (String(path).endsWith("/models"))
      return new Response(
        JSON.stringify({
          revision: "model-1",
          editable_profiles: {},
          role_assignments: {},
          roles: [],
        }),
      );
    if (String(path).endsWith("/production-profiles"))
      return new Response(
        JSON.stringify({
          presets: {
            compact6: { label: "紧凑短片 · 6 镜" },
            legacy15: { label: "经典短剧 · 15 镜" },
          },
        }),
      );
    if (String(path).endsWith("/workflow"))
      return new Response(JSON.stringify({ production_graph: graph }));
    if (body && String(path).endsWith("/projects"))
      return new Response(JSON.stringify({ project: { ...project, id } }), {
        status: 202,
      });
    if (body) {
      revision++;
      return new Response(
        JSON.stringify({ project: { ...project, revision } }),
      );
    }
    return new Response(JSON.stringify({}));
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
  return { calls, fetch, client };
}
it("实际导航从总览进入具体故事选择，提交绑定当前gate与revision", async () => {
  const { calls } = setup();
  await screen.findByRole("heading", { name: "制片总览" });
  const nav = screen.getByRole("navigation");
  await userEvent.click(within(nav).getByRole("button", { name: /待办/ }));
  await screen.findByRole("heading", { name: "待办决策" });
  await userEvent.click(screen.getByRole("button", { name: "处理" }));
  await screen.findByRole("button", { name: "选择这个故事" });
  expect(screen.queryByText("未通过候选")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "选择这个故事" }));
  await waitFor(() =>
    expect(calls.some((c) => c.path.endsWith("/decision"))).toBe(true),
  );
  expect(calls.find((c) => c.path.endsWith("/decision"))?.body).toEqual({
    type: "story",
    choice: "A",
    decision_id: "gate-7",
    expected_revision: 7,
  });
});
it("项目先展示实际创作产物，完整工作流可按需打开", async () => {
  setup();
  await screen.findByRole("heading", { name: "制片总览" });
  await userEvent.click(document.querySelector("#project-list button")!);
  await screen.findByRole("heading", { name: "当前创作产物" });
  expect(screen.getByText("在雨夜寻找旧时钟的主人")).toBeVisible();
  expect(document.querySelector(".react-flow")).toBeNull();
  expect(document.querySelector(".project-process")).not.toHaveAttribute(
    "open",
  );
  await userEvent.click(screen.getByText("完整制作流程"));
  expect(screen.getByRole("region", { name: "制作工作流" })).toBeVisible();
  expect(
    document
      .querySelector(".project-focus")!
      .compareDocumentPosition(document.querySelector(".workflow-workspace")!) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});
it("人工介入按钮由真实动作契约提供并提交安全接管动作", async () => {
  const { calls } = setup();
  await screen.findByRole("heading", { name: "制片总览" });
  await userEvent.click(document.querySelector("#project-list button")!);
  await screen.findByText("项目操作");
  await userEvent.click(screen.getByText("项目操作"));
  await screen.findByRole("button", { name: "人工介入" });
  await userEvent.click(screen.getByRole("button", { name: "人工介入" }));
  await waitFor(() =>
    expect(calls.some((c) => c.path.endsWith("/action"))).toBe(true),
  );
  expect(calls.find((c) => c.path.endsWith("/action"))?.body).toEqual({
    action: "take_control",
    expected_revision: 7,
  });
  expect(
    calls.filter((c) => c.body && /generate|decision/.test(c.path)),
  ).toHaveLength(0);
});
it("创建入口默认AI代管并显式提交原创/改编与制作规格", async () => {
  const { calls } = setup();
  await screen.findByRole("heading", { name: "制片总览" });
  await userEvent.click(screen.getAllByRole("button", { name: "新建制作" })[0]);
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByLabelText(/团队协作方式/)).toHaveValue(
    "delegated",
  );
  await userEvent.type(
    within(dialog).getByLabelText("创作方向"),
    "成年修表师与邻居解开谜题，最终和解。",
  );
  await userEvent.click(
    within(dialog).getByRole("button", { name: "开始自主策划" }),
  );
  await waitFor(() =>
    expect(
      calls.some((c) => c.path === "/api/factory/projects" && c.body),
    ).toBe(true),
  );
  const body = calls.find(
    (c) => c.path === "/api/factory/projects" && c.body,
  )?.body;
  expect(body.team_mode).toBe("delegated");
  expect(body.creation_mode).toBe("original");
  expect(body.production_profile.preset).toBe("compact6");
  expect(body.request_id).toMatch(/^[a-f0-9-]+$/);
});
