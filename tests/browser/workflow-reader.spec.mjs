import { expandRole, canvasState } from "./fixed-canvas.mjs";
import { refreshPage } from "./header-flow.mjs";
import { openWorkflow } from "./navigation.mjs";
import { test, expect } from "@playwright/test";
const id = "012345abcdef";
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});
for (const width of [1440, 1024, 390]) {
  test(`five stages and readable I/O at ${width}px`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`/#project=${id}&tab=overview`);
    await openWorkflow(page);
    const inspector = page.getByRole("complementary", { name: "节点详情" });
    await expect(page.locator("[data-stage]")).toHaveCount(5);
    for (const card of await page
      .locator(".workflow-fixed-node-select > strong")
      .all()) {
      expect(
        await card.evaluate((el) => el.getBoundingClientRect().height),
      ).toBeLessThan(40);
    }
    await expect(page.locator(".workflow-flow-line")).toHaveCount(7);
    await page.locator(".workflow-canvas-viewport").screenshot({
      path: `runtime/browser-report/workflow-stages-${width}.png`,
    });
    const routing = await page.locator(".workflow-map").evaluate((map) => {
      const bounds = map.getBoundingClientRect();
      const boxes = [...map.querySelectorAll("[data-map-node]")].map((n) =>
        n.getBoundingClientRect(),
      );
      const nodesFit = boxes.every(
        (b) =>
          b.left >= bounds.left &&
          b.right <= bounds.right &&
          b.top >= bounds.top &&
          b.bottom <= bounds.bottom,
      );
      const intersections = [];
      for (const path of map.querySelectorAll(".workflow-flow-line")) {
        for (
          let distance = 2;
          distance < path.getTotalLength() - 2;
          distance += 4
        ) {
          const point = path
              .getPointAtLength(distance)
              .matrixTransform(path.getScreenCTM()),
            x = point.x,
            y = point.y;
          if (
            boxes.some(
              (b) =>
                x > b.left + 2 &&
                x < b.right - 2 &&
                y > b.top + 2 &&
                y < b.bottom - 2,
            )
          )
            intersections.push(path.parentElement.dataset.flowEdge);
        }
      }
      return { nodesFit, intersections: [...new Set(intersections)] };
    });
    expect(routing).toEqual({ nodesFit: true, intersections: [] });
    await expect(page.locator(".react-flow")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "展开完整工作流" }),
    ).toHaveCount(0);
    for (const stage of [
      "story",
      "script",
      "visual",
      "production",
      "delivery",
    ]) {
      await page.locator(`[data-stage="${stage}"]`).click();
      await expect(page.locator(`[data-stage="${stage}"]`)).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      const steps = page
        .getByRole("navigation", { name: "阶段内步骤" })
        .locator("button[data-flow-node]:visible");
      for (let i = 0; i < (await steps.count()); i++) {
        const label = await steps
          .nth(i)
          .evaluate((el) => el.childNodes[0].textContent);
        await steps.nth(i).click();
        await expect(inspector.locator(".workflow-step-heading h3")).toHaveText(
          label,
        );
      }
    }
    await page.locator('[data-stage="script"]').click();
    await page.locator('[data-flow-node="screenplay"]').click();
    const inputs = inspector.getByRole("region", { name: "输入材料" });
    await inputs.getByRole("button", { name: "阅读文件" }).click();
    await expect(
      inputs.getByRole("heading", { name: "故事目标全文" }),
    ).toBeVisible();
    await expect(inputs).not.toContainText("尚未生成");
    const script = inspector
      .getByRole("region", { name: "交付内容" })
      .locator(".material-card")
      .filter({ has: page.getByText("剧本正文", { exact: true }) });
    await script.getByRole("button", { name: "阅读文件" }).click();
    await expect(
      script.getByRole("heading", { name: "灯下的约定" }),
    ).toBeVisible();
    await expect(script.getByRole("table")).toContainText("我答应过你");
    await request.post("/__test/progress");
    await refreshPage(page);
    await expect(inspector.locator(".workflow-step-heading h3")).toHaveText(
      "剧本创作",
    );
    await expect(
      script.getByRole("heading", { name: "灯下的约定" }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBeTruthy();
    await page
      .locator(".workflow-workspace")
      .screenshot({ path: `runtime/browser-report/workflow-${width}.png` });
    await page
      .locator(".workflow-workspace")
      .evaluate((el) => el.scrollIntoView({ block: "start" }));
    await page.screenshot({
      path: `runtime/browser-report/workflow-overview-${width}.png`,
    });
    await script.getByRole("button", { name: "放大阅读" }).click();
    await expect(page.getByRole("dialog").getByRole("table")).toContainText(
      "S02",
    );
    await page.getByRole("dialog").screenshot({
      path: `runtime/browser-report/workflow-document-${width}.png`,
    });
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "关闭", exact: true })
      .click();
    await page.locator('[data-branch="revision"]').click();
    await expect(inspector).toContainText("返修后接续");
    await inspector
      .locator(".workflow-transitions")
      .getByRole("button", { name: "镜头与后期" })
      .click();
    await expect(inspector.locator(".workflow-step-heading h3")).toHaveText(
      "镜头与后期",
    );
    const calls = await (await request.get("/__test/calls")).json();
    expect(
      calls.filter((c) => c.method === "POST" && c.path.startsWith("/api/")),
    ).toEqual([]);
    expect(errors).toEqual([]);
  });
}
test("call attempts expose exact originals and history can locate its step", async ({
  page,
}) => {
  await page.goto(`/#project=${id}&tab=team`);
  const call = page.locator('[data-execution="writer-current"]');
  await expect(call).toContainText("当前步骤执行");
  await call.getByText("第 1 次请求与响应", { exact: true }).click();
  const request = call
    .locator(".material-card")
    .filter({ has: page.getByText("请求原文", { exact: true }) })
    .first();
  await request.getByRole("button", { name: "阅读文件" }).click();
  await expect(request).toContainText("这是当次保存的完整输入");
  await expect(request).toContainText("请求原文中的提示词");
  await call.getByText("第 2 次请求与响应", { exact: true }).click();
  const response = call
    .locator("details", {
      has: page.locator("summary", { hasText: /^第 2 次请求与响应$/ }),
    })
    .locator(".material-card")
    .filter({ has: page.getByText("响应原文", { exact: true }) });
  await response.getByRole("button", { name: "阅读文件" }).click();
  await expect(response).toContainText("第二次输出已完成");
  await expect(page.locator('[data-execution="writer-failed"]')).toContainText(
    "执行失败",
  );
  await expect(page.locator('[data-execution="writer-legacy"]')).toContainText(
    "未记录步骤归属",
  );
  await call.getByRole("button", { name: "定位步骤与记录" }).click();
  await expect(page.locator(".workflow-step-heading h3")).toHaveText(
    "剧本创作",
  );
  await expect(page.locator(".execution-record-focus")).toContainText(
    "mock-writer-current",
  );
});
test("missing files retry and oversized Markdown remains downloadable", async ({
  page,
}) => {
  await page.goto(`/#project=${id}&tab=overview`);
  await openWorkflow(page);
  await page.locator('[data-stage="script"]').click();
  await page.locator('[data-flow-node="screenplay"]').click();
  const missing = page
    .locator(".material-card")
    .filter({ has: page.getByText("缺失的旧稿", { exact: true }) });
  await missing.getByRole("button", { name: "阅读文件" }).click();
  await expect(missing.getByRole("alert")).toContainText("文件缺失");
  await page.route("**/planning/missing.md", (r) =>
    r.fulfill({
      status: 200,
      contentType: "text/markdown",
      body: "# 文件已恢复",
    }),
  );
  await missing.getByRole("button", { name: "重新读取" }).click();
  await expect(
    missing.getByRole("heading", { name: "文件已恢复" }),
  ).toBeVisible();
  const script = page
    .locator(".material-card")
    .filter({ has: page.getByText("剧本正文", { exact: true }) });
  await page.route("**/planning/script.md", (r) =>
    r.fulfill({
      status: 200,
      contentType: "text/markdown",
      body: "x".repeat(2 * 1024 * 1024 + 1),
    }),
  );
  await script.getByRole("button", { name: "阅读文件" }).click();
  await expect(script.getByRole("alert")).toContainText("2 MiB");
  await expect(script.getByRole("link", { name: "下载原文件" })).toBeVisible();
});

