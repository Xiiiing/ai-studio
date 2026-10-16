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
