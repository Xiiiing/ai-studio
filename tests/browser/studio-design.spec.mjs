import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { openSystemSettings } from "./header-flow.mjs";
import { paletteViolations, colorHierarchy, gradientSurfaces } from "./palette.mjs";

// The production bundle is served with test-only API responses. These are
// interface checks, not model performance or production-resource evidence.
const id = "012345abcdef";
const title = "长夜之后仍然有人守护这座城市：一部关于重逢与选择的原创剧集";
const primary = ["dashboard", "projects", "library", "activity", "models", "settings"];
const palettes = [["slate", "深灰蓝"], ["graphite", "雾银"], ["teal", "薄荷青"], ["jade", "青柠绿"], ["iris", "雾紫"], ["bronze", "杏桃"]];

async function fixture(page, request, { empty = false, attention = true, poster } = {}) {
  const base = await (await request.get("/api/factory/console")).json();
  const selected = {
    ...base.projects[0], title, poster,
    current_activity: "等待成片验收",
    attention: attention ? { ...base.projects[0].attention, blocking: true, title: "终片等待验收", summary: "成片已经完成，请查看结果后决定。", target_tab: "decisions" } : undefined,
  };
  const snapshot = {
    ...base,
    // The recent project is intentionally different from the blocking project:
    // visual prominence must follow the work that needs the user's attention.
    projects: empty ? [] : [{ ...selected, id: "eeeeeeeeeeee", title: "最近创建的其他剧集", attention: undefined }, selected],
    attention_projects: empty || !attention ? [] : [selected],
    running_tasks: [],
    metrics: { ...base.metrics, running: 7, attention: empty ? 0 : 2, queued: 3, waiting_remote: 5, delivered: 11 },
  };
  await page.route("**/api/factory/console**", (route) => route.fulfill({ json: snapshot }));
  return { snapshot, selected };
}

async function fit(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
}

