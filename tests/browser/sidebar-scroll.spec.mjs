import { test, expect } from "@playwright/test";

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
    test(`sidebar background stays joined to the frame while navigation sticks at ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await page.setViewportSize({ width, height: 700 });
      await page.emulateMedia({ colorScheme: theme });
      await longNavigation(page, request);
      await page.goto("/#view=settings&tab=models");
      await expect(page.locator(".model-service-card").first()).toBeVisible();
      const sidebar = page.locator("#studio-navigation");
      const initial = await sidebar.boundingBox();
      const rail = page.locator(".sidebar-rail");
      const initialRail = await rail.boundingBox();
      expect(Math.abs(initialRail.y - initial.y)).toBeLessThanOrEqual(1);

      await page.mouse.move(width - 80, 400);
      await page.mouse.wheel(0, 250);
      await expect
        .poll(() => page.evaluate(() => scrollY))
        .toBeGreaterThan(150);
      const sticky = await sidebar.boundingBox();
      expect(Math.abs(sticky.y - initial.y)).toBeLessThanOrEqual(1);
      // Both gaps around the sticky navigation must belong to the same opaque
      // sidebar surface. A background on the sticky box leaves these gaps exposed.
      const continuity = await rail.evaluate((element) => {
        const r = element.getBoundingClientRect();
        const sidebar = element.querySelector("aside");
        const s = sidebar.getBoundingClientRect();
        return {
          extendsAbove: r.top < 0,
          extendsBelow: r.bottom > innerHeight,
          covered: [4, innerHeight - 4].map(
            (y) => document.elementFromPoint(r.left + 8, y) === element,
          ),
          background: getComputedStyle(element).backgroundColor,
          bottomGap: innerHeight - s.bottom,
        };
      });
      expect(continuity.extendsAbove).toBe(true);
      expect(continuity.extendsBelow).toBe(true);
      expect(continuity.covered).toEqual([true, true]);
      expect(continuity.background).not.toBe("rgba(0, 0, 0, 0)");
      expect(continuity.bottomGap).toBeGreaterThan(0);

      const list = await sidebar.locator(".project-list").boundingBox();
      await page.mouse.move(list.x + list.width / 2, list.y + list.height / 2);
      await page.mouse.wheel(0, 900);
      await expect(
        sidebar.getByRole("button", { name: /最近制作 10/ }),
      ).toBeInViewport();
      const documentScroll = await page.evaluate(() => scrollY);
      expect(documentScroll).toBeGreaterThan(150);
      await page.mouse.move(width - 80, 400);
      await page.mouse.wheel(0, 5000);
      await expect
        .poll(() =>
          page.evaluate(() =>
            Math.abs(
              document.documentElement.scrollHeight - innerHeight - scrollY,
            ),
          ),
        )
        .toBeLessThanOrEqual(1);
      const bottomRail = await rail.boundingBox();
      const bottomSidebar = await sidebar.boundingBox();
      expect(
        Math.abs(
          bottomRail.y +
            bottomRail.height -
            bottomSidebar.y -
            bottomSidebar.height,
        ),
      ).toBeLessThanOrEqual(1);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `runtime/browser-report/sidebar-bottom-${width}-${theme}.png`,
      });

      // Return the independently scrolling navigation to its first controls.
      await sidebar.evaluate((element) => {
        element.scrollTop = 0;
      });
      await sidebar.getByRole("button", { name: "总览", exact: true }).click();
      await expect(page.locator(".dashboard-view")).toBeVisible();
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
