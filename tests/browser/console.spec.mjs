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
test("review buttons submit actual repair requirements without typed notes", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "creative" } });
  await openDecision(page);
  const form = page.locator('form[data-form="retry"]');
  await form.getByRole("button", { name: "按全部审核建议修改" }).click();
  const issue = form.getByRole("button", { name: "S03 · 补齐人物动机" });
  await issue.click();
  await expect(
    form.getByRole("button", { name: "按全部审核建议修改" }),
  ).toHaveAttribute("aria-pressed", "false");
  await expect(issue).toHaveAttribute("aria-pressed", "true");
  await expect(form.locator("textarea")).toHaveValue("");
  expect(
    (await (await request.get("/__test/calls")).json()).filter((c) =>
      c.path.endsWith("/action"),
    ),
  ).toHaveLength(0);
  await form.getByRole("button", { name: "提交修改并重新审核" }).click();
  await expect(page.locator(".project-meta")).toContainText("排队中");
  const actions = (await (await request.get("/__test/calls")).json()).filter(
    (c) => c.path.endsWith("/action"),
  );
  expect(actions).toHaveLength(1);
  expect(actions[0].body).toMatchObject({
    action: "provide_feedback",
    feedback:
      "S03 补充人物此前的承诺，让救助行为有明确动机，修改后重新独立审核。",
  });
  expect(Number.isInteger(actions[0].body.expected_revision)).toBeTruthy();
});

test("final review choices support keyboard and narrow screens without typing", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "final" } });
  await page.setViewportSize({ width: 390, height: 844 });
  await openDecision(page);
  const form = page.locator('form[data-form="final"]');
  const choice = form.getByRole("button", { name: "调整剪辑节奏" });
  await choice.focus();
  await page.keyboard.press("Space");
  await expect(choice).toHaveAttribute("aria-pressed", "true");
  await expect(form.locator("textarea")).toHaveValue("");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "runtime/browser-report/review-options-mobile.png",
    fullPage: true,
  });
  await form.getByRole("button", { name: "重新剪辑", exact: true }).click();
  const mutations = (await (await request.get("/__test/calls")).json()).filter(
    (c) => c.path.endsWith("/decision"),
  );
  expect(mutations).toHaveLength(1);
  expect(mutations[0].body).toMatchObject({
    type: "final",
    choice: "reedit",
    feedback: "缩短重复空镜，保留完整对白，调整镜头衔接。",
  });
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

test("automatic review displays its bounded repair round without a human blocker", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "repairing" } });
  await page.goto("/#project=" + id + "&tab=decisions");
  await expect(page.locator(".review-automation")).toContainText(
    "第 3 / 4 轮审核",
  );
  await expect(page.locator(".review-automation")).toContainText(
    "正在按审核意见返修",
  );
  await expect(
    page.getByText("当前没有人工待办", { exact: true }),
  ).toBeVisible();
  const calls = await (await request.get("/__test/calls")).json();
  expect(calls.filter((c) => c.path.endsWith("/action"))).toHaveLength(0);
});

test("AI advice is requested separately and only adopted by an explicit producer action", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "review_assist" } });
  await openDecision(page);
  await page
    .getByRole("button", { name: "请 AI 分析并推荐", exact: true })
    .click();
  await expect(page.locator(".review-advisor")).toContainText(
    "缺少救助动机，建议保留故事方向补齐过渡。",
  );
  let calls = (await (await request.get("/__test/calls")).json()).filter((c) =>
    c.path.endsWith("/action"),
  );
  expect(calls).toHaveLength(1);
  expect(calls[0].body.action).toBe("request_review_advice");
  await expect(page.locator(".review-primary-action textarea")).toHaveValue("");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "runtime/browser-report/ai-review-advice-mobile.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "采纳 AI 方案并继续返修", exact: true })
    .click();
  await expect(page.locator(".project-meta")).toContainText("排队中");
  calls = (await (await request.get("/__test/calls")).json()).filter((c) =>
    c.path.endsWith("/action"),
  );
  expect(calls).toHaveLength(2);
  expect(calls[1].body).toMatchObject({
    action: "provide_feedback",
    feedback: "在 S03 前增加人物发现危险的动作，保留其他镜头，再交独立审核。",
  });
  expect(calls[1].body.expected_revision).toBeGreaterThan(
    calls[0].body.expected_revision,
  );
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
