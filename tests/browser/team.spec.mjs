import { openDetails, closeDetails } from "./fixed-canvas.mjs";
import { refreshPage } from "./header-flow.mjs";
import { openWorkflow } from "./navigation.mjs";
import { openProjectPage } from "./navigation.mjs";
import { test, expect } from "@playwright/test";

const id = "012345abcdef";
test.beforeEach(async ({ request, page }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort("blockedbyclient"),
  );
});
test("workflow is navigable and team roles remain separate from approval", async ({
  page,
  request,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator("#workspace h1")).toHaveText("制片总览");
  await expect(page.locator(".metric-card")).toHaveCount(4);
  await page.screenshot({
    path: "runtime/browser-report/console-dashboard.png",
    fullPage: true,
  });
  await page.locator('#project-list [data-id="' + id + '"]').click();
  await openWorkflow(page);
  await expect(page.locator("[data-stage]")).toHaveCount(5);
  await page.locator('[data-stage="script"]').click();
  await openDetails(page);
  await page.locator('[data-flow-node="screenplay"]').click();
  await expect(page.locator(".workflow-detail-content h3")).toHaveText(
    "剧本创作",
  );
  await expect(page.locator(".workflow-detail-content")).toContainText(
    "独立审稿",
  );
  await expect(page.locator(".react-flow")).toHaveCount(0);
  await page.screenshot({
    path: "runtime/browser-report/console-workflow.png",
    fullPage: true,
  });
  await closeDetails(page);
  await openProjectPage(page, "team");
  await expect(page.locator(".department-card")).toHaveCount(4);
  await expect(page.locator('[data-team-task="screenplay"]')).toContainText(
    "独立审稿",
  );
  await expect(page.locator(".delivery-readiness")).toContainText(
    "重新验收文件和音画",
  );
  await expect(page.locator(".supervisor-panel")).toContainText(
    "不代表已观看成片",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".department-card").first()).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "runtime/browser-report/console-team-mobile.png",
    fullPage: true,
  });
  await request.post("/__test/failure", { data: { route: "/workspace" } });
  await refreshPage(page);
  await expect(page.locator('.feedback-region [role="alert"]')).toContainText(
    "模拟服务暂时不可用",
  );
  const calls = await (await request.get("/__test/calls")).json();
  expect(
    calls.filter((c) => c.method === "POST" && c.path.startsWith("/api/")),
  ).toEqual([]);
  expect(errors).toEqual([]);
});
test("saved optional producer binding enables delegated creation but not final approval", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.locator('[data-action="new-project"]').first().click();
  await page.getByRole("button", { name: "使用原单集入口" }).click();
  await expect(
    page.locator('#project-dialog option[value="delegated"]'),
  ).toBeEnabled();
  await expect(page.locator("#project-dialog")).toContainText(
    "总制片尚未绑定时会明确转人工接续",
  );
  await page
    .locator('#project-dialog [data-action="close-dialog"]')
    .first()
    .click();
  await page.locator('.main-nav [data-view="settings"]').click();
  await page.getByRole("button", { name: "模型与服务", exact: true }).click();
  await page.getByRole("tab", { name: "制作分工" }).click();
  await page.getByText("按角色单独配置 · 总制片代理", { exact: true }).click();
  await page.locator("#role-executive_producer").selectOption("writer");
  await page.locator('[data-model-action="save-roles"]').click();
  await expect(page.locator('[data-model-action="save-roles"]')).toBeDisabled();
  await page.locator('[data-action="new-project"]').first().click();
  await page.getByRole("button", { name: "使用原单集入口" }).click();
  await expect(
    page.locator('#project-dialog option[value="delegated"]'),
  ).toBeEnabled();
  await page
    .locator('#project-dialog [name="team_mode"]')
    .selectOption("delegated");
  await page
    .locator('#project-dialog [name="brief"]')
    .fill("成年修表师与邻居合作解开旧时钟的谜题，结局温暖。");
  await page.screenshot({
    path: "runtime/browser-report/console-create.png",
    fullPage: true,
  });
  await page.locator('#project-dialog button[type="submit"]').click();
  await expect(page.locator("#project-dialog")).not.toBeVisible();
  await expect(page.locator(".current-task")).toContainText("审看并确认终片");
  await openProjectPage(page, "decisions");
  await expect(page.locator('form[data-form="final"]')).toBeVisible();
  const calls = await (await request.get("/__test/calls")).json();
  expect(
    calls.find((c) => c.path === "/api/factory/projects" && c.method === "POST")
      ?.body.team_mode,
  ).toBe("delegated");
  expect(
    calls.filter(
      (c) =>
        c.method === "POST" && /decision|cloud|generate|\/test$/.test(c.path),
    ),
  ).toEqual([]);
});

test("a running AI project can be taken over only through its explicit safe-boundary action", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "automatic" } });
  await page.goto("/#project=" + id);
  await page.locator(".project-actions-menu > summary").click();
  await page.getByRole("button", { name: "人工介入" }).click();
  await expect(page.locator(".project-meta")).toContainText("已暂停");
  const calls = await (await request.get("/__test/calls")).json();
  const actions = calls.filter(
    (c) => c.method === "POST" && c.path.endsWith("/action"),
  );
  expect(actions).toHaveLength(1);
  expect(actions[0].body.action).toBe("take_control");
  expect(Number.isInteger(actions[0].body.expected_revision)).toBeTruthy();
  expect(
    calls.filter(
      (c) => c.method === "POST" && /decision|cloud|generate/.test(c.path),
    ),
  ).toEqual([]);
});