test("workflow and document controls work with keyboard navigation", async ({
  page,
}) => {
  await page.goto(`/#project=${id}&tab=overview`);
  await openWorkflow(page);
  await page.locator('[data-stage="script"]').focus();
  await page.keyboard.press("Enter");
  await page.locator('[data-flow-node="screenplay"]').focus();
  await page.keyboard.press("Space");
  await expect(page.locator(".workflow-step-heading h3")).toHaveText(
    "剧本创作",
  );
  const inputs = page.getByRole("region", { name: "输入材料" });
  await inputs.getByRole("button", { name: "阅读文件" }).focus();
  await page.keyboard.press("Enter");
  await expect(
    inputs.getByRole("heading", { name: "故事目标全文" }),
  ).toBeVisible();
  await inputs.getByRole("button", { name: "放大阅读" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("data flow does not invent a transmission without receiving-task evidence", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "repairing" } });
  await page.goto(`/#project=${id}&tab=workflow`);
  await expect(page.locator('[data-stage="script"]')).toContainText("执行中");
  await expect(page.locator(".workflow-flow-stream")).toHaveCount(0);
  const pause = async (route) => {
    const response = await route.fetch(),
      body = await response.json();
    body.project.status = "paused";
    body.workflow.nodes = body.workflow.nodes.map((n) =>
      n.id === body.workflow.active_node ? { ...n, status: "paused" } : n,
    );
    await route.fulfill({ response, json: body });
  };
  await page.route("**/workspace", pause);
  await refreshPage(page);
  await expect(page.locator('[data-flow-state="running"]')).toHaveCount(0);
  await page.unroute("**/workspace", pause);
  await request.post("/__test/scenario", { data: { kind: "final" } });
  await refreshPage(page);
  await expect(page.locator('[data-stage="delivery"]')).toContainText(
    "等待你处理",
  );
  await expect(page.locator(".workflow-flow-stream")).toHaveCount(0);
});

