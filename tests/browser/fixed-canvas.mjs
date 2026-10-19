import { expect } from "@playwright/test";

// Preparation may intentionally scroll the canvas to an offscreen phase. Selection
// invariance is measured only after the target is onscreen, as with a real pointer.
export async function expandRole(page, role, phase) {
  const canvas = page.locator(".workflow-fixed-canvas");
  const stages = phase
    ? canvas.locator(`[data-stage="${phase}"]`)
    : canvas.locator("[data-stage]");
  await expect(stages.first()).toBeAttached();
  for (const stage of await stages.all()) {
    const root = stage.locator("..");
    const disclosure = root.locator(":scope > button[aria-expanded]");
    if (
      (await disclosure.count()) &&
      (await disclosure.getAttribute("aria-expanded")) === "false"
    ) {
      await disclosure.click();
      await expect(disclosure).toHaveAttribute("aria-expanded", "true");
    }
  }
  for (const button of await canvas
    .locator(".kind-department > button[aria-expanded]")
    .all())
    if ((await button.getAttribute("aria-expanded")) === "false") {
      await button.click();
      await expect(button).toHaveAttribute("aria-expanded", "true");
    }
  const target = canvas.locator(`[data-role="${role}"]`).first();
  await expect(target).toBeAttached();
  await target.scrollIntoViewIfNeeded();
  return target;
}
export async function canvasState(page) {
  return page.locator(".workflow-canvas-viewport").evaluate((el) => ({
    left: el.scrollLeft,
    top: el.scrollTop,
    width: el.clientWidth,
    height: el.clientHeight,
    board: el.querySelector(".workflow-canvas-board").getAttribute("style"),
    nodes: [...el.querySelectorAll("[data-map-node]")].map((n) => [
      n.dataset.mapNode,
      n.getAttribute("style"),
    ]),
  }));
}
export async function pointerEdge(page, edge) {
  await edge.scrollIntoViewIfNeeded();
  const point = await edge.evaluate((el) => {
    const path = el.querySelector(".workflow-flow-line"),
      matrix = path.getScreenCTM();
    const viewport = el
      .closest(".workflow-canvas-viewport")
      .getBoundingClientRect();
    for (
      let distance = 5;
      distance < path.getTotalLength() - 5;
      distance += 3
    ) {
      const p = path.getPointAtLength(distance).matrixTransform(matrix);
      if (
        p.x < viewport.left + 4 ||
        p.x > viewport.right - 4 ||
        p.y < Math.max(viewport.top + 4, 0) ||
        p.y > Math.min(viewport.bottom - 4, innerHeight)
      )
        continue;
      const hit = document
        .elementFromPoint(p.x, p.y)
        ?.closest("[data-flow-edge]");
      if (hit === el) return { x: p.x, y: p.y };
    }
  });
  expect(point, "visible line must have a real pointer target").toBeTruthy();
  const before = await canvasState(page);
  await page.mouse.click(point.x, point.y);
  expect(await canvasState(page)).toEqual(before);
}

export async function assertUnobstructedLines(page) {
  const intersections = await page
    .locator(".workflow-canvas-board")
    .evaluate((board) => {
      const nodes = [...board.querySelectorAll("[data-map-node]")].map((n) =>
        n.getBoundingClientRect(),
      );
      return [...board.querySelectorAll(".workflow-flow-line")].flatMap(
        (path) => {
          const matrix = path.getScreenCTM();
          for (
            let distance = 3;
            distance < path.getTotalLength() - 3;
            distance += 3
          ) {
            const p = path.getPointAtLength(distance).matrixTransform(matrix);
            if (
              nodes.some(
                (b) =>
                  p.x > b.left + 2 &&
                  p.x < b.right - 2 &&
                  p.y > b.top + 2 &&
                  p.y < b.bottom - 2,
              )
            )
              return [path.parentElement.dataset.flowEdge];
          }
          return [];
        },
      );
    });
  expect(intersections).toEqual([]);
}

export async function openDetails(page, view) {
  const card = page.locator("#workflow-detail-card");
  if (!(await card.count())) {
    const before = await canvasState(page);
    await page
      .getByRole("button", { name: "查看完整详情", exact: true })
      .click();
    await expect(card).toBeVisible();
    expect(await canvasState(page)).toEqual(before);
  }
  if (view === "records")
    await card.getByRole("button", { name: "执行记录", exact: true }).click();
  return card;
}
export async function closeDetails(page) {
  const card = page.locator("#workflow-detail-card");
  if (await card.count()) {
    const before = await canvasState(page);
    await card
      .locator(":scope > .dialog-head")
      .getByRole("button", { name: "关闭", exact: true })
      .click();
    await expect(card).toHaveCount(0);
    expect(await canvasState(page)).toEqual(before);
  }
}
