import { expect, it } from "vitest";
import { fixedWorkflowRouter } from "../../ui/factory/src/FixedWorkflowRouting";

for (const left of [56, 316])
  it(`keeps two return paths distinct and inside the canvas at x=${left}`, () => {
    const boxes = [
      { id: "writer", x: left, y: 536, width: 172, height: 64, kind: "role" },
      { id: "director", x: left, y: 712, width: 172, height: 64, kind: "role" },
      { id: "critic", x: left, y: 888, width: 172, height: 64, kind: "role" },
      {
        id: "department",
        x: left - 16,
        y: 448,
        width: 188,
        height: 72,
        kind: "department",
      },
    ];
    const bounds = { width: left + 268, height: 1100 };
    const before = structuredClone(boxes);
    const route = fixedWorkflowRouter(boxes, bounds);
    route(boxes[0], boxes[1]);
    route(boxes[1], boxes[2]);
    const director = route(boxes[1], boxes[0], true);
    const critic = route(boxes[2], boxes[0], true);
    const points = (path: string) =>
      [...path.matchAll(/[ML] ([\d.-]+) ([\d.-]+)/g)].map((m) => [
        Number(m[1]),
        Number(m[2]),
      ]);
    const lane = (path: string) => {
      const p = points(path);
      return p
        .slice(1)
        .filter((b, i) => b[0] === p[i][0] && Math.abs(b[1] - p[i][1]) > 80)
        .map((b) => b[0]);
    };
    expect(
      lane(director.path).some((x) =>
        lane(critic.path).every((y) => Math.abs(x - y) >= 24),
      ),
    ).toBe(true);
    expect(
      points(director.path)
        .concat(points(critic.path))
        .every((p) => p[0] >= 0 && p[0] <= bounds.width),
    ).toBe(true);
    expect(route(boxes[1], boxes[0], true)).toEqual(director);
    expect(route(boxes[2], boxes[0], true)).toEqual(critic);
    expect(boxes).toEqual(before);
  });
