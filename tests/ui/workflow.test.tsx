import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Team, Workflow } from "../../ui/factory/src/Workflow";
import type { RecordData } from "../../ui/factory/src/api";
const ID = "012345abcdef";
function graph(extra: RecordData = {}): RecordData {
  return {
    project_id: ID,
    revision: 7,
    active_node: "write",
    nodes: [
      {
        id: "write",
        label: "服务器定义的创作步骤",
        owner: "screenwriter",
        members: ["screenwriter"],
        status: "running",
        input: "当前故事目标",
        output: "版本绑定的剧本",
        attempt: 2,
        evidence: "task_receipt",
      },
      {
        id: "check",
        label: "服务器定义的验收步骤",
        owner: "independent_critic",
        members: ["independent_critic"],
        status: "pending",
      },
    ],
    edges: [
      {
        id: "review-loop",
        source: "check",
        target: "write",
        loop: true,
        label: "审核未通过返回",
      },
    ],
    ...extra,
  };
}
function mount(
  initial: RecordData,
  flow: RecordData = {},
  extra: RecordData = {},
) {
  const client = new QueryClient();
  const onDecision = vi.fn(),
    onShot = vi.fn();
  const view = (g: RecordData, f = flow) => (
    <QueryClientProvider client={client}>
      <Workflow
        graph={g}
        flow={f}
        onDecision={onDecision}
        onShot={onShot}
        {...extra}
      />
    </QueryClientProvider>
  );
  const result = render(view(initial));
  return {
    ...result,
    onDecision,
    onShot,
    update: (g: RecordData, f = flow) => result.rerender(view(g, f)),
  };
}

