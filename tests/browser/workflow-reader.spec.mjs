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
    const inspector = page.getByRole("complementary", { name: "节点详情" });
    await expect(page.locator("[data-stage]")).toHaveCount(5);
    for (const card of await page
      .locator(".workflow-stage-copy > strong")
      .all()) {
      expect(
        await card.evaluate((el) => el.getBoundingClientRect().height),
      ).toBeLessThan(40);
    }
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
        .locator("button[data-flow-node]");
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
    await page.locator('.topbar [data-action="refresh"]').click();
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
      .locator(".workflow-stages")
      .screenshot({
        path: `runtime/browser-report/workflow-stages-${width}.png`,
      });
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
    await page.getByRole("button", { name: /定向返修/ }).click();
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
    .locator("details")
    .nth(1)
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
