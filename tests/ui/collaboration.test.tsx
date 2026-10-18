import { describe, it, expect } from "vitest";
import { collaborationModel } from "../../ui/factory/src/WorkflowCollaboration";
import { stageFlow } from "../../ui/factory/src/WorkflowMap";
const steps = [
  {
    id: "write",
    label: "创作规划",
    owner: "screenwriter",
    reviewer: "independent_critic",
    members: ["screenwriter", "independent_critic"],
    status: "blocked",
  },
  {
    id: "edit",
    label: "剪辑",
    owner: "editor",
    members: ["editor"],
    status: "pending",
  },
];
const graph = {
  active_node: "write",
  departments: [
    { id: "story", label: "编剧", roles: ["screenwriter"] },
    { id: "review", label: "审核", roles: ["independent_critic"] },
    { id: "post", label: "后期", roles: ["editor"] },
  ],
  edges: [
    { source: "write", target: "edit" },
    { source: "edit", target: "write", loop: true },
  ],
};
describe("department and employee evidence", () => {
  it("keeps an employee result and historical call under the recorded role, without inheriting a blocked shared-step state", () => {
    const result = collaborationModel(
      graph,
      {
        collaboration_tasks: [
          {
            id: "w1",
            role: "screenwriter",
            stage_id: "write",
            status: "completed",
            output: "编剧实际交付",
          },
        ],
        nodes: [
          {
            role: "independent_critic",
            status: "pending",
            status_source: "not_recorded",
            executions: [
              { id: "r1", scope: "project_history", model: "真实历史模型" },
            ],
          },
        ],
      },
      steps,
    );
    const writer = result.members.find((m) => m.id === "screenwriter")!,
      critic = result.members.find((m) => m.id === "independent_critic")!;
    expect(writer.savedTasks.map((t) => t.output)).toEqual(["编剧实际交付"]);
    expect(writer.executions).toEqual([]);
    expect(critic.savedTasks).toEqual([]);
    expect(critic.executions.map((e) => e.model)).toEqual(["真实历史模型"]);
    expect(critic.status).toBe("pending");
    expect(critic.status_label).toBe("1 份记录");
    expect(result.departments.find((d) => d.id === "story")?.status).toBe(
      "blocked",
    );
    expect(result.handoffs).toContainEqual(
      expect.objectContaining({
        source: "screenwriter",
        target: "independent_critic",
        basis: "负责与审核分工",
      }),
    );
  });
  it("keeps documented department order from turning normal cross-department data into a repair route", () => {
    const result = collaborationModel(graph, {}, steps);
    const flow = stageFlow(
      result.graph,
      [...result.departments].reverse(),
      result.members,
    );
    expect(
      flow.edges.find((e) => e.source === "story" && e.target === "post")
        ?.repair,
    ).toBe(false);
    expect(
      flow.edges.find((e) => e.source === "post" && e.target === "story")
        ?.repair,
    ).toBe(true);
    expect(flow.edges.some((e) => e.running)).toBe(false);
  });
  it("retains saved tasks for a newly assigned or historical role outside current graph membership", () => {
    const result = collaborationModel(
      graph,
      {
        collaboration_tasks: [
          {
            id: "new-role-task",
            role: "dialogue_editor",
            project_id: "child-unit",
            output: "对白调整",
          },
        ],
        nodes: [
          {
            role: "old_role",
            executions: [{ id: "old-call", scope: "project_history" }],
          },
        ],
      },
      steps,
    );
    const other = result.departments.find((d) => d.id === "other-roles");
    expect(other?.node_ids).toEqual(["dialogue_editor", "old_role"]);
    expect(
      result.members.find((m) => m.id === "dialogue_editor")?.savedTasks[0]
        .project_id,
    ).toBe("child-unit");
    expect(
      result.members.find((m) => m.id === "old_role")?.executions[0].id,
    ).toBe("old-call");
  });
  it("keeps a recorded stage worker separate from a newly reassigned owner", () => {
    const result = collaborationModel(
      graph,
      {
        task_board: {
          tasks: [
            {
              id: "write",
              owner: "screenwriter",
              label: "规划写作",
              current: false,
              record: {
                owner: "creator",
                status: "succeeded",
                model: "historical-model",
                run_id: "old-run",
              },
            },
          ],
        },
      },
      steps,
    );
    expect(
      result.members.find((m) => m.id === "screenwriter")?.savedTasks,
    ).toEqual([]);
    const record = result.members.find((m) => m.id === "creator")
      ?.savedTasks[0];
    expect(record?.model).toBe("historical-model");
    expect(record?.source_label).toBe("历史步骤工单");
    expect(record?.input).toContain("未保存独立输入正文");
  });
  it.each(["paused", "blocked", "human"])(
    "stops an old running role when the current project step is %s",
    (status) => {
      const result = collaborationModel(
        { ...graph, revision: 9 },
        {
          revision: 8,
          nodes: [
            {
              role: "screenwriter",
              status: "running",
              status_source: "project_stage",
            },
          ],
        },
        steps.map((s) => (s.id === "write" ? { ...s, status } : s)),
      );
      expect(result.graph.active_node).toBeUndefined();
      expect(result.members.find((m) => m.id === "screenwriter")?.status).toBe(
        "pending",
      );
      expect(
        stageFlow(result.graph, result.departments, result.members).edges.some(
          (e) => e.running,
        ),
      ).toBe(false);
    },
  );
});

