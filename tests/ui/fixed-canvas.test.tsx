import { fireEvent, render } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { FixedWorkflowCanvas } from "../../ui/factory/src/FixedWorkflowCanvas";
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
