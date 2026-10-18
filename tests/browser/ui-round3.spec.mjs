import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { openWorkflow } from "./navigation.mjs";
import { serviceCard, openServiceDetails } from "./model-flow.mjs";
import {
  exposePageControl,
  closePageTools,
  refreshPage,
} from "./header-flow.mjs";
import { setTheme, exposeTheme } from "./theme.mjs";
import { paletteViolations } from "./palette.mjs";

const projectId = "012345abcdef";
test.beforeAll(async () => {
  await mkdir("runtime/ui-round3/after", { recursive: true });
});
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

for (const width of [390, 1440])
  test(`model cards keep endpoints independent and test only by explicit action at ${width}px`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.route("**/api/factory/models", async (route) => {
      const response = await route.fetch(),
        body = await response.json();
      const original = body.editable_profiles.writer;
      body.editable_profiles.writer_b = {
        ...original,
        model: "fixture-secondary-text",
        base_url: "http://127.0.0.1:8091/v1",
      };
      body.editable_profiles.writer_c = {
        ...original,
        model: "fixture-same-endpoint-vision",
        vision: true,
      };
      body.profiles.push(
        { id: "writer_b", ...body.editable_profiles.writer_b },
        { id: "writer_c", ...body.editable_profiles.writer_c },
      );
      await route.fulfill({ response, json: body });
    });
    await page.goto("/#view=models");
    const original = serviceCard(page),
      other = serviceCard(page, "llm:writer_b");
    await expect(original).toBeVisible();
    await expect(other).toBeVisible();
    await expect(original).toContainText(
      "mock-writer、fixture-same-endpoint-vision",
    );
    await expect(
      original.locator('[data-model-action="details"]'),
    ).toContainText("2 个模型");
    await expect(other).toContainText("fixture-secondary-text");
    await expect(other.locator(".service-card-address")).toContainText(
      "127.0.0.1:8091/v1",
    );
    for (const card of [original, other]) {
      await expect(card).toContainText("已配置");
      await expect(card).not.toContainText(
        /已连接|连接成功|连接正常|已验证|在线/,
      );
      await expect(card.locator('[data-model-action="manage"]')).toBeEnabled();
      for (const action of await card.locator("footer button").all()) {
        await expect(action).toHaveCSS("font-size", "14px");
        await expect(action).toHaveCSS("line-height", "22px");
      }
    }
    if (width === 1440) {
      // Resize the same container at a fixed viewport to distinguish container
      // breakpoints from viewport media queries, including each exact boundary.
      const settings = page.locator(".model-settings");
      const originalStyle = await settings.getAttribute("style");
      for (const [containerWidth, columns] of [
        [639, 1],
        [640, 2],
        [979, 2],
        [980, 3],
      ]) {
        await settings.evaluate((element, value) => {
          element.style.width = `${value}px`;
        }, containerWidth);
        await expect
          .poll(() =>
            page
              .locator(".service-card-grid")
              .evaluate(
                (element) =>
                  getComputedStyle(element).gridTemplateColumns.split(/\s+/)
                    .length,
              ),
          )
          .toBe(columns);
      }
      await settings.evaluate((element, value) => {
        if (value === null) element.removeAttribute("style");
        else element.setAttribute("style", value);
      }, originalStyle);
    }
    await expect(page.locator('[data-model-action="add"]')).toHaveCount(1);
    await expect(page.locator(".service-sidebar, .service-detail")).toHaveCount(
      0,
    );
    const posts = async () =>
      (await (await request.get("/__test/calls")).json()).filter(
        (call) => call.method === "POST" && call.path.startsWith("/api/"),
      );
    expect(await posts()).toEqual([]);
    await page.screenshot({
      path: `runtime/ui-round3/after/models-endpoints-${width}.png`,
      fullPage: true,
    });
    const dialog = await openServiceDetails(page, "llm:writer_b");
    await expect(dialog).toHaveAccessibleName("服务详情");
    await expect(dialog.locator(".model-name")).toHaveText(
      "fixture-secondary-text",
    );
    expect(await posts()).toEqual([]);
    await dialog
      .locator('[data-model-action="test"][data-profile="writer_b"]')
      .click();
    await expect(dialog).toContainText("模拟预检，不调用模型");
    const actions = await posts();
    expect(actions).toHaveLength(1);
    expect(actions[0]).toMatchObject({
      path: "/api/factory/models/test",
      body: { profile_id: "writer_b" },
    });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(other.locator('[data-model-action="details"]')).toBeFocused();
  });