describe("clickable handoff evidence", () => {
  it("separates matched receiver input from unassociated sender results", () => {
    const output = {
      episodes: [{ number: 1, synopsis: "真实交付的第一集大纲" }],
    };
    const result = collaborationModel(
      graph,
      {
        collaboration_tasks: [
          {
            id: "writer-1",
            role: "screenwriter",
            status: "succeeded",
            stage_id: "write",
            output,
          },
          {
            id: "review-1",
            role: "independent_critic",
            status: "succeeded",
            stage_id: "write",
            output: "审稿结论",
            received: [
              {
                source_task_id: "writer-1",
                source_role: "screenwriter",
                label: "分集大纲",
                input_field: "episodes",
                value: output,
                basis: "recorded_input_content_match",
              },
            ],
          },
        ],
      },
      steps,
    );
    const review = result.handoffs.find(
      (h) => h.target === "independent_critic",
    )!;
    expect(review.received_count).toBe(1);
    expect(review.deliveries[0].value).toEqual(output);
    expect(review.available).toHaveLength(1);
    const post = result.handoffs.find((h) => h.target === "editor")!;
    expect(post.received_count).toBe(0);
    expect(post.available).toHaveLength(1);
    expect(post.in_progress).toBe(false);
  });

  it("only marks a current timestamped worker and a linked receiver input as processing", () => {
    const runningSteps = steps.map((s) => ({
      ...s,
      status: s.id === "write" ? "running" : "pending",
    }));
    const flow = {
      collaboration_tasks: [
        {
          id: "old-unfinished",
          role: "screenwriter",
          stage_id: "write",
          status: "running",
          started_at: 5,
        },
        {
          id: "writer-done",
          role: "screenwriter",
          stage_id: "write",
          status: "succeeded",
          finished_at: 10,
          output: "大纲",
        },
        {
          id: "review-live",
          role: "independent_critic",
          stage_id: "write",
          status: "running",
          started_at: 11,
          received: [
            {
              source_task_id: "writer-done",
              source_role: "screenwriter",
              input_field: "episodes",
              label: "大纲",
              value: "大纲",
            },
          ],
        },
      ],
    };
    const result = collaborationModel(graph, flow, runningSteps);
    expect(result.members.find((m) => m.id === "screenwriter")?.status).toBe(
      "pending",
    );
    expect(
      result.members.find((m) => m.id === "independent_critic")?.status,
    ).toBe("running");
    expect(
      result.handoffs.find((h) => h.target === "independent_critic")
        ?.in_progress,
    ).toBe(true);
    expect(
      result.handoffs.find((h) => h.target === "editor")?.in_progress,
    ).toBe(false);
    const paused = collaborationModel(
      graph,
      flow,
      runningSteps.map((s) => ({ ...s, status: "paused" })),
    );
    expect(paused.handoffs.some((h) => h.in_progress)).toBe(false);
  });
});

it.each([
  { status: "queued", started: null },
  { status: "running", started: 30 },
])(
  "does not reuse a dangling receipt from before the current job: %s",
  (current_job) => {
    const result = collaborationModel(
      graph,
      {
        current_job,
        collaboration_tasks: [
          {
            id: "old-call",
            role: "screenwriter",
            stage_id: "write",
            status: "running",
            started_at: 20,
          },
        ],
      },
      steps.map((s) => ({
        ...s,
        status: s.id === "write" ? "running" : "pending",
      })),
    );
    expect(result.members.find((m) => m.id === "screenwriter")?.status).toBe(
      "pending",
    );
    expect(result.handoffs.some((h) => h.in_progress)).toBe(false);
  },
);

it("keeps concurrent current-job workers active when another task finishes", () => {
  const result = collaborationModel(
    graph,
    {
      current_job: { status: "running", started: 10 },
      collaboration_tasks: [
        {
          id: "writer-live",
          role: "screenwriter",
          stage_id: "write",
          status: "running",
          started_at: 11,
        },
        {
          id: "critic-live",
          role: "independent_critic",
          stage_id: "write",
          status: "running",
          started_at: 12,
        },
        {
          id: "other-finished",
          role: "editor",
          stage_id: "edit",
          status: "succeeded",
          finished_at: 15,
        },
      ],
    },
    steps.map((s) => ({
      ...s,
      status: s.id === "write" ? "running" : "pending",
    })),
  );
  expect(
    result.members.filter((m) => m.status === "running").map((m) => m.id),
  ).toEqual(["screenwriter", "independent_critic"]);
});
