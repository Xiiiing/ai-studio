export type CanvasBox = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  kind?: string;
};
type Point = { x: number; y: number };
/** Routing uses the complete reserved geometry, including collapsed children. */
export function fixedWorkflowRouter(
  boxes: CanvasBox[],
  bounds: { width: number; height: number },
) {
  const sorted = (values: number[]) =>
    [...new Set(values)].sort((a, b) => a - b);
  const xs = sorted(
    [
      16,
      bounds.width - 16,
      ...boxes.flatMap((b) => [
        b.x - 42,
        b.x - 14,
        b.x + b.width + 14,
        b.x + b.width + 42,
        b.x + b.width / 2,
      ]),
    ].filter((x) => x >= 4 && x <= bounds.width - 4),
  );
  const ys = sorted([
    16,
    bounds.height - 16,
    ...boxes.flatMap((b) => [
      b.y - 14,
      b.y + b.height + 14,
      b.y + b.height / 2 - 10,
      b.y + b.height / 2,
      b.y + b.height / 2 + 10,
    ]),
  ]);
  const obstacles = boxes.map((b) => ({
    left: b.x - 8,
    right: b.x + b.width + 8,
    top: b.y - 8,
    bottom: b.y + b.height + 8,
  }));
  const nx = xs.length;
  const point = (id: number): Point => ({
    x: xs[id % nx],
    y: ys[Math.floor(id / nx)],
  });
  const cell = (p: Point) => ys.indexOf(p.y) * nx + xs.indexOf(p.x);
  const clearCache = new Map<string, boolean>();
  const clear = (a: number, b: number) => {
    const key = a < b ? `${a}:${b}` : `${b}:${a}`,
      cached = clearCache.get(key);
    if (cached !== undefined) return cached;
    const s = point(a),
      t = point(b);
    const blocked = obstacles.some((r) =>
      s.x === t.x
        ? s.x > r.left &&
          s.x < r.right &&
          Math.max(s.y, t.y) > r.top &&
          Math.min(s.y, t.y) < r.bottom
        : s.y > r.top &&
          s.y < r.bottom &&
          Math.max(s.x, t.x) > r.left &&
          Math.min(s.x, t.x) < r.right,
    );
    clearCache.set(key, !blocked);
    return !blocked;
  };
  const routes = new Map<string, { path: string; x: number; y: number }>();
  // Shared destinations still need separate corridors wider than the pointer
  // hit area. Reservations change paths, never the fixed node geometry.
  const returnSegments: { a: Point; b: Point }[] = [];
  const allSegments: { a: Point; b: Point }[] = [];
  const returnLabels: Point[] = [];
  const returnCounts = new Map<number, number>();
  return (source: CanvasBox, target: CanvasBox, repair = false) => {
    const key = `${source.id}>${target.id}:${repair}`,
      cached = routes.get(key);
    if (cached) return cached;
    const center = (b: CanvasBox) => ({
      x: b.x + b.width / 2,
      y: b.y + b.height / 2,
    });
    const s = center(source),
      t = center(target),
      sameColumn = Math.abs(s.x - t.x) < 30;
    const top = repair && source.kind === "stage" && target.kind === "stage";
    const separateReturn = repair && sameColumn && !top;
    const overlapCost = (a: Point, b: Point) =>
      !sameColumn
        ? 0
        : (separateReturn ? allSegments : returnSegments).reduce(
            (cost, segment) => {
              const vertical = a.x === b.x && segment.a.x === segment.b.x;
              const horizontal = a.y === b.y && segment.a.y === segment.b.y;
              if (
                (!vertical && !horizontal) ||
                Math.abs(vertical ? a.x - segment.a.x : a.y - segment.a.y) >= 24
              )
                return cost;
              const overlap = vertical
                ? Math.min(
                    Math.max(a.y, b.y),
                    Math.max(segment.a.y, segment.b.y),
                  ) -
                  Math.max(
                    Math.min(a.y, b.y),
                    Math.min(segment.a.y, segment.b.y),
                  )
                : Math.min(
                    Math.max(a.x, b.x),
                    Math.max(segment.a.x, segment.b.x),
                  ) -
                  Math.max(
                    Math.min(a.x, b.x),
                    Math.min(segment.a.x, segment.b.x),
                  );
              return cost + Math.max(0, overlap) * 8;
            },
            0,
          );
    const column = Math.round(s.x / 260);
    // A narrow left gutter cannot hold several returns on a one-column canvas.
    const side = sameColumn
      ? (separateReturn &&
          source.x < 90 &&
          (returnCounts.get(column) || 0) > 0) ||
        (t.y >= s.y && !repair)
        ? 1
        : -1
      : t.x > s.x
        ? 1
        : -1;
    const offset = sameColumn ? 0 : side === 1 ? -10 : 10;
    const start = top
      ? { x: s.x, y: source.y - 14 }
      : {
          x: side === 1 ? source.x + source.width + 14 : source.x - 14,
          y: s.y + offset,
        };
    const end = top
      ? { x: t.x, y: target.y - 14 }
      : {
          x: sameColumn
            ? side === 1
              ? target.x + target.width + 14
              : target.x - 14
            : side === 1
              ? target.x - 14
              : target.x + target.width + 14,
          y: t.y + offset,
        };
    const startBoundary = top
      ? { x: start.x, y: source.y }
      : { x: start.x + (side === 1 ? -14 : 14), y: start.y };
    const endBoundary = top
      ? { x: end.x, y: target.y }
      : {
          x: sameColumn
            ? end.x + (side === 1 ? -14 : 14)
            : end.x + (side === 1 ? 14 : -14),
          y: end.y,
        };
    const startId = cell(start),
      endId = cell(end),
      size = nx * ys.length * 2;
    const distance = new Float64Array(size).fill(Infinity),
      parent = new Int32Array(size).fill(-1);
    type Entry = { score: number; cost: number; state: number };
    const heap: Entry[] = [];
    const push = (entry: Entry) => {
      heap.push(entry);
      let at = heap.length - 1;
      while (at > 0) {
        const p = (at - 1) >> 1;
        if (heap[p].score <= entry.score) break;
        heap[at] = heap[p];
        at = p;
      }
      heap[at] = entry;
    };
    const pop = () => {
      const first = heap[0],
        last = heap.pop()!;
      if (heap.length) {
        let at = 0;
        while (at * 2 + 1 < heap.length) {
          let child = at * 2 + 1;
          if (
            child + 1 < heap.length &&
            heap[child + 1].score < heap[child].score
          )
            child++;
          if (heap[child].score >= last.score) break;
          heap[at] = heap[child];
          at = child;
        }
        heap[at] = last;
      }
      return first;
    };
    const heuristic = (id: number) => {
      const p = point(id);
      return Math.abs(p.x - end.x) + Math.abs(p.y - end.y);
    };
    for (const dir of [0, 1]) {
      const state = startId * 2 + dir;
      distance[state] = 0;
      push({ state, cost: 0, score: heuristic(startId) });
    }
    let finish = -1;
    while (heap.length) {
      const current = pop();
      if (current.cost !== distance[current.state]) continue;
      const id = current.state >> 1,
        dir = current.state % 2;
      if (id === endId) {
        finish = current.state;
        break;
      }
      const col = id % nx,
        row = Math.floor(id / nx);
      const neighbors = [
        ...(col > 0 ? [id - 1] : []),
        ...(col + 1 < nx ? [id + 1] : []),
        ...(row > 0 ? [id - nx] : []),
        ...(row + 1 < ys.length ? [id + nx] : []),
      ];
      const from = point(id);
      for (const next of neighbors) {
        if (!clear(id, next)) continue;
        const to = point(next),
          nextDir = from.y === to.y ? 0 : 1,
          state = next * 2 + nextDir,
          cost =
            current.cost +
            Math.abs(from.x - to.x) +
            Math.abs(from.y - to.y) +
            overlapCost(from, to) +
            (dir === nextDir ? 0 : 18);
        if (cost >= distance[state]) continue;
        distance[state] = cost;
        parent[state] = current.state;
        push({ state, cost, score: cost + heuristic(next) });
      }
    }
    if (finish < 0)
      throw new Error(
        `No unobstructed workflow route: ${source.id} → ${target.id}`,
      );
    const points: Point[] = [];
    for (let state = finish; state >= 0; state = parent[state])
      points.unshift(point(state >> 1));
    points.unshift(startBoundary);
    points.push(endBoundary);
    const clean = points.filter(
      (p, i) => i === 0 || p.x !== points[i - 1].x || p.y !== points[i - 1].y,
    );
    for (let i = 1; i < clean.length - 1;) {
      const a = clean[i - 1],
        b = clean[i],
        c = clean[i + 1];
      if ((a.x === b.x && b.x === c.x) || (a.y === b.y && b.y === c.y))
        clean.splice(i, 1);
      else i++;
    }
    const segments = clean.slice(1).map((b, i) => ({
      a: clean[i],
      b,
      length: Math.abs(clean[i].x - b.x) + Math.abs(clean[i].y - b.y),
    }));
    const ranked = [...segments].sort((a, b) => b.length - a.length);
    let label: Point | undefined;
    for (const segment of ranked) {
      const fractions = [
        0.5,
        0.25,
        0.75,
        ...Array.from(
          { length: Math.floor(segment.length / 20) },
          (_, i) => ((i + 1) * 20) / segment.length,
        ),
      ];
      for (const fraction of fractions) {
        const center = {
          x: segment.a.x + (segment.b.x - segment.a.x) * fraction,
          y: segment.a.y + (segment.b.y - segment.a.y) * fraction - 10,
        };
        for (const shift of segment.a.x === segment.b.x ? [0, 34, -34] : [0]) {
          const candidate = { x: center.x + shift, y: center.y };
          if (
            candidate.x - 29 < 4 ||
            candidate.x + 29 > bounds.width - 4 ||
            candidate.y - 14 < 4 ||
            candidate.y + 8 > bounds.height - 4
          )
            continue;
          if (
            boxes.some(
              (b) =>
                candidate.x + 29 > b.x - 3 &&
                candidate.x - 29 < b.x + b.width + 3 &&
                candidate.y + 8 > b.y - 3 &&
                candidate.y - 14 < b.y + b.height + 3,
            )
          )
            continue;
          if (
            separateReturn &&
            returnLabels.some(
              (p) =>
                Math.abs(p.x - candidate.x) < 62 &&
                Math.abs(p.y - candidate.y) < 28,
            )
          )
            continue;
          label = candidate;
          break;
        }
        if (label) break;
      }
      if (label) break;
    }
    const longest = ranked[0];
    const result = {
      path: clean.map((p, i) => `${i ? "L" : "M"} ${p.x} ${p.y}`).join(" "),
      ...(label || {
        x: (longest.a.x + longest.b.x) / 2,
        y: (longest.a.y + longest.b.y) / 2 - 10,
      }),
    };
    routes.set(key, result);
    if (separateReturn) {
      returnSegments.push(...segments);
      returnLabels.push({ x: result.x, y: result.y });
      returnCounts.set(column, (returnCounts.get(column) || 0) + 1);
    }
    allSegments.push(...segments);
    return result;
  };
}