async function checkOverview(page, expected = ["7", "2", "8", "11"]) {
  const focus = page.locator(".dashboard-focus");
  await expect(page.locator(".dashboard-view")).toBeVisible();
  await expect(focus.locator(".dashboard-focus-copy")).toContainText(title);
  await expect(focus).toContainText("等待成片验收");
  expect(await page.locator("[data-metric]").allTextContents()).toEqual(expected);
  for (const heading of ["制作中的任务", "待办", "最近动态", "制作资源"])
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  const feature = focus.locator(".dashboard-focus-feature");
  await expect(feature).toBeVisible();
  await expect(feature).not.toHaveAttribute("aria-hidden", "true");
  await expect(feature).toContainText("等待成片验收");
  await expect(feature).toContainText("95%");
  const progress = focus.getByRole("progressbar", { name: "当前制作进度", exact: true });
  await expect(progress).toHaveAttribute("aria-valuenow", "95");
  await expect(progress).toHaveAttribute("aria-valuemax", "100");
  await expect(focus.locator(".dashboard-focus-next").getByRole("button", { name: "打开当前项目", exact: true })).toBeVisible();
  await expect(focus.locator(".dashboard-focus-copy")).not.toContainText(/等待成片验收|95%/);
  await expect(feature.locator("button,a,input,[tabindex]")).toHaveCount(0);
  await expect(focus.locator(".focus-film-frame")).toHaveCount(0);
  await expect(focus).not.toContainText(/节省|52\.6|节能/);
  await focus.getByRole("button", { name: "打开当前项目", exact: true }).click({ trial: true });
  const copy = await focus.locator(".dashboard-focus-copy").evaluate((element) => {
    const style = getComputedStyle(element);
    return { overflow: style.overflow, height: element.clientHeight, contentHeight: element.scrollHeight };
  });
  expect(copy.overflow).not.toBe("hidden");
  expect(copy.contentHeight).toBeLessThanOrEqual(copy.height + 1);
  const projectTitle = await focus.locator(".dashboard-focus-copy").getByText(title, { exact: true }).evaluate((element) => {
    const style = getComputedStyle(element);
    return { width: element.clientWidth, contentWidth: element.scrollWidth, clamp: style.webkitLineClamp, ellipsis: style.textOverflow };
  });
  expect(projectTitle.contentWidth).toBeLessThanOrEqual(projectTitle.width + 1);
  expect(["none", "", "0"]).toContain(projectTitle.clamp);
  expect(projectTitle.ellipsis).not.toBe("ellipsis");
  await page.mouse.move(0, 0);
  const hierarchy = await colorHierarchy(page);
  expect(hierarchy.focus.rawBackground).toBe("rgba(0, 0, 0, 0)");
  expect(hierarchy.feature.mask).not.toBe("none");
  await expect(focus.locator(".dashboard-focus-next .primary")).toBeVisible();
  await expect(feature).toHaveCSS("background-image", "none");
  expect(hierarchy.focus.image).toBe("none");
  expect(hierarchy.primary.background).toEqual(hierarchy.tokens.primary);
  expect(Math.max(...hierarchy.primary.background) - Math.min(...hierarchy.primary.background)).toBeLessThanOrEqual(8);
  expect(hierarchy.primary.contrast).toBeGreaterThanOrEqual(4.5);
  expect(hierarchy.feature.background).toEqual(hierarchy.tokens["feature-color"]);
  expect(hierarchy.feature.image).toMatch(/^linear-gradient\(145deg,/);
  expect(hierarchy.feature.color).toEqual(hierarchy.tokens["feature-ink"]);
  expect(hierarchy.feature.contrast).toBeGreaterThanOrEqual(4.5);
  for (const text of hierarchy.featureText) {
    expect(text.color, text.text).toEqual(hierarchy.tokens["feature-ink"]);
    expect(text.contrast, text.text).toBeGreaterThanOrEqual(4.5);
  }
  for (const metric of hierarchy.metrics) {
    expect(metric.background).toEqual(hierarchy.tokens.panel);
    expect(metric.image).toBe("none");
  }
  const gradients = await gradientSurfaces(page);
  expect(gradients.unexpected).toEqual([]);
  expect(gradients.surfaces.some(s => !s.swatch)).toBe(true);
  for (const surface of gradients.surfaces) {
    expect(surface.image, surface.element).toMatch(/^linear-gradient\(145deg,/);
    expect(surface.image, surface.element).toMatch(/0%,.*48%,.*100%/);
    expect(surface.stops, surface.element).toHaveLength(3);
    expect(surface.distinctStops, surface.element).toBe(3);
    if (surface.expectedStops) expect(surface.stops, surface.element).toEqual(surface.expectedStops);
    if (!surface.swatch) {
      expect(surface.surfaceContrast, surface.element).toBeLessThanOrEqual(3);
      expect(surface.contrast, surface.element).toBeGreaterThanOrEqual(4.5);
      for (const text of surface.text) expect(text.contrast, text.label).toBeGreaterThanOrEqual(4.5);
    }
  }
  if (page.viewportSize().width <= 700) {
    const bounds = await feature.boundingBox();
    const copyBounds = await focus.locator(".dashboard-focus-copy").boundingBox();
    expect(bounds.y).toBeGreaterThanOrEqual(copyBounds.y + copyBounds.height - 1);
    expect(await feature.evaluate((element) => element.scrollHeight <= element.clientHeight + 1)).toBe(true);
  }
  expect(await paletteViolations(page)).toEqual([]);
  await fit(page);
}

test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
});

