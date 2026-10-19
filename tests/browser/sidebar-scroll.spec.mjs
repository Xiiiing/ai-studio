import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

// Use the production bundle and isolated API fixtures; never contact model services.
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

async function longNavigation(page, request) {
  const overview = await (await request.get("/api/factory/console")).json();
  overview.projects = Array.from({ length: 10 }, (_, index) => ({
    ...overview.projects[0],
    id: `recent-${index}`,
    title: `最近制作 ${index + 1}`,
  }));
  overview.metrics = {
    ...overview.metrics,
    projects: overview.projects.length,
  };
  await page.route("**/api/factory/console**", (route) =>
    route.fulfill({ json: overview }),
  );
  await page.route("**/api/factory/models", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    for (let index = 0; index < 12; index++) {
      const id = `scroll-fixture-${index}`;
      const profile = {
        ...body.editable_profiles.writer,
        model: id,
        base_url: `http://127.0.0.1:${20000 + index}/v1`,
      };
      body.editable_profiles[id] = profile;
      body.profiles.push({ id, ...profile });
    }
    await route.fulfill({ response, json: body });
  });
}

for (const width of [1024, 1440, 1920])
  for (const theme of ["light", "dark"]) {
    test(`desktop frame and navigation keep both edges while workspace scrolls at ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await page.setViewportSize({ width, height: 700 });
      await page.emulateMedia({ colorScheme: theme });
      await longNavigation(page, request);
      await page.goto("/#view=settings&tab=models");
      await expect(page.locator(".model-service-card").first()).toBeVisible();
      const shell = page.locator(".app-shell"),
        sidebar = page.locator("#studio-navigation"),
        workspace = page.locator(".main-shell");
      const initialFrame = await shell.boundingBox(),
        initialNav = await sidebar.boundingBox();
      if (width === 1440) {
        await mkdir("docs/images/navigation-selection", { recursive: true });
        await page.screenshot({
          path: `docs/images/navigation-selection/frame-top-${theme}.png`,
        });
      }
      const pane = await workspace.boundingBox();
      const scrollX = pane.x + pane.width - 30;
      expect(initialFrame.y).toBeGreaterThan(0);
      expect(initialFrame.y + initialFrame.height).toBeLessThan(700);
      expect(
        Math.abs(initialFrame.y - (700 - initialFrame.y - initialFrame.height)),
      ).toBeLessThanOrEqual(1);
      const inViewport = async () => {
        expect(await shell.boundingBox()).toEqual(initialFrame);
        expect(await sidebar.boundingBox()).toEqual(initialNav);
        expect(await page.evaluate(() => scrollY)).toBe(0);
        expect(
          await page.evaluate(() => document.documentElement.scrollHeight),
        ).toBeLessThanOrEqual(700);
      };
      await page.mouse.move(scrollX, 400);
      await page.mouse.wheel(0, 250);
      await expect
        .poll(() => workspace.evaluate((el) => el.scrollTop))
        .toBeGreaterThan(150);
      await inViewport();
      const mainPosition = await workspace.evaluate((el) => el.scrollTop);
      const list = await sidebar.locator(".project-list").boundingBox();
      await page.mouse.move(list.x + list.width / 2, list.y + list.height / 2);
      await page.mouse.wheel(0, 900);
      await expect(
        sidebar.getByRole("button", { name: /最近制作 10/ }),
      ).toBeInViewport();
      expect(await workspace.evaluate((el) => el.scrollTop)).toBe(mainPosition);
      await page.mouse.move(scrollX, 400);
      await page.mouse.wheel(0, 10000);
      await expect
        .poll(() =>
          workspace.evaluate((el) =>
            Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop),
          ),
        )
        .toBeLessThanOrEqual(1);
      await inViewport();
      await expect(page.locator(".model-service-card").last()).toBeInViewport();
      const bottomSpacing = await page.locator("#workspace").evaluate((el) => ({
        bottom: el.getBoundingClientRect().bottom,
        padding: parseFloat(getComputedStyle(el).paddingBottom),
        contentBottom: el.lastElementChild.getBoundingClientRect().bottom,
      }));
      expect(
        Math.abs(bottomSpacing.bottom - (pane.y + pane.height)),
      ).toBeLessThanOrEqual(1);
      expect(
        bottomSpacing.bottom - bottomSpacing.contentBottom,
      ).toBeGreaterThanOrEqual(bottomSpacing.padding - 1);
      await page.screenshot({
        path: `runtime/browser-report/frame-bottom-${width}-${theme}.png`,
      });
      if (width === 1440)
        await page.screenshot({
          path: `docs/images/navigation-selection/frame-bottom-${theme}.png`,
        });
      // An overlay must lock the actual desktop scroller and preserve its position.
      const bottom = await workspace.evaluate((el) => el.scrollTop);
      await sidebar
        .getByRole("button", { name: "新建制作", exact: true })
        .click();
      const dialog = page.getByRole("dialog", {
        name: "新建一部剧",
        exact: true,
      });
      await expect(dialog).toBeVisible();
      await expect(workspace).toHaveCSS("overflow-y", "hidden");
      await page.mouse.move(width - 30, 500);
      await page.mouse.wheel(0, -500);
      expect(await workspace.evaluate((el) => el.scrollTop)).toBe(bottom);
      await dialog.getByRole("button", { name: "关闭", exact: true }).click();
      await expect(dialog).toHaveCount(0);
      await expect(workspace).toHaveCSS("overflow-y", "auto");
      expect(await workspace.evaluate((el) => el.scrollTop)).toBe(bottom);
      await sidebar.getByRole("button", { name: "总览", exact: true }).click();
      await expect(page.locator(".dashboard-view")).toBeVisible();
      expect(await workspace.evaluate((el) => el.scrollTop)).toBe(0);
      await inViewport();
      await page.setViewportSize({ width, height: 540 });
      const resized = await shell.boundingBox();
      expect(
        Math.abs(resized.y - (540 - resized.y - resized.height)),
      ).toBeLessThanOrEqual(1);
      expect(
        await page.evaluate(() => document.documentElement.scrollHeight),
      ).toBeLessThanOrEqual(540);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    });
  }

for (const width of [320, 390, 768, 1023])
  for (const theme of ["light", "dark"]) {
    test(`sidebar drawer stays opaque, scrollable and dismissible at ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await page.setViewportSize({ width, height: 700 });
      await page.emulateMedia({ colorScheme: theme });
      await longNavigation(page, request);
      await page.goto("/#view=settings&tab=models");
      await expect(
        page.getByRole("heading", { name: "系统设置", exact: true }),
      ).toBeVisible();
      const sidebar = page.locator("#studio-navigation");
      await expect(sidebar).toHaveAttribute("inert", "");
      await page.getByRole("button", { name: "展开导航", exact: true }).click();
      await expect(
        sidebar.getByRole("button", { name: "关闭导航", exact: true }),
      ).toBeFocused();
      const box = await sidebar.boundingBox();
      expect(box.y).toBe(0);
      expect(box.height).toBe(700);
      expect(
        await sidebar.evaluate(
          (element) => getComputedStyle(element).backgroundColor,
        ),
      ).not.toBe("rgba(0, 0, 0, 0)");
      const list = await sidebar.locator(".project-list").boundingBox();
      await page.mouse.move(list.x + list.width / 2, list.y + list.height / 2);
      await page.mouse.wheel(0, 1000);
      await expect(
        sidebar.getByRole("button", { name: /最近制作 10/ }),
      ).toBeInViewport();
      expect(await page.evaluate(() => scrollY)).toBe(0);
      await page.keyboard.press("Escape");
      await expect(sidebar).toHaveAttribute("inert", "");
      await expect(
        page.getByRole("button", { name: "展开导航", exact: true }),
      ).toBeFocused();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    });
  }
