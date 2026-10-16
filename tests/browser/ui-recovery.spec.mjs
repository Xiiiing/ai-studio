import { test, expect } from "@playwright/test";

const projectId = "012345abcdef";
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort("blockedbyclient"),
  );
});

for (const scenario of [
  {
    name: "dashboard",
    hash: "view=dashboard",
    route: "/console",
    retry: "重新读取工作室",
    ready: ".dashboard-grid",
  },
  {
    name: "models",
    hash: "view=models",
    route: "/models",
    retry: "重新读取模型配置",
    ready: ".model-card",
  },
  {
    name: "project overview",
    hash: `project=${projectId}&tab=overview`,
    route: "/workspace",
    retry: "重新读取项目",
    ready: ".project-context",
  },
  {
    name: "project shots",
    hash: `project=${projectId}&tab=shots`,
    route: "/shots",
    retry: "重新读取镜头",
    ready: ".shot-workbench",
  },
]) {
  test(`${scenario.name} recovers from an initial API failure through its retry action`, async ({
    page,
    request,
  }) => {
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await request.post("/__test/failure", { data: { route: scenario.route } });
    await page.goto("/#" + scenario.hash);
    await expect(page.getByRole("alert").first()).toContainText(
      "模拟服务暂时不可用",
    );
    const retry = page.getByRole("button", {
      name: scenario.retry,
      exact: true,
    });
    await expect(retry).toBeVisible();
    await expect(page.locator("main .loading")).toHaveCount(0);
    const before = (await (await request.get("/__test/calls")).json()).filter(
      (call) => call.method === "GET" && call.path.endsWith(scenario.route),
    ).length;
    await request.post("/__test/failure", { data: { route: "" } });
    await retry.click();
    await expect(page.locator(scenario.ready).first()).toBeVisible();
    await expect(page.locator("main .loading")).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(retry).toHaveCount(0);
    const after = (await (await request.get("/__test/calls")).json()).filter(
      (call) => call.method === "GET" && call.path.endsWith(scenario.route),
    ).length;
    expect(after).toBeGreaterThan(before);
    expect(errors).toEqual([]);
  });
}

test("mobile workflow keyboard selection transfers focus to details and returns to the selected stage", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/#project=${projectId}&tab=overview`);
  const stage = page.locator('[data-stage="script"]');
  await expect(stage).toBeVisible();
  await stage.focus();
  await page.keyboard.press("Enter");
  const inspector = page.getByRole("complementary", { name: "节点详情" });
  await expect(page.locator(".workflow-map-container")).toBeHidden();
  await expect(inspector).toBeVisible();
  await expect(inspector).toBeFocused();
  await inspector.getByRole("button", { name: "返回流程图" }).click();
  await expect(page.locator(".workflow-map-container")).toBeVisible();
  await expect(stage).toHaveAttribute("aria-pressed", "true");
  await expect(stage).toBeFocused();
});

test("composition dragging previews the region, saves normalized coordinates, and cancels a later drag", async ({
  page,
}) => {
  const composition = {
    asset_id: "composition_browser",
    version: 1,
    library_key: "global",
    kind: "composition",
    status: "approved",
    data: {
      document: {
        name: "双人参考",
        description: "用于验证浏览器真实指针框选的离线组合素材。",
        members: [{ asset_id: "character_browser", version: 1, role: "lead" }],
      },
      images: [
        {
          path: `production/factory-${projectId}/frames/frame.png`,
          label: "同框参考",
        },
      ],
    },
  };
  const character = {
    asset_id: "character_browser",
    version: 1,
    library_key: "global",
    kind: "character",
    status: "approved",
    data: {
      document: { name: "成年主角", identity_constraints: "离线测试人物" },
      images: [],
    },
  };
  const posts = [];
  await page.route("**/api/factory/library**", async (route) => {
    const incoming = route.request();
    if (new URL(incoming.url()).pathname.endsWith("/tasks"))
      return route.fulfill({ json: { tasks: [] } });
    if (incoming.method() === "GET")
      return route.fulfill({
        json: { assets: [composition, character], has_more: false },
      });
    const body = incoming.postDataJSON();
    posts.push(body);
    expect(body.action).toBe("revise");
    expect(body.asset_id).toBe(composition.asset_id);
    expect(body.expected_version).toBe(1);
    composition.data.document = body.document;
    composition.version = 2;
    return route.fulfill({ json: { asset: composition } });
  });
  await page.goto("/#view=library");
  const card = page.locator(".panel").filter({
    has: page.getByRole("heading", { name: "双人参考 · v1", exact: true }),
  });
  await card
    .getByRole("button", { name: "编辑 / 添加图片", exact: true })
    .click();
  const editor = page.locator(".material-editor");
  const image = editor.getByRole("img", { name: "同框人物区域标注" });
  const region = editor.locator(".material-region svg");
  await region.scrollIntoViewIfNeeded();
  await expect
    .poll(() =>
      image.evaluate((element) => element.complete && element.naturalWidth > 0),
    )
    .toBe(true);
  const imageBounds = await image.boundingBox();
  const regionBounds = await region.boundingBox();
  expect(imageBounds).not.toBeNull();
  expect(regionBounds).not.toBeNull();
  for (const axis of ["x", "y", "width", "height"])
    expect(regionBounds[axis]).toBeCloseTo(imageBounds[axis], 0);
  const at = (x, y) => ({
    x: regionBounds.x + regionBounds.width * x,
    y: regionBounds.y + regionBounds.height * y,
  });
  const begin = at(0.2, 0.2),
    end = at(0.7, 0.6);
  await page.mouse.move(begin.x, begin.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 8 });
  await expect(editor.locator(".region-draft")).toBeVisible();
  const draft = await editor
    .locator(".region-draft")
    .evaluate((element) =>
      ["x", "y", "width", "height"].map((name) =>
        Number(element.getAttribute(name)),
      ),
    );
  for (const [index, value] of [20, 20, 50, 40].entries())
    expect(draft[index]).toBeCloseTo(value, 0);
  await page.mouse.up();
  await expect(editor.locator(".region-draft")).toHaveCount(0);
  const inputs = ["左", "上", "右", "下"].map((side) =>
    editor.getByLabel(`人物1${side}边界`),
  );
  const savedBox = await Promise.all(
    inputs.map(async (input) => Number(await input.inputValue())),
  );
  for (const [index, value] of [0.2, 0.2, 0.7, 0.6].entries())
    expect(savedBox[index]).toBeCloseTo(value, 2);
  const again = at(0.3, 0.3),
    canceled = at(0.8, 0.8);
  await page.mouse.move(again.x, again.y);
  await page.mouse.down();
  await page.mouse.move(canceled.x, canceled.y, { steps: 5 });
  await expect(editor.locator(".region-draft")).toBeVisible();
  await region.dispatchEvent("pointercancel", {
    pointerId: 1,
    pointerType: "mouse",
  });
  await expect(editor.locator(".region-draft")).toHaveCount(0);
  await page.mouse.up();
  expect(
    await Promise.all(
      inputs.map(async (input) => Number(await input.inputValue())),
    ),
  ).toEqual(savedBox);
  for (const input of inputs)
    expect(await input.evaluate((element) => element.validity.valid)).toBe(true);
  await editor.getByRole("button", { name: "保存素材", exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].document.members[0].box).toEqual(savedBox);
  expect(posts[0].project_id).toBeUndefined();
  await expect(
    page.getByRole("heading", { name: "双人参考 · v2", exact: true }),
  ).toBeVisible();
});
