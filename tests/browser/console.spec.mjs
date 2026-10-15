import { test, expect } from "@playwright/test";
const id = "012345abcdef";
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (r) =>
    ["127.0.0.1", "localhost"].includes(new URL(r.request().url()).hostname)
      ? r.continue()
      : r.abort("blockedbyclient"),
  );
});
async function openDecision(page) {
  await page.goto("/#project=" + id + "&tab=decisions");
  await expect(page.locator(".decision-section")).toBeVisible();
}
test("human story to visual selection to final review has concrete bounded actions", async ({
  page,
  request,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await request.post("/__test/scenario", { data: { kind: "story" } });
  await openDecision(page);
  await expect(
    page.locator('[data-action="decide"][data-type="story"]'),
  ).toHaveCount(2);
  await expect(page.locator("#workspace")).not.toContainText("未通过的方案");
  await page.screenshot({
    path: "runtime/browser-report/console-story-decision.png",
    fullPage: true,
  });
  await page.locator('[data-action="decide"][data-choice="A"]').click();
  await expect(
    page.locator('[data-action="decide"][data-type="style"]'),
  ).toHaveCount(2);
  await expect
    .poll(() =>
      page
        .locator(".choice-image img")
        .first()
        .evaluate((img) => img.complete && img.naturalWidth > 0),
    )
    .toBeTruthy();
  await page.screenshot({
    path: "runtime/browser-report/console-style-decision.png",
    fullPage: true,
  });
  await page.locator('[data-action="decide"][data-choice="ink"]').click();
  await expect(page.locator('form[data-form="final"]')).toBeVisible();
  await page
    .locator('form[data-form="final"]')
    .evaluate((form) =>
      form.dispatchEvent(
        new SubmitEvent("submit", { bubbles: true, cancelable: true }),
      ),
    );
  await expect(page.locator(".decision-inline-error")).toContainText(
    "请明确选择",
  );
  let calls = await (await request.get("/__test/calls")).json();
  expect(calls.filter((c) => c.path.endsWith("/decision"))).toHaveLength(2);
  await page.locator('form[data-form="final"] [value="approve"]').click();
  await expect(page.locator(".project-meta")).toContainText("已通过");
  calls = await (await request.get("/__test/calls")).json();
  const mutations = calls.filter((c) => c.path.endsWith("/decision"));
  expect(mutations).toHaveLength(3);
  expect(mutations.map((c) => c.body.type)).toEqual([
    "story",
    "style",
    "final",
  ]);
  expect(
    mutations.every((c) => Number.isInteger(c.body.expected_revision)),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
test("creative blocker exposes review evidence, suggested edits, and preserves stale drafts", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "creative" } });
  await openDecision(page);
  await expect(page.locator(".decision-issue")).toContainText(
    "人物突然选择救助",
  );
  await page.locator('[data-action="use-review-feedback"]').click();
  const field = page.locator('form[data-form="retry"] textarea');
  await expect(field).toHaveValue(/S03/);
  const original = await field.inputValue();
  const old = await page
    .locator('form[data-form="retry"]')
    .getAttribute("data-project-revision");
  await page.screenshot({
    path: "runtime/browser-report/console-review-blocked.png",
    fullPage: true,
  });
  await request.post("/__test/progress");
  await page.locator('.topbar [data-action="refresh"]').click();
  await expect(page.locator(".decision-draft-stale")).toBeVisible();
  await expect(field).toHaveValue(original);
  await expect(page.locator('form[data-form="retry"]')).toHaveAttribute(
    "data-project-revision",
    old,
  );
  await expect(
    page.locator('form[data-form="retry"] button[type="submit"]'),
  ).toBeDisabled();
  expect(
    (await (await request.get("/__test/calls")).json()).filter((c) =>
      c.path.endsWith("/action"),
    ),
  ).toHaveLength(0);
  await expect(field).toHaveValue(original);
  await page.locator('[data-action="confirm-decision-revision"]').click();
  await page.locator('form[data-form="retry"] button[type="submit"]').click();
  await expect(page.locator(".project-meta")).toContainText("排队中");
  const calls = await (await request.get("/__test/calls")).json();
  const actions = calls.filter((c) => c.path.endsWith("/action"));
  expect(actions).toHaveLength(1);
  expect(actions[0].body.expected_revision).toBe(Number(old) + 1);
  expect(actions[0].body.feedback).toBe(original);
});
test("automatic supervisor gate is not a phantom human decision", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "automatic" } });
  await page.goto("/");
  await expect(page.locator('[data-metric="1"]')).toHaveText("0");
  await page.locator('#project-list [data-id="' + id + '"]').click();
  await page.getByRole("button", { name: "展开完整工作流" }).click();
  await expect(page.locator('[data-flow-node="story_decision"]')).toContainText(
    "执行中",
  );
  await expect(page.locator(".project-attention-banner")).toHaveCount(0);
  await page.locator('.tabbar [data-tab="decisions"]').click();
  await expect(page.locator("#workspace")).toContainText("当前没有人工待办");
  await expect(page.locator('[data-action="decide"]')).toHaveCount(0);
});
test("dashboard navigation filters and mobile viewport remain functional", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".metric-card")).toHaveCount(4);
  await page.locator('.main-nav [data-view="projects"]').click();
  await page.locator("[data-console-search]").fill("不会存在的项目");
  await expect(page.locator(".console-project-table tbody tr")).toHaveCount(0);
  await page.locator("[data-console-search]").fill("浏览器");
  await expect(page.locator(".console-project-table tbody tr")).toHaveCount(1);
  await page.locator('.main-nav [data-view="inbox"]').click();
  await page.locator(".attention-row button").click();
  await expect(page.locator('form[data-form="final"]')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "runtime/browser-report/console-decision-mobile.png",
    fullPage: true,
  });
  await page.locator('[data-action="toggle-navigation"]').click();
  await page.locator('.main-nav [data-view="activity"]').click();
  await expect(page.locator("#workspace h1")).toHaveText("运行记录");
});
