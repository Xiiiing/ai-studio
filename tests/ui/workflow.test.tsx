import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { Team, Workflow } from "../../ui/factory/src/Workflow";
import type { RecordData } from "../../ui/factory/src/api";

const ID = "012345abcdef";
const bboxDescriptor = Object.getOwnPropertyDescriptor(
  SVGElement.prototype,
  "getBBox",
);
beforeAll(() =>
  Object.defineProperty(SVGElement.prototype, "getBBox", {
    configurable: true,
    value(this: SVGElement) {
      return {
        x: 0,
        y: 0,
        width: (this.textContent?.length || 1) * 8,
        height: 16,
      };
    },
  }),
);
afterAll(() => {
  if (bboxDescriptor)
    Object.defineProperty(SVGElement.prototype, "getBBox", bboxDescriptor);
  else delete (SVGElement.prototype as any).getBBox;
});
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockRejectedValue(
        new Error("Workflow must use the supplied server snapshot"),
      ),
  );
  // jsdom has no layout engine. Supply browser measurements to the actual
  // React Flow renderer so its real SVG edges and click handlers can run.
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains("react-flow__handle")
        ? 8
        : this.classList.contains("react-flow__node")
          ? 260
          : 960;
    },
  );
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains("react-flow__handle")
        ? 8
        : this.classList.contains("react-flow__node")
          ? 120
          : 640;
    },
  );
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      return {
        width: this.offsetWidth || 960,
        height: this.offsetHeight || 640,
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 960,
        bottom: 640,
        toJSON: () => ({}),
      };
    },
  );
  class MeasuringObserver {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      queueMicrotask(() =>
        this.callback(
          [
            {
              target,
              contentRect: target.getBoundingClientRect(),
            } as ResizeObserverEntry,
          ],
          this as unknown as ResizeObserver,
        ),
      );
    }
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", MeasuringObserver);
});
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
  it("does not fabricate a workflow while the server has supplied no nodes", () => {
    mount({ nodes: [], edges: [] });
    expect(screen.getByText("尚未返回执行图")).toBeVisible();
    expect(screen.queryByTestId("rf__wrapper")).not.toBeInTheDocument();
    expect(screen.queryByText("故事提案")).not.toBeInTheDocument();
  });

  it("renders and updates actual server nodes and edges instead of a hardcoded sequence", async () => {
    const view = mount(graph());
    expect(screen.getByTestId("rf__node-write")).toHaveTextContent(
      "服务器定义的创作步骤",
    );
    expect(screen.getByTestId("rf__node-check")).toHaveTextContent(
      "服务器定义的验收步骤",
    );
    await waitFor(() =>
      expect(screen.getByTestId("rf__edge-review-loop")).toHaveTextContent(
        "审核未通过返回",
      ),
    );
    view.update(
      graph({
        active_node: "check",
        nodes: [
          {
            id: "check",
            label: "改后的独立验收",
            owner: "independent_critic",
            status: "running",
          },
          {
            id: "dispatch",
            label: "新加入的交付任务",
            owner: "editor",
            status: "pending",
          },
        ],
        edges: [
          {
            id: "new-server-edge",
            source: "check",
            target: "dispatch",
            conditional: true,
            label: "新服务交接条件",
          },
        ],
      }),
    );
    expect(screen.queryByTestId("rf__node-write")).not.toBeInTheDocument();
    expect(screen.getByTestId("rf__node-dispatch")).toHaveTextContent(
      "新加入的交付任务",
    );
    await waitFor(() =>
      expect(screen.getByTestId("rf__edge-new-server-edge")).toHaveTextContent(
        "新服务交接条件",
      ),
    );
    expect(
      screen.queryByTestId("rf__edge-review-loop"),
    ).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("opens a selected node's inputs, outputs and independently assigned reviewer", async () => {
    const serverGraph = graph({
      nodes: [
        {
          id: "write",
          label: "可恢复稿件任务",
          owner: "screenwriter",
          reviewer: "independent_critic",
          members: ["screenwriter"],
          status: "running",
          input: "已选故事与反馈",
          output: "待审核稿件",
          attempt: 2,
          evidence: "task_receipt",
        },
        {
          id: "check",
          label: "审查当前稿件",
          owner: "independent_critic",
          status: "pending",
        },
      ],
    });
    const flow = {
      nodes: [
        {
          id: "independent_critic",
          role: "independent_critic",
          inputs: [{ label: "当前稿件", value: "需要检查因果链" }],
          outputs: [
            {
              label: "审核记录",
              value: "逐镜证据保留",
              artifact_url: `/media/production/factory-${ID}/planning/review.json`,
            },
          ],
        },
      ],
    };
    mount(serverGraph, flow);
    const inspector = within(
      screen.getByRole("complementary", { name: "节点详情" }),
    );
    expect(
      inspector.getByRole("heading", { name: "可恢复稿件任务" }),
    ).toBeVisible();
    expect(inspector.getByText("已选故事与反馈")).toBeVisible();
    expect(inspector.getByText("待审核稿件")).toBeVisible();
    expect(inspector.getByText("独立审稿")).toBeVisible();
    await userEvent.click(inspector.getByText("独立审稿 · 输入与交付"));
    expect(inspector.getByText("逐镜证据保留")).toBeVisible();
    expect(inspector.getByRole("link", { name: "审核记录" })).toHaveAttribute(
      "href",
      expect.stringContaining(`/production/factory-${ID}/planning/review.json`),
    );
    fireEvent.click(screen.getByTestId("rf__node-check"));
    expect(
      inspector.getByRole("heading", { name: "审查当前稿件" }),
    ).toBeVisible();
  });

  it("keeps a contract's independent reviewer visible when the node omits members", () => {
    mount(graph(), {
      task_board: {
        tasks: [
          {
            id: "write",
            owner: "screenwriter",
            reviewer: "independent_critic",
          },
        ],
      },
    });
    expect(
      within(screen.getByRole("complementary", { name: "节点详情" })).getByText(
        "独立审稿",
      ),
    ).toBeVisible();
  });

  it("offers the decision handoff only on the active blocking node", async () => {
    const view = mount(graph(), {}, { attention: { blocking: true } });
    await userEvent.click(screen.getByRole("button", { name: "处理当前事项" }));
    expect(view.onDecision).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByTestId("rf__node-check"));
    expect(
      screen.queryByRole("button", { name: "处理当前事项" }),
    ).not.toBeInTheDocument();
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