describe("real server workflow and agent handoffs", () => {
  it("does not fabricate a workflow before nodes arrive", () => {
    mount({ nodes: [], edges: [] });
    expect(screen.getByText("尚未返回执行图")).toBeVisible();
    expect(
      screen.queryByRole("navigation", { name: "制作阶段" }),
    ).not.toBeInTheDocument();
  });
  it("uses server groups and exposes conditional and repair transitions", async () => {
    const view = mount(
      graph({
        presentation: {
          stages: [
            { id: "write-group", label: "创作大阶段", node_ids: ["write"] },
            { id: "check-group", label: "验收大阶段", node_ids: ["check"] },
          ],
        },
        nodes: [
          ...graph().nodes.map((n: RecordData) => ({
            ...n,
            conditional: n.id === "check",
          })),
        ],
      }),
    );
    expect(screen.queryByTestId("rf__wrapper")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /验收大阶段/ }));
    expect(
      screen.getByRole("heading", { name: "服务器定义的验收步骤" }),
    ).toBeVisible();
    expect(screen.getByText("按需执行")).toBeVisible();
    expect(screen.getByText("审核未通过返回")).toBeVisible();
    view.update(
      graph({
        nodes: [{ id: "dispatch", label: "新交付步骤", owner: "editor" }],
        edges: [],
      }),
    );
    expect(screen.getByRole("heading", { name: "新交付步骤" })).toBeVisible();
    expect(screen.queryByText("按需执行")).not.toBeInTheDocument();
  });
  it("selects exact substeps and keeps manual selection when active work changes", async () => {
    const view = mount(graph());
    fireEvent.click(document.querySelector('[data-flow-node="check"]')!);
    view.update(graph({ active_node: "write", revision: 8 }));
    expect(
      screen.getByRole("heading", { name: "服务器定义的验收步骤" }),
    ).toBeVisible();
  });
  it("opens both file-only inputs and outputs and keeps reviewer roles visible", async () => {
    const flow = {
      project_id: ID,
      nodes: [
        {
          id: "independent_critic",
          role: "independent_critic",
          inputs: [
            {
              label: "当前稿件",
              artifact_url: `/media/production/factory-${ID}/planning/script.md`,
            },
          ],
          outputs: [{ label: "审核记录", value: "逐镜证据保留" }],
        },
      ],
      task_board: { tasks: [{ id: "write", reviewer: "independent_critic" }] },
    };
    mount(graph(), flow);
    const inspector = within(
      screen.getByRole("complementary", { name: "节点详情" }),
    );
    expect(inspector.getByText("独立审稿")).toBeVisible();
    expect(inspector.getByText("当前稿件")).toBeVisible();
    expect(inspector.getByRole("button", { name: "阅读文件" })).toBeVisible();
    expect(await inspector.findByText("逐镜证据保留")).toBeVisible();
    expect(inspector.queryByText("尚未生成")).not.toBeInTheDocument();
  });
  it("separates current step evidence from other steps and unassigned history", () => {
    mount(graph(), {
      nodes: [
        {
          id: "screenwriter",
          role: "screenwriter",
          executions: [
            {
              id: "current",
              model: "本次模型",
              stage: "write",
              scope: "current_run",
            },
            {
              id: "elsewhere",
              model: "其他步骤模型",
              stage: "check",
              scope: "current_run",
            },
            { id: "legacy", model: "旧模型", scope: "project_history" },
          ],
        },
      ],
    });
    expect(screen.getByText(/本次模型/)).toBeVisible();
    expect(screen.queryByText(/其他步骤模型/)).not.toBeInTheDocument();
    expect(screen.getByText(/未记录步骤归属/)).toBeVisible();
  });
  it("offers human decisions only for the active blocking step", async () => {
    const view = mount(graph(), {}, { attention: { blocking: true } });
    await userEvent.click(screen.getByRole("button", { name: "处理当前事项" }));
    expect(view.onDecision).toHaveBeenCalledOnce();
    fireEvent.click(document.querySelector('[data-flow-node="check"]')!);
    expect(
      screen.queryByRole("button", { name: "处理当前事项" }),
    ).not.toBeInTheDocument();
  });
  it("renders inputs as well as outputs in the team handoff section", async () => {
    render(
      <Team
        graph={{}}
        projectRevision={1}
        flow={{
          revision: 1,
          nodes: [
            {
              id: "writer",
              role: "screenwriter",
              inputs: [{ label: "完整输入", value: "故事动机" }],
              outputs: [{ label: "完整输出", value: "剧本交付" }],
            },
          ],
        }}
      />,
    );
    await userEvent.click(screen.getByText("输入、交付与依赖"));
    expect(screen.getByText("故事动机")).toBeVisible();
    expect(screen.getByText("剧本交付")).toBeVisible();
  });

  it("limits team calls to six recent records, filters by role and locates the exact call", async () => {
    const onInspect = vi.fn();
    render(
      <Team
        graph={{}}
        projectRevision={1}
        onInspect={onInspect}
        flow={{
          project_id: ID,
          revision: 1,
          nodes: [
            {
              id: "writer",
              role: "screenwriter",
              executions: Array.from({ length: 8 }, (_, index) => ({
                id: `writer-${index + 1}`,
                model: `编剧调用 ${index + 1}`,
                started_at: index + 1,
                stage: "screenplay",
              })),
            },
            {
              id: "critic",
              role: "independent_critic",
              executions: Array.from({ length: 3 }, (_, index) => ({
                id: `critic-${index + 1}`,
                model: `审稿调用 ${index + 1}`,
                started_at: index + 20,
              })),
            },
          ],
        }}
      />,
    );
    const calls = () =>
      [...document.querySelectorAll("[data-execution]")].map((element) =>
        element.getAttribute("data-execution"),
      );
    expect(calls()).toEqual([
      "critic-3",
      "critic-2",
      "critic-1",
      "writer-8",
      "writer-7",
      "writer-6",
    ]);
    expect(
      screen.getByRole("navigation", { name: "调用记录分页" }),
    ).toHaveTextContent("第 1 / 2 页");
    await userEvent.click(screen.getByRole("button", { name: "下一页调用" }));
    expect(calls()).toEqual([
      "writer-5",
      "writer-4",
      "writer-3",
      "writer-2",
      "writer-1",
    ]);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "调用角色" }),
      "screenwriter",
    );
    expect(calls()).toEqual([
      "writer-8",
      "writer-7",
      "writer-6",
      "writer-5",
      "writer-4",
      "writer-3",
    ]);
    expect(
      screen.getByRole("navigation", { name: "调用记录分页" }),
    ).toHaveTextContent("第 1 / 2 页");
    await userEvent.click(screen.getByRole("button", { name: "下一页调用" }));
    const call = document.querySelector('[data-execution="writer-2"]')!;
    await userEvent.click(
      within(call as HTMLElement).getByRole("button", {
        name: "定位步骤与记录",
      }),
    );
    expect(onInspect).toHaveBeenCalledWith("screenplay", "writer-2");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "调用角色" }),
      "independent_critic",
    );
    expect(calls()).toEqual(["critic-3", "critic-2", "critic-1"]);
    expect(
      screen.queryByRole("navigation", { name: "调用记录分页" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the selected role, page, open request and reader while newer calls arrive", async () => {
    const fetcher = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(new Response("正在阅读的旧调用原文")),
      );
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    const executions = Array.from({ length: 9 }, (_, index) => ({
      id: `writer-${index + 1}`,
      model: `编剧调用 ${index + 1}`,
      started_at: index + 1,
      attempts: [
        {
          attempt: 1,
          artifacts: [
            {
              label: "调用响应",
              artifact_url: `/media/production/factory-${ID}/planning/response-${index + 1}.txt`,
            },
          ],
        },
      ],
    }));
    const view = (calls: RecordData[]) => (
      <QueryClientProvider client={client}>
        <Team
          graph={{}}
          projectRevision={1}
          flow={{
            project_id: ID,
            revision: 1,
            nodes: [{ id: "writer", role: "screenwriter", executions: calls }],
          }}
        />
      </QueryClientProvider>
    );
    const rendered = render(view(executions));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "调用角色" }),
      "screenwriter",
    );
    await userEvent.click(screen.getByRole("button", { name: "下一页调用" }));
    const call = document.querySelector(
      '[data-execution="writer-3"]',
    ) as HTMLElement;
    await userEvent.click(within(call).getByText("第 1 次请求与响应"));
    await userEvent.click(
      within(call).getByRole("button", { name: "阅读文件" }),
    );
    expect(await within(call).findByText("正在阅读的旧调用原文")).toBeVisible();
    await userEvent.click(within(call).getByRole("button", { name: "原文" }));
    await userEvent.click(
      within(call).getByRole("button", { name: "放大阅读" }),
    );
    const dialog = screen.getByRole("dialog");
    const content = dialog.querySelector(".document-content")!;
    content.scrollTop = 120;
    rendered.rerender(
      view([
        ...[...executions].reverse().map((execution) => ({ ...execution })),
        ...Array.from({ length: 3 }, (_, index) => ({
          id: `new-${index}`,
          model: `新调用 ${index}`,
          started_at: index + 100,
        })),
      ]),
    );
    expect(screen.getByRole("combobox", { name: "调用角色" })).toHaveValue(
      "screenwriter",
    );
    expect(
      screen.getByRole("navigation", { name: "调用记录分页" }),
    ).toHaveTextContent("第 2 / 2 页");
    expect(document.querySelector('[data-execution="writer-3"]')).toBe(call);
    expect(call.querySelector("details")).toHaveAttribute("open");
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(within(dialog).getByRole("button", { name: "原文" })).toHaveClass(
      "active",
    );
    expect(content.scrollTop).toBe(120);
    expect(fetcher).toHaveBeenCalledOnce();
    await userEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
    await userEvent.click(screen.getByRole("button", { name: "查看最新调用" }));
    expect(
      screen.getByRole("navigation", { name: "调用记录分页" }),
    ).toHaveTextContent("第 1 / 2 页");
    expect(
      [...document.querySelectorAll("[data-execution]")].map((element) =>
        element.getAttribute("data-execution"),
      ),
    ).toEqual(["new-2", "new-1", "new-0", "writer-9", "writer-8", "writer-7"]);
  });

  it("clamps a removed final call page and keeps a small refreshed list free of pagination", async () => {
    const executions = Array.from({ length: 7 }, (_, index) => ({
      id: `call-${index}`,
      model: `调用 ${index}`,
      started_at: index + 1,
    }));
    const view = (calls: RecordData[]) => (
      <Team
        graph={{}}
        projectRevision={1}
        flow={{
          project_id: ID,
          revision: 1,
          nodes: [{ id: "writer", role: "screenwriter", executions: calls }],
        }}
      />
    );
    const rendered = render(view(executions));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "调用角色" }),
      "screenwriter",
    );
    await userEvent.click(screen.getByRole("button", { name: "下一页调用" }));
    rendered.rerender(view(executions.slice(4)));
    expect(
      screen.queryByRole("navigation", { name: "调用记录分页" }),
    ).not.toBeInTheDocument();
    expect(document.querySelectorAll("[data-execution]")).toHaveLength(3);
    expect(screen.getByText("编剧 · 调用 6")).toBeVisible();
    rendered.rerender(view(executions));
    expect(
      screen.getByRole("navigation", { name: "调用记录分页" }),
    ).toHaveTextContent("第 1 / 2 页");
    rendered.rerender(view([]));
    expect(screen.getByRole("combobox", { name: "调用角色" })).toHaveValue(
      "screenwriter",
    );
    expect(screen.getByText("该角色尚无保存的执行记录。")).toBeVisible();
    rendered.rerender(view(executions));
    expect(screen.getByRole("combobox", { name: "调用角色" })).toHaveValue(
      "screenwriter",
    );
    expect(
      screen.getByRole("navigation", { name: "调用记录分页" }),
    ).toHaveTextContent("第 1 / 2 页");
  });

  it("shows task owners, reviewers and links users to the relevant product area", async () => {
    const onOpen = vi.fn();
    render(
      <Team
        graph={{}}
        projectRevision={9}
        onOpen={onOpen}
        flow={{
          revision: 9,
          task_board: {
            tasks: [
              {
                id: "screenplay",
                label: "剧本工单",
                owner: "screenwriter",
                reviewer: "independent_critic",
                current: true,
                status: "running",
                inputs: ["故事目标"],
                outputs: ["剧本初稿"],
                record: { attempt: 2, started_at: 100 },
              },
              {
                id: "production",
                label: "镜头制作工单",
                owner: "image_generator",
                reviewer: "visual_critic",
                current: true,
                status: "waiting",
                inputs: ["已确认分镜"],
                outputs: ["镜头素材"],
              },
              {
                id: "final_decision",
                label: "终审工单",
                owner: "producer",
                reviewer: "producer",
                current: true,
                status: "human",
                inputs: ["成片"],
                outputs: ["终审记录"],
              },
            ],
          },
        }}
      />,
    );
    const screenplay = screen
      .getByRole("heading", { name: "剧本工单" })
      .closest("article")!;
    expect(within(screenplay).getByText(/负责：编剧/)).toHaveTextContent(
      "审核：独立审稿",
    );
    expect(within(screenplay).getByText("故事目标")).toBeVisible();
    expect(within(screenplay).getByText("剧本初稿")).toBeVisible();
    for (const title of ["剧本工单", "镜头制作工单", "终审工单"]) {
      const card = screen
        .getByRole("heading", { name: title })
        .closest("article")!;
      await userEvent.click(
        within(card).getByRole("button", { name: "打开关联产物" }),
      );
    }
    expect(onOpen.mock.calls).toEqual([["script"], ["shots"], ["decisions"]]);
  });

  it("puts current work first and keeps finished tasks and team responsibilities available on demand", async () => {
    const onInspect = vi.fn();
    const tasks = [
      { id: "screenplay", label: "正在编剧", status: "running", current: true },
      { id: "failed", label: "需要重试", status: "failed", current: true },
      { id: "human", label: "需要人工", status: "human", current: true },
      { id: "waiting", label: "等待上游", status: "waiting", current: true },
      {
        id: "historical",
        label: "旧版本运行工单",
        status: "running",
        current: false,
      },
      { id: "future", label: "待启动工单", status: "pending", current: false },
      {
        id: "skipped",
        label: "无需执行工单",
        status: "skipped",
        current: true,
      },
      {
        id: "finished",
        label: "已结束工单",
        status: "finished",
        current: true,
      },
      {
        id: "production",
        label: "已交付工单",
        status: "succeeded",
        current: true,
      },
      {
        id: "completed",
        label: "已完成工单",
        status: "completed",
        current: true,
      },
    ].map((task) => ({
      ...task,
      owner: "screenwriter",
      reviewer: "independent_critic",
    }));
    const view = () => (
      <Team
        graph={{
          active_node: "screenplay",
          groups: [
            { id: "writing", label: "创作分工", roles: ["screenwriter"] },
            { id: "review", label: "审核分工", roles: ["independent_critic"] },
          ],
        }}
        projectRevision={1}
        onInspect={onInspect}
        flow={{ revision: 1, task_board: { tasks } }}
      />
    );
    const rendered = render(view());
    expect(screen.getAllByRole("heading", { level: 2 })[0]).toHaveTextContent(
      "当前任务与交接",
    );
    for (const id of ["screenplay", "failed", "human", "waiting"]) {
      expect(document.querySelector(`[data-team-task="${id}"]`)).toBeVisible();
    }
    for (const id of [
      "historical",
      "future",
      "skipped",
      "finished",
      "production",
      "completed",
    ]) {
      expect(
        document.querySelector(`[data-team-task="${id}"]`),
      ).not.toBeVisible();
    }
    expect(screen.getByText("创作分工")).not.toBeVisible();
    await userEvent.click(screen.getByText("其他工单 · 6 项"));
    const delivered = document.querySelector(
      '[data-team-task="production"]',
    ) as HTMLElement;
    expect(delivered).toBeVisible();
    await userEvent.click(
      within(delivered).getByRole("button", { name: "打开关联产物" }),
    );
    expect(onInspect).toHaveBeenCalledWith("production");
    await userEvent.click(screen.getByText("团队分工 · 2 个协作组"));
    expect(screen.getByText("创作分工")).toBeVisible();
    rendered.rerender(view());
    expect(delivered).toBeVisible();
    expect(screen.getByText("创作分工")).toBeVisible();
  });

  it("keeps every unfinished current task visible even when more than six need attention", () => {
    const tasks = Array.from({ length: 8 }, (_, index) => ({
      id: `active-${index}`,
      label: `当前任务 ${index}`,
      current: true,
      status: index === 7 ? "succeeded" : index % 2 ? "failed" : "running",
    }));
    render(
      <Team
        graph={{}}
        projectRevision={1}
        flow={{ revision: 1, task_board: { tasks } }}
      />,
    );
    for (let index = 0; index < 7; index++) {
      expect(
        document.querySelector(`[data-team-task="active-${index}"]`),
      ).toBeVisible();
    }
    expect(
      document.querySelector('[data-team-task="active-7"]'),
    ).not.toBeVisible();
  });

  it("retains the latest completed handoff when a large task board has no unfinished work", async () => {
    const tasks = Array.from({ length: 8 }, (_, index) => ({
      id: index === 7 ? "production" : `completed-${index}`,
      label: index === 7 ? "最新后期交付" : `完成工单 ${index}`,
      current: true,
      status: index === 6 ? "skipped" : "succeeded",
      record: { finished_at: index + 1, attempt: 1 },
    }));
    render(
      <Team
        graph={{}}
        projectRevision={1}
        flow={{ revision: 1, task_board: { tasks } }}
      />,
    );
    const latest = document.querySelector(
      '[data-team-task="production"]',
    ) as HTMLElement;
    expect(latest).toBeVisible();
    expect(within(latest).getByText("已完成")).toBeVisible();
    expect(
      document.querySelector('[data-team-task="completed-0"]'),
    ).not.toBeVisible();
    await userEvent.click(screen.getByText("其他工单 · 7 项"));
    expect(
      document.querySelector('[data-team-task="completed-0"]'),
    ).toBeVisible();
  });

  it("keeps the latest producer decision and unresolved decisions visible while folding resolved history", async () => {
    render(
      <Team
        graph={{}}
        projectRevision={1}
        flow={{
          revision: 1,
          supervision: {
            records: [
              {
                kind: "story",
                status: "selected",
                started_at: 1,
                current_gate_version: false,
                proposal: { rationale: "较早已选故事" },
              },
              {
                kind: "style",
                status: "manual",
                started_at: 2,
                current_gate_version: true,
                proposal: { rationale: "待人工确认视觉" },
              },
              {
                kind: "old_review",
                status: "requested",
                started_at: 2,
                current_gate_version: false,
                proposal: { rationale: "未完成历史请求" },
              },
              {
                kind: "final",
                status: "selected",
                started_at: 3,
                current_gate_version: true,
                proposal: { rationale: "最近保存的决定" },
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText("较早已选故事")).not.toBeVisible();
    expect(screen.getByText("待人工确认视觉")).toBeVisible();
    expect(screen.getByText("未完成历史请求")).toBeVisible();
    expect(screen.getByText("最近保存的决定")).toBeVisible();
    await userEvent.click(screen.getByText("历史总制片决定 · 1 条"));
    expect(screen.getByText("较早已选故事")).toBeVisible();
  });

  it("distinguishes historical reused and skipped production tasks from a new successful execution", async () => {
    const tasks = [
      {
        id: "old-shot",
        shot_id: "S01",
        stage: "image",
        owner: "image_generator",
        reviewer: "visual_critic",
        production_revision: 1,
        referenced_by_revision: 3,
        attempt: "take_01",
        status: "reused",
        artifact: `production/factory-${ID}/shots/S01/frame.png`,
      },
      {
        id: "skipped-step",
        phase: "story_resolution",
        owner: "story_editor",
        reviewer: "story_critic",
        production_revision: 3,
        attempt: 1,
        status: "skipped",
      },
      {
        id: "unknown-version",
        shot_id: "S02",
        stage: "video",
        owner: "video_generator",
        reviewer: "visual_critic",
        production_revision: null,
        referenced_by_revision: 3,
        attempt: 1,
        status: "unknown",
      },
    ];
    const view = mount(
      graph({
        active_node: "production",
        nodes: [
          {
            id: "production",
            label: "镜头和后期",
            owner: "editor",
            status: "running",
          },
        ],
        edges: [],
        production_tasks: tasks,
      }),
    );
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows[1]).toHaveTextContent(/复用|reused/);
    expect(rows[1]).toHaveTextContent("v1");
    expect(rows[1]).not.toHaveTextContent("已完成");
    expect(rows[2]).toHaveTextContent("无需执行");
    expect(rows[2]).not.toHaveTextContent("已完成");
    expect(rows[3]).not.toHaveTextContent("v3");
    expect(rows[3]).not.toHaveTextContent("已完成");
    expect(
      within(rows[1]).getByRole("link", { name: "查看产物" }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining(`/production/factory-${ID}/shots/S01/frame.png`),
    );
    await userEvent.click(within(rows[1]).getByRole("button", { name: "S01" }));
    expect(view.onShot).toHaveBeenCalledWith("S01");
  });

  it("disables stale delivery evidence while retaining the last recorded task status", () => {
    render(
      <Team
        graph={{}}
        projectRevision={10}
        flow={{
          revision: 9,
          task_board: {
            tasks: [
              {
                id: "story_resolution",
                label: "无需目标改写",
                owner: "story_editor",
                reviewer: "story_critic",
                current: true,
                status: "skipped",
              },
            ],
          },
          delivery_readiness: {
            checks: [],
            artifacts: [
              {
                label: "上一版交付包",
                url: `/media/production/factory-${ID}/delivery/manifest.json`,
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByText(/团队记录正在同步/)).toBeVisible();
    expect(screen.getByText("无需执行")).toBeVisible();
    expect(screen.getByText("上一版交付包")).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "上一版交付包" }),
    ).not.toBeInTheDocument();
  });
});
