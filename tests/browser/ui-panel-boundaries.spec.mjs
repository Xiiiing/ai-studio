import { test, expect } from "@playwright/test";
import {
  manageService,
  openServiceDetails,
  serviceCard,
} from "./model-flow.mjs";

const pid = "012345abcdef";
const widths = [320, 390, 768, 1440];

test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

async function groupedModels(page, count) {
  await page.route("**/api/factory/models", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const response = await route.fetch();
    const body = await response.json();
    for (let i = 1; i < count; i++) {
      const id = `writer_${i}`;
      const profile = {
        ...body.editable_profiles.writer,
        model: `fixture-model-${i}`,
      };
      body.editable_profiles[id] = profile;
      body.profiles.push({ id, ...profile });
    }
    await route.fulfill({ response, json: body });
  });
}

async function pageGeometry(page) {
  return page
    .locator("#workspace h1, #workspace .service-card-grid")
    .evaluateAll((nodes) =>
      nodes.map((node) => {
        const r = node.getBoundingClientRect();
        return { x: r.x, y: r.y + scrollY, width: r.width };
      }),
    );
}

async function stableGeometry(page, previous) {
  const current = await pageGeometry(page);
  expect(current.length).toBe(previous.length);
  current.forEach((rect, index) => {
    for (const key of ["x", "y", "width"])
      expect(
        Math.abs(rect[key] - previous[index][key]),
        `workspace ${index} ${key}`,
      ).toBeLessThanOrEqual(1);
  });
}

async function lockStyles(page) {
  return page.evaluate(() => [
    document.documentElement.style.getPropertyValue("scrollbar-gutter"),
    document.documentElement.style.getPropertyValue("overflow"),
    document.body.style.getPropertyValue("overflow"),
    document.body.style.getPropertyValue("padding-inline-end"),
  ]);
}

async function insideVisibleDialog(control, dialog, { aboveFooter } = {}) {
  await expect(control).toBeVisible();
  const box = await control.boundingBox();
  const bounds = await dialog.boundingBox();
  const header = await dialog.locator(":scope > .dialog-head").boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(bounds.x - 1);
  expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width + 1);
  expect(box.y).toBeGreaterThanOrEqual(header.y + header.height - 1);
  expect(box.y + box.height).toBeLessThanOrEqual(bounds.y + bounds.height + 1);
  if (aboveFooter) {
    const footer = await aboveFooter.boundingBox();
    expect(
      box.y + box.height,
      "control must not hide behind the sticky save bar",
    ).toBeLessThanOrEqual(footer.y + 1);
  }
}