for (const width of [1920, 1440, 390, 320]) for (const theme of ["light", "dark"]) {
  test(`reference-inspired overview keeps real work readable and actionable at ${width} ${theme}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ colorScheme: theme });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await fixture(page, request);
    await page.goto("/");
    await checkOverview(page);
    if (width < 1024) {
      await page.getByRole("button", { name: "展开导航", exact: true }).click();
      await expect(page.locator("#studio-navigation").getByRole("button", { name: "关闭导航", exact: true })).toBeFocused();
    }
    const nav = page.getByRole("navigation", { name: "主导航", exact: true });
    expect(await nav.locator("[data-view]").evaluateAll((nodes) => nodes.map((node) => node.dataset.view))).toEqual(primary);
    await expect(page.getByRole("link", { name: "映序首页" }).locator("img.brand-symbol")).toHaveAttribute("src", /brand\.svg$/);
    if (width < 1024) {
      await page.keyboard.press("Escape");
      await expect(page.locator("#studio-navigation")).toHaveAttribute("inert", "");
    }
    if ([1440, 390].includes(width)) {
      await mkdir("docs/images/studio-design", { recursive: true });
      await page.screenshot({ path: `docs/images/studio-design/overview-${theme}-${width}.png`, fullPage: true });
    }
    await page.locator(".dashboard-focus-actions").getByRole("button", { name: "打开当前项目", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`project=${id}&tab=decisions$`));
    await expect(page.locator('form[data-form="final"]')).toBeVisible();
    await fit(page);
    expect(errors).toEqual([]);
    const writes = (await (await request.get("/__test/calls")).json()).filter((call) => !["GET", "HEAD"].includes(call.method));
    expect(writes).toEqual([]);
  });
}

test("existing poster stays inside the current-work thumbnail and a failed poster keeps the usable fallback", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const poster = `/media/production/factory-${id}/frames/frame.png`;
  const response = await request.get(poster);
  expect(response.ok()).toBe(true);
  const png = await response.body();
  await fixture(page, request, { poster });
  let broken = false;
  await page.route(`**${poster}`, (route) => broken
    ? route.fulfill({ status: 404, body: "missing poster" })
    : route.fulfill({ status: 200, contentType: "image/png", body: png }));
  await page.goto("/");
  const focus = page.locator(".dashboard-focus");
  const frame = focus.locator(".dashboard-focus-feature .focus-poster");
  await expect(frame).toHaveAttribute("aria-hidden", "true");
  const image = frame.locator("img");
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((element) => element.naturalWidth)).toBeGreaterThan(0);
  const bounds = await frame.evaluate((element) => {
    const outer = element.getBoundingClientRect();
    const image = element.querySelector("img").getBoundingClientRect();
    return { contained: image.left >= outer.left && image.right <= outer.right && image.top >= outer.top && image.bottom <= outer.bottom };
  });
  expect(bounds.contained).toBe(true);
  const thumbnail = await frame.boundingBox();
  expect(thumbnail.width).toBeLessThanOrEqual(72);
  expect(thumbnail.height).toBeLessThanOrEqual(72);
  await focus.getByRole("button", { name: "打开当前项目", exact: true }).click({ trial: true });
  broken = true;
  await page.reload();
  await expect(image).toHaveAttribute("hidden", "");
  await expect(frame.locator("svg")).toBeVisible();
  await focus.getByRole("button", { name: "打开当前项目", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`project=${id}&tab=decisions$`));
  const writes = (await (await request.get("/__test/calls")).json()).filter((call) => !["GET", "HEAD"].includes(call.method));
  expect(writes).toEqual([]);
});

for (const width of [1440, 390]) for (const theme of ["light", "dark"]) {
  test(`all six accent choices preserve black-white hierarchy and mode-aware layered theme surfaces at ${width} ${theme}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ colorScheme: theme });
    await fixture(page, request);
    await page.goto("/#view=settings");
    const neutral = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return ["canvas", "panel", "surface", "ink", "primary", "on-primary", "success", "warning", "danger"].map((token) => style.getPropertyValue(`--${token}`).trim());
    });
    const accents = new Set();
    for (const [accent, label] of palettes) {
      await page.getByRole("group", { name: "主题色", exact: true }).getByText(label, { exact: true }).click();
      await page.goto("/");
      await expect(page.locator("html")).toHaveAttribute("data-accent", accent);
      const current = await page.evaluate(() => {
        const style = getComputedStyle(document.documentElement);
        return { accent: style.getPropertyValue("--accent-feature").trim(), neutral: ["canvas", "panel", "surface", "ink", "primary", "on-primary", "success", "warning", "danger"].map((token) => style.getPropertyValue(`--${token}`).trim()) };
      });
      expect(current.neutral).toEqual(neutral);
      accents.add(current.accent);
      await checkOverview(page);
      if (accent === "jade") {
        await mkdir("docs/images/studio-design", { recursive: true });
        await page.screenshot({ path: `docs/images/studio-design/overview-jade-${theme}-${width}.png`, fullPage: true });
        if (width === 1440 && theme === "light")
          await page.locator(".dashboard-focus").screenshot({ path: "docs/images/studio-design/focus-jade-light-1440.png" });
      }
      await openSystemSettings(page);
    }
    expect(accents.size).toBe(6);
  });
}

for (const width of [1440, 390]) {
  test(`empty overview opens the existing drama creation dialog at ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    await fixture(page, request, { empty: true });
    await page.goto("/");
    const focus = page.locator(".dashboard-focus");
    await expect(focus).toContainText("开始第一部作品");
    await focus.getByRole("button", { name: "新建一部剧", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "新建一部剧", exact: true })).toBeVisible();
    await fit(page);
    const writes = (await (await request.get("/__test/calls")).json()).filter((call) => !["GET", "HEAD"].includes(call.method));
    expect(writes).toEqual([]);
  });
}
