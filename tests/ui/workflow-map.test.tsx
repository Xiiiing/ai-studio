import { describe, expect, it } from "vitest";
import {
  stageFlow,
  stageEdgePath,
  type StageEdge,
} from "../../ui/factory/src/WorkflowMap";
const groups = [
  { id: "story", label: "故事策划", node_ids: ["research", "story_decision"] },
  {
    id: "script",
    label: "剧本与分镜",
    node_ids: ["screenplay", "script_review"],
  },
  { id: "production", label: "镜头与后期", node_ids: ["production"] },
];
const steps = [
  { id: "research", status: "succeeded" },
  { id: "story_decision", status: "succeeded" },
  { id: "screenplay", status: "running" },
  { id: "script_review", status: "pending" },
  { id: "production", status: "pending" },
  { id: "revision", status: "pending" },
];
const graph = {
  active_node: "screenplay",
  edges: [
    { source: "research", target: "story_decision" },
    { source: "story_decision", target: "screenplay" },
    { source: "screenplay", target: "script_review" },
    { source: "script_review", target: "production" },
    { source: "production", target: "revision" },
    { source: "revision", target: "production" },
    { source: "unknown", target: "production" },
  ],
};
describe("fixed stage flow", () => {
  it("projects only actual cross-stage and repair links, preserving direction", () => {
    const flow = stageFlow(graph, groups, steps);
    expect(flow.edges.map((e) => [e.source, e.target])).toEqual([
      ["story", "script"],
      ["script", "production"],
      ["production", "revision"],
      ["revision", "production"],
    ]);
    expect(flow.edges.filter((e) => e.running).map((e) => e.id)).toEqual([
      "script:production:forward",
    ]);
    expect(flow.nodes.find((n) => n.id === "story")?.status).toBe("succeeded");
    expect(flow.edges.find((e) => e.target === "revision")?.repair).toBe(true);
    expect(stageFlow({ ...graph, edges: [] }, groups, steps).edges).toEqual([]);
  });
  it.each(["paused", "human", "blocked", "failed", "pending"])(
    "does not show data moving while the active step is %s",
    (status) => {
      expect(
        stageFlow(
          graph,
          groups,
          steps.map((n) => (n.id === "screenplay" ? { ...n, status } : n)),
        ).edges.some((e) => e.running),
      ).toBe(false);
    },
  );
  it("animates repair only when the actual revision step is running", () => {
    const flow = stageFlow(
      { ...graph, active_node: "revision" },
      groups,
      steps.map((n) => ({
        ...n,
        status: n.id === "revision" ? "running" : "pending",
      })),
    );
    expect(flow.edges.filter((e) => e.running).map((e) => e.id)).toEqual([
      "revision:production:repair",
    ]);
  });
  it("does not mark an entire stage complete from its active completed decision", () => {
    const flow = stageFlow(
      { ...graph, active_node: "story_decision" },
      groups,
      steps.map((n) => (n.id === "research" ? { ...n, status: "pending" } : n)),
    );
    expect(flow.nodes.find((n) => n.id === "story")?.status).toBe("pending");
  });
  it("keeps both directions of a return route in separate outer lanes", () => {
    const forward = {
      source: "production",
      target: "revision",
      repair: true,
    } as StageEdge;
    const reverse = {
      source: "revision",
      target: "production",
      repair: true,
    } as StageEdge;
    const source = { x: 350, y: 210, width: 140, height: 130 },
      target = { x: 24, y: 210, width: 140, height: 130 };
    const a = stageEdgePath(source, target, forward, {
        width: 520,
        height: 430,
      }),
      b = stageEdgePath(target, source, reverse, { width: 520, height: 430 });
    expect(a).toContain("388");
    expect(b).toContain("412");
    expect(a).not.toBe(b);
  });
});