for (const width of widths) {
  test(`project modal footer and nested errors stay reachable at ${width}px`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/#view=projects");
    await expect(page.locator("#workspace h1")).toHaveText("全部项目");
    const styles = await lockStyles(page);
    const anchors = await pageGeometry(page);
    await page
      .locator("#workspace")
      .getByRole("button", { name: "新建制作", exact: true })
      .click();
    // Creation now starts with a whole drama. Keep testing the legacy JSON
    // validation and nested error dialog through its actual user-facing entry.
    const drama = page.getByRole("dialog", { name: "新建一部剧", exact: true });
    await expect(drama).toBeVisible();
    await drama.getByRole("button", { name: "使用原单集入口", exact: true }).click();
    await expect(drama).toHaveCount(0);
    const dialog = page.locator("#project-dialog");
    await dialog
      .getByLabel("创作方向", { exact: true })
      .fill("隔离浏览器验证：保留创作草稿。");
    await dialog
      .locator("summary")
      .filter({ hasText: /^预算、系列与研究资料$/ })
      .click();
    const lastField = dialog.getByLabel("稳定角色 JSON / 可选", {
      exact: true,
    });
    await lastField.fill("{");
    const footer = dialog.locator(".dialog-actions");
    const save = dialog.getByRole("button", {
      name: "开始自主策划",
      exact: true,
    });
    await expect(footer).toHaveCSS("bottom", "0px");
    await dialog.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await insideVisibleDialog(lastField, dialog, { aboveFooter: footer });
    await insideVisibleDialog(save, dialog);
    await save.click();
    const error = dialog.getByRole("alert");
    await expect(error).toBeVisible();
    const details = error.getByRole("button", {
      name: "查看详情",
      exact: true,
    });
    await insideVisibleDialog(error, dialog, { aboveFooter: footer });
    await insideVisibleDialog(details, dialog, { aboveFooter: footer });
    await details.click();
    await expect(
      page.getByRole("dialog", { name: "错误详情", exact: true }),
    ).toBeVisible();
    await expect(page.locator("html")).toHaveCSS("overflow", "hidden");
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("dialog", { name: "错误详情", exact: true }),
    ).toHaveCount(0);
    await expect(dialog).toBeVisible();
    await expect(page.locator("html")).toHaveCSS("overflow", "hidden");
    await expect(details).toBeFocused();
    await expect(lastField).toHaveValue("{");
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    expect(await lockStyles(page)).toEqual(styles);
    await stableGeometry(page, anchors);
    const calls = await (await request.get("/__test/calls")).json();
    expect(
      calls.filter(
        (call) => call.method === "POST" && call.path.startsWith("/api/"),
      ),
    ).toEqual([]);
  });

  test(`service drawer paints to the viewport edge and keeps a fixed header at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await groupedModels(page, 30);
    await page.goto("/#view=models");
    await expect(serviceCard(page)).toBeVisible();
    const styles = await lockStyles(page);
    const anchors = await pageGeometry(page);
    const dialog = await openServiceDetails(page);
    const rect = await dialog.boundingBox();
    expect(Math.abs(rect.x + rect.width - width)).toBeLessThanOrEqual(1);
    expect(Math.abs(rect.width - Math.min(480, width))).toBeLessThanOrEqual(1);
    expect(Math.abs(rect.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(rect.height - 844)).toBeLessThanOrEqual(1);
    await stableGeometry(page, anchors);
    const header = dialog.locator(".dialog-head");
    const headerBox = await header.boundingBox();
    const close = header.locator('[data-action="close-dialog"]');
    await close.focus();
    const closeBox = await close.boundingBox();
    const outline = await close.evaluate((element) => {
      const css = getComputedStyle(element);
      return Math.max(
        0,
        parseFloat(css.outlineWidth) + parseFloat(css.outlineOffset),
      );
    });
    expect(
      closeBox.x + closeBox.width + outline,
      "right-side keyboard focus ring fits the physical viewport",
    ).toBeLessThanOrEqual(width);
    const body = dialog.locator(":scope > .dialog-body");
    expect(
      await body.evaluate(
        (element) => element.scrollHeight > element.clientHeight,
      ),
    ).toBe(true);
    await body.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    expect(await header.boundingBox()).toEqual(headerBox);
    await insideVisibleDialog(dialog.locator("[data-profile]").last(), dialog);
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(
      serviceCard(page).locator('[data-model-action="details"]'),
    ).toBeFocused();
    expect(await lockStyles(page)).toEqual(styles);
    await stableGeometry(page, anchors);
  });

  test(`service save failure reveals its error without losing focus or retrying at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await groupedModels(page, 12);
    await page.goto("/#view=models");
    await manageService(page);
    const dialog = page.locator("#service-dialog");
    const url = dialog.getByLabel("服务 URL", { exact: true });
    const key = dialog.getByLabel("API Key", { exact: true });
    await url.fill("http://127.0.0.1:1/fixture-draft/v1");
    await key.fill("fixture-draft-secret");
    await dialog.evaluate((element) => {
      element.scrollTop = 0;
    });
    const slot = dialog.locator(".form-feedback-slot");
    expect((await slot.boundingBox()).y).toBeGreaterThan(844);
    const footer = dialog.locator(".service-dialog-actions");
    const save = footer.locator('button[type="submit"]');
    await expect(save).toHaveAccessibleName("保存连接");
    let posts = 0,
      release,
      started;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const requested = new Promise((resolve) => {
      started = resolve;
    });
    const message = "隔离模拟保存失败，URL、Key 和模型草稿应保留。".repeat(30);
    await page.route("**/api/factory/models", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      posts++;
      started();
      await gate;
      await route.fulfill({ status: 503, json: { error: message } });
    });
    await save.click();
    await requested;
    // Disabling the submitting button can itself blur it. Capture only after
    // that native transition, before releasing the failed HTTP response.
    await expect(save).toBeDisabled();
    const focus = await page.evaluateHandle(() => document.activeElement);
    release();
    const error = dialog.getByRole("alert");
    await expect(error).toContainText("隔离模拟保存失败");
    await expect(save).toBeEnabled();
    await expect
      .poll(() => dialog.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
    await insideVisibleDialog(error, dialog, { aboveFooter: footer });
    const details = error.getByRole("button", {
      name: "查看详情",
      exact: true,
    });
    await insideVisibleDialog(details, dialog, { aboveFooter: footer });
    expect(
      await page.evaluate(
        (previous) => document.activeElement === previous,
        focus,
      ),
      "revealing an error does not steal focus",
    ).toBe(true);
    await expect(url).toHaveValue("http://127.0.0.1:1/fixture-draft/v1");
    await expect(key).toHaveValue("fixture-draft-secret");
    await expect(dialog.locator(".model-options")).toHaveCount(12);
    await details.click();
    await expect(
      page.getByRole("dialog", { name: "错误详情", exact: true }),
    ).toContainText(message);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible();
    await expect(page.locator("html")).toHaveCSS("overflow", "hidden");
    await expect(url).toHaveValue("http://127.0.0.1:1/fixture-draft/v1");
    expect(posts).toBe(1);
  });

  test(`cost authorization has no top feedback gap and preserves a failed draft at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.route(`**/projects/${pid}/cloud-budget`, (route) =>
      route.fulfill({
        json: {
          project_id: pid,
          tasks: [],
          authorizations: [],
          totals: {},
          quote: {
            id: "fixture-batch",
            estimated_submissions: 2,
            estimated_total_micros: 4000000,
            pricing: {
              unit_cost_micros: 2000000,
              currency: "USD",
              source: "fixture pricing",
            },
            connection: { model: "fixture-video" },
            resolution: "352×640",
            duration: 6,
            shot_count: 1,
            lines: [],
          },
        },
      }),
    );
    let posts = 0;
    await page.route(`**/projects/${pid}/cloud-authorize`, (route) => {
      posts++;
      expect(route.request().postDataJSON()).toMatchObject({
        max_submissions: 3,
        max_estimated_cost_micros: 6500000,
      });
      return route.fulfill({
        status: 503,
        json: { error: "隔离模拟授权失败，保留费用输入。" },
      });
    });
    await page.goto(`/#project=${pid}&tab=costs`);
    const stack = page.locator(".project-pane > .stack");
    const panel = stack.locator(":scope > .panel").first();
    await expect(
      panel.getByRole("heading", { name: "云媒体费用与授权", exact: true }),
    ).toBeVisible();
    await expect(stack.locator(":scope > .form-feedback-slot")).toHaveCount(0);
    expect(
      Math.abs((await panel.boundingBox()).y - (await stack.boundingBox()).y),
    ).toBeLessThanOrEqual(1);
    await panel.locator('[name="max_submissions"]').fill("3");
    await panel.locator('[name="max_cost"]').fill("6.50");
    const before = await panel.evaluate(
      (element) => element.getBoundingClientRect().y + scrollY,
    );
    await panel
      .getByRole("button", { name: "明确授权本批次", exact: true })
      .click();
    const error = page.getByRole("alert");
    await expect(error).toContainText("隔离模拟授权失败");
    expect(
      await error.evaluate(
        (element) =>
          getComputedStyle(element.closest(".feedback-region")).position,
      ),
    ).toBe("fixed");
    await expect(panel.locator('[name="max_submissions"]')).toHaveValue("3");
    await expect(panel.locator('[name="max_cost"]')).toHaveValue("6.50");
    expect(
      Math.abs(
        (await panel.evaluate(
          (element) => element.getBoundingClientRect().y + scrollY,
        )) - before,
      ),
    ).toBeLessThanOrEqual(1);
    expect(posts).toBe(1);
  });
}