test("narrow screens keep the canvas and reader in place", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#project=${id}&tab=workflow`);
  const stage = page.locator('[data-stage="script"]');
  await stage.scrollIntoViewIfNeeded();
  const before = await canvasState(page);
  await stage.click();
  await expect(page.locator(".workflow-step-heading h3")).toHaveText(
    "故事目标落实与独立审核",
  );
  expect(await canvasState(page)).toEqual(before);
  await page.getByRole("button", { name: "关闭详情", exact: true }).click();
  expect(await canvasState(page)).toEqual(before);
  await expect(page.locator(".workflow-canvas-viewport")).toBeVisible();
});

test("department employee opens the recorded original request in its own workflow", async ({
  page,
}) => {
  await page.goto(`/#project=${id}&tab=workflow`);
  await expect(
    page.getByRole("heading", { name: "制作工作流", exact: true }),
  ).toBeVisible();
  await (await expandRole(page, "screenwriter", "script")).click();
  const employee = page.getByRole("complementary", { name: "节点详情" });
  const call = employee.locator('[data-execution="writer-current"]');
  await expect(call).toContainText("mock-writer-current");
  await call.getByText("第 1 次请求与响应", { exact: true }).click();
  const original = call
    .locator(".material-card")
    .filter({ has: page.getByText("请求原文", { exact: true }) })
    .first();
  await original.getByRole("button", { name: "阅读文件", exact: true }).click();
  await expect(original).toContainText("这是当次保存的完整输入");
  await expect(original).toContainText("请求原文中的提示词");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
});

test("department delivery opens a saved sender result inline without claiming a receiver call", async ({
  page,
}) => {
  await page.goto(`/#project=${id}&tab=workflow`);
  await expandRole(page, "screenwriter");
  const diagram = page.locator(".workflow-canvas-board");
  const edge = diagram
    .locator(
      '[data-flow-edge][data-source="screenwriter"][data-target="independent_critic"]',
    )
    .first();
  await edge.focus();
  await page.keyboard.press("Enter");
  const panel = page.getByRole("complementary", { name: "节点详情" });
  await expect(panel).toContainText("尚未关联到接收方的实际输入");
  const call = panel.locator('[data-execution="writer-current"]');
  const original = call
    .locator(".material-card")
    .filter({ has: page.getByText("解析结果", { exact: true }) });
  await original.getByRole("button", { name: "阅读文件", exact: true }).click();
  await expect(original).toContainText("灯下的约定");
  await expect(diagram).toBeVisible();
  expect(page.url()).toContain(`project=${id}&tab=workflow`);
});
