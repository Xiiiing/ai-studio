import { fixedWorkflowRouter } from "../../ui/factory/src/FixedWorkflowRouting";
import { fireEvent, render } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import {
  FixedWorkflowCanvas,
  fixedWorkflowLayout,
} from "../../ui/factory/src/FixedWorkflowCanvas";
import {
  collaborationModel,
  handoffForPhases,
} from "../../ui/factory/src/WorkflowCollaboration";
import type { RecordData } from "../../ui/factory/src/api";

it("anchors a received draft to its recorded source phase when the writer also works in a later phase", () => {
  sessionStorage.clear();
  const steps = [
    {
      id: "planning",
      label: "初稿",
      owner: "screenwriter",
      members: ["screenwriter"],
      status: "succeeded",
    },
    {
      id: "rewrite",
      label: "改稿",
      owner: "screenwriter",
      members: ["screenwriter"],
      status: "pending",
    },
    {
      id: "review",
      label: "审稿",
      owner: "independent_critic",
      members: ["independent_critic"],
      status: "succeeded",
    },
  ];
  const graph: RecordData = {
    project_id: "provenance-test",
    nodes: steps,
    edges: [{ source: "rewrite", target: "review" }],
    departments: [
      { id: "story", label: "编剧部", roles: ["screenwriter"] },
      { id: "quality", label: "审核部", roles: ["independent_critic"] },
    ],
  };
  const flow = {
    collaboration_tasks: [
      {
        id: "draft-v1",
        owner: "screenwriter",
        stage_id: "planning",
        status: "completed",
        output: "初稿正文",
      },
      {
        id: "critic-v1",
        owner: "independent_critic",
        stage_id: "review",
        status: "completed",
        input: "初稿正文",
        received: [
          {
            source_task_id: "draft-v1",
            source_role: "screenwriter",
            value: "初稿正文",
            input_field: "draft",
          },
        ],
      },
    ],
  };
  const model = collaborationModel(graph, flow, steps);
  const view = render(
    <FixedWorkflowCanvas
      graph={graph}
      groups={steps.map((s) => ({ ...s, node_ids: [s.id] }))}
      steps={steps}
      model={model}
      onSelection={vi.fn()}
    />,
  );
  for (const button of view.container.querySelectorAll<HTMLButtonElement>(
    ".kind-stage > button[aria-expanded]",
  ))
    fireEvent.click(button);
  for (const button of view.container.querySelectorAll<HTMLButtonElement>(
    ".kind-department > button[aria-expanded]",
  ))
    fireEvent.click(button);
  const handoffs = [
    ...view.container.querySelectorAll("[data-flow-edge]"),
  ].filter(
    (e) =>
      e.getAttribute("data-source") === "screenwriter" &&
      e.getAttribute("data-target") === "independent_critic",
  );
  expect(handoffs.map((e) => e.getAttribute("data-flow-edge"))).toEqual([
    "planning/story/screenwriter>review/quality/independent_critic:forward",
  ]);
});

it("keeps a current handoff animation out of a previous draft's phase", () => {
  const model = collaborationModel(
    {
      departments: [{ id: "story", label: "编剧", roles: ["screenwriter"] }],
      nodes: [],
    },
    {
      collaboration_tasks: [
        {
          id: "old",
          role: "screenwriter",
          stage_id: "planning",
          status: "completed",
          output: "初稿",
        },
        {
          id: "new",
          role: "screenwriter",
          stage_id: "rewrite",
          status: "completed",
          output: "改稿",
        },
      ],
    },
    [],
  );
  const handoff = {
    source: "screenwriter",
    available: [],
    executions: [],
    deliveries: [
      {
        source_task_id: "old",
        receiver: { stage_id: "review" },
        in_progress: false,
      },
      {
        source_task_id: "new",
        receiver: { stage_id: "review" },
        in_progress: true,
      },
    ],
  };
  expect(
    handoffForPhases(handoff, model, ["planning"], ["review"]),
  ).toMatchObject({ received_count: 1, in_progress: false });
  expect(
    handoffForPhases(handoff, model, ["rewrite"], ["review"]),
  ).toMatchObject({ received_count: 1, in_progress: true });
});

it("lays five phases out in two aligned rows without repacking on status changes", () => {
  const steps = Array.from({ length: 5 }, (_, i) => ({
    id: `phase-${i}`,
    label: `阶段${i + 1}`,
    owner: "producer",
    members: ["producer"],
    status: "pending",
  }));
  const graph = {
    nodes: steps,
    edges: [],
    departments: [{ id: "direction", label: "制片", roles: ["producer"] }],
  };
  const groups = steps.map((s) => ({ ...s, node_ids: [s.id] }));
  const model = collaborationModel(graph, {}, steps);
  const layout = fixedWorkflowLayout(graph, groups, steps, model, 3);
  const phases = layout.boxes.filter((b) => b.kind === "stage");
  expect(phases[0].y).toBe(phases[2].y);
  expect(phases[3].y).toBe(phases[4].y);
  expect(phases[3].y).toBeGreaterThan(phases[0].y);
  expect(phases[3].x).toBe(phases[2].x);
  expect(phases[4].x).toBe(phases[1].x);
  const updated = steps.map((s) => ({ ...s, status: "running" }));
  expect(
    fixedWorkflowLayout(
      { ...graph, nodes: updated },
      groups,
      updated,
      collaborationModel(graph, {}, updated),
      3,
    ).boxes.map(({ id, x, y, width, height }) => ({ id, x, y, width, height })),
  ).toEqual(
    layout.boxes.map(({ id, x, y, width, height }) => ({
      id,
      x,
      y,
      width,
      height,
    })),
  );
});

it("routes a row turn outside all reserved cards and keeps its label readable", () => {
  const boxes = [
    { id: "top", kind: "stage", x: 320, y: 48, width: 196, height: 148 },
    {
      id: "department",
      kind: "department",
      x: 328,
      y: 232,
      width: 188,
      height: 72,
    },
    { id: "worker", kind: "role", x: 344, y: 328, width: 172, height: 64 },
    { id: "bottom", kind: "stage", x: 320, y: 440, width: 196, height: 148 },
  ];
  const route = fixedWorkflowRouter(boxes, { width: 564, height: 680 })(
    boxes[0],
    boxes[3],
  );
  const points = [...route.path.matchAll(/[ML]\s+([-\d.]+)\s+([-\d.]+)/g)].map(
    (m) => ({ x: Number(m[1]), y: Number(m[2]) }),
  );
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1],
      b = points[i],
      length = Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
    for (let d = 2; d < length; d += 2) {
      const x = a.x + ((b.x - a.x) * d) / length,
        y = a.y + ((b.y - a.y) * d) / length;
      expect(
        boxes.some(
          (box) =>
            x > box.x + 0.5 &&
            x < box.x + box.width - 0.5 &&
            y > box.y + 0.5 &&
            y < box.y + box.height - 0.5,
        ),
      ).toBe(false);
    }
  }
  expect(
    boxes.some(
      (b) =>
        route.x + 29 > b.x &&
        route.x - 29 < b.x + b.width &&
        route.y + 8 > b.y &&
        route.y - 14 < b.y + b.height,
    ),
  ).toBe(false);
});