test("storage unavailable theme survives tools, route changes and responsive remounts", async ({
  page,
}) => {
  await page.addInitScript(() => {
    for (const name of ["getItem", "setItem", "removeItem"])
      Object.defineProperty(Storage.prototype, name, {
        configurable: true,
        value() {
          throw new DOMException("Storage blocked", "SecurityError");
        },
      });
  });
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.goto("/#view=models");
  await setTheme(page, "dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.locator('[data-action="toggle-navigation"]').click();
  await page.locator('.main-nav [data-view="workflow"]').click();
  await expect(page.locator(".shared-page-header h1")).toHaveText(
    "制作流程与团队",
  );
  await expect(page.locator("html")).toHaveAttribute(
    "data-theme-preference",
    "dark",
  );
  const button = await exposeTheme(page);
  await expect(button).toHaveAccessibleName("外观主题：深色；切换为跟随系统");
  await closePageTools(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(await exposeTheme(page)).toHaveAccessibleName(
    "外观主题：深色；切换为跟随系统",
  );
  await page.locator('.main-nav [data-view="dashboard"]').click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.setViewportSize({ width: 320, height: 1000 });
  await expect(await exposeTheme(page)).toHaveAccessibleName(
    "外观主题：深色；切换为跟随系统",
  );
  await closePageTools(page);
  await setTheme(page, "system");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

for (const width of [320, 1440])
  for (const state of ["loading", "error"])
    test(`shared header remains operable while models are ${state} at ${width}px`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 1000 });
      let release,
        requests = 0;
      const held = new Promise((resolve) => {
        release = resolve;
      });
      await page.route("**/api/factory/models", async (route) => {
        requests++;
        if (state === "loading") {
          await held;
          return route.continue();
        }
        return route.fulfill({
          status: 503,
          json: { error: "模型配置暂时不可读取" },
        });
      });
      try {
        await page.goto("/#view=models", { waitUntil: "domcontentloaded" });
        await expect(page.locator("h1")).toHaveCount(1);
        await expect(page.locator(".shared-page-header h1")).toHaveText(
          "模型与连接",
        );
        await expect(page.locator(".topbar, .breadcrumb")).toHaveCount(0);
        await expect(await exposeTheme(page)).toBeEnabled();
        await closePageTools(page);
        const refresh = page.locator(
          '.shared-page-header [data-action="refresh"]',
        );
        await expect(await exposePageControl(page, refresh)).toBeEnabled();
        await closePageTools(page);
        if (width === 320)
          await expect(
            page.locator('[data-action="toggle-navigation"]'),
          ).toBeEnabled();
        const before = requests;
        await refreshPage(page);
        if (state === "error") {
          await expect.poll(() => requests).toBeGreaterThan(before);
          await expect(page.getByRole("alert")).toHaveCount(1);
        } else {
          release();
          await expect(page.locator(".service-card-grid")).toBeVisible();
        }
        await expect(page.locator(".shared-page-header h1")).toHaveText(
          "模型与连接",
        );
      } finally {
        release();
      }
    });

for (const theme of ["light", "dark"])
  test(`neutral surfaces, blue actions and semantic status colors stay coherent in ${theme}`, async ({
    page,
    request,
  }) => {
    await request.post("/__test/scenario", { data: { kind: "repairing" } });
    await page.goto(`/#project=${projectId}&tab=overview`);
    await setTheme(page, theme);
    await openWorkflow(page);
    await expect(page.locator('[data-flow-state="running"]')).not.toHaveCount(
      0,
    );
    const dots = page.locator("[data-status-indicator]:visible");
    expect(await dots.count()).toBeGreaterThan(0);
    for (const dot of await dots.all()) {
      await expect(dot).toHaveAttribute("aria-hidden", "true");
      await expect(dot).toHaveText("");
      await expect(dot).toHaveCSS("width", "6px");
      await expect(dot).toHaveCSS("height", "6px");
      const circular = await dot.evaluate(
        (element) => parseFloat(getComputedStyle(element).borderRadius) >= 3,
      );
      expect(circular).toBe(true);
    }
    expect(await paletteViolations(page)).toEqual([]);
    const surfaces = await page
      .locator("body, .panel, .metric-card, .drama-card")
      .evaluateAll((elements) =>
        elements
          .filter((element) => element.getClientRects().length)
          .map((element) => ({
            name: element.className || element.tagName,
            background: getComputedStyle(element)
              .backgroundColor.match(/[\d.]+/g)
              .slice(0, 3)
              .map(Number),
          })),
      );
    for (const surface of surfaces)
      expect(
        Math.max(...surface.background) - Math.min(...surface.background),
        surface.name,
      ).toBeLessThanOrEqual(14);
    const colors = await page.evaluate(() => {
      const rgb = (value) =>
        value
          .match(/[\d.]+/g)
          .slice(0, 3)
          .map(Number);
      const luminance = (value) =>
        rgb(value)
          .map((channel) => {
            const v = channel / 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          })
          .reduce(
            (sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i],
            0,
          );
      const probe = document.createElement("div");
      probe.innerHTML =
        `<button class="button primary">开始制作</button>` +
        ["review", "failed", "success"]
          .map(
            (status) =>
              `<span class="badge"><i data-status-indicator="${status}"></i>状态</span>`,
          )
          .join("");
      document.body.append(probe);
      const result = [...probe.children].map((element) => {
        const style = getComputedStyle(element);
        const fg = luminance(style.color),
          bg = luminance(style.backgroundColor);
        return {
          state:
            element.querySelector("i")?.dataset.statusIndicator || "primary",
          color: rgb(style.color),
          background: rgb(style.backgroundColor),
          ratio: (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05),
        };
      });
      probe.remove();
      return result;
    });
    for (const color of colors)
      expect(color.ratio, color.state).toBeGreaterThanOrEqual(4.5);
    const [primary, review, failed, success] = colors;
    expect(primary.background[2]).toBeGreaterThan(primary.background[0] + 40);
    expect(review.color[0]).toBeGreaterThan(review.color[2] + 40);
    expect(review.color[1]).toBeGreaterThan(review.color[2] + 30);
    expect(failed.color[0]).toBeGreaterThan(failed.color[1] + 40);
    expect(success.color[1]).toBeGreaterThan(success.color[0] + 30);
    await page.screenshot({
      path: `runtime/ui-round3/after/workflow-${theme}-1440.png`,
      fullPage: true,
    });
  });
