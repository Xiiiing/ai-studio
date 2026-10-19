import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { startRealServer } from "./real-server.mjs";
import { openProjectPage } from "./navigation.mjs";

const views = [
  "dashboard",
  "projects",
  "library",
  "inbox",
  "workflow",
  "models",
  "activity",
];
const tabs = [
  "overview",
  "decisions",
  "team",
  "prompts",
  "generation",
  "shots",
  "script",
  "quality",
  "delivery",
  "series",
  "materials",
  "costs",
];
let server;
test.beforeAll(async () => {
  server = await startRealServer();
  await mkdir("runtime/browser-report/redesign", { recursive: true });
});
test.afterAll(async () => {
  await server?.stop();
});
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

for (const width of [320, 390, 768, 1024, 1440, 1920]) {
  for (const theme of ["light", "dark"]) {
    test(`complete studio layout at ${width}px in ${theme}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      for (const route of [
        ...views.map((view) => ({ name: view, hash: "view=" + view })),
        ...tabs.map((tab) => ({
          name: "project-" + tab,
          tab,
          hash: `project=${server.projectId}&tab=${tab}`,
        })),
      ]) {
        await page.goto(server.endpoint + "/#" + route.hash);
        await expect(page.locator("main h1")).toHaveCount(1);
        await expect(page.locator("main h1")).toBeVisible();
        await expect(page.locator(".shared-page-header")).toHaveCount(1);
        await expect(page.locator(".shared-page-header h1")).toHaveCount(1);
        await expect(page.locator(".topbar, .breadcrumb")).toHaveCount(0);
        await expect(page.locator("main .loading")).toHaveCount(0);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await expect(page.getByRole("alert")).toHaveCount(0);
        if (route.name === "models") {
          const grid = page.locator(".service-card-grid");
          await expect(grid).toBeVisible();
          const geometry = await grid.evaluate((element) => ({
            width: element.getBoundingClientRect().width,
            columns:
              getComputedStyle(element).gridTemplateColumns.split(/\s+/).length,
            firstY: element
              .querySelector(".model-service-card")
              .getBoundingClientRect().top,
          }));
          expect(geometry.columns).toBe(
            geometry.width >= 980 ? 3 : geometry.width >= 640 ? 2 : 1,
          );
          // Models now live below the settings categories and section heading.
          // The first usable card must still be reachable in the initial viewport.
          expect(geometry.firstY).toBeLessThanOrEqual(page.viewportSize().height - 44);
          await expect(page.locator('.settings-category[aria-current="page"]')).toHaveAccessibleName("模型与服务");
        }
        if (route.tab) {
          if (width >= 1280) {
            await expect(page.locator(".heading-actions")).toHaveCSS(
              "flex-direction",
              "row",
            );
            const result = await page
              .locator(".project-result-actions")
              .boundingBox();
            const operations = await page
              .locator(".project-actions-menu > summary")
              .boundingBox();
            expect(Math.abs(result.y - operations.y)).toBeLessThanOrEqual(1);
            if (route.tab === "overview") {
              const geometry = () =>
                page.evaluate(() =>
                  Object.fromEntries(
                    [".shared-page-header", ".project-navigation-bar"].map(
                      (selector) => [
                        selector,
                        document.querySelector(selector).getBoundingClientRect()
                          .top + scrollY,
                      ],
                    ),
                  ),
                );
              const baseline = await geometry();
              const menu = page.locator(".project-actions-menu");
              await menu.locator(":scope > summary").click();
              await expect(menu).toHaveAttribute("open", "");
              const expanded = await geometry();
              for (const selector of Object.keys(baseline))
                expect(
                  Math.abs(expanded[selector] - baseline[selector]),
                  selector + " after opening project operations",
                ).toBeLessThanOrEqual(1);
              await page.keyboard.press("Escape");
              await expect(menu).not.toHaveAttribute("open");
              const closed = await geometry();
              for (const selector of Object.keys(baseline))
                expect(
                  Math.abs(closed[selector] - baseline[selector]),
                  selector + " after closing project operations",
                ).toBeLessThanOrEqual(1);
            }
          }
          const group = ["materials", "series"].includes(route.tab)
            ? "materials"
            : ["decisions", "quality"].includes(route.tab)
              ? "decisions"
              : route.tab;
          const navigation = page.locator(".project-primary-navigation");
          await expect(navigation.locator("[data-tab]")).toHaveCount(6);
          for (const item of await navigation.locator("[data-tab]").all()) {
            await expect(item).toBeInViewport({ ratio: 1 });
            const box = await item.boundingBox();
            expect(box.height).toBeGreaterThanOrEqual(44);
          }
          if (!["team", "prompts", "generation", "costs"].includes(route.tab))
            await expect(
              navigation.locator(`[data-tab="${group}"]`),
            ).toHaveAttribute("aria-current", "page");
          if (
            ["materials", "series", "decisions", "quality"].includes(route.tab)
          )
            await expect(
              page.locator(
                `.project-subnavigation [data-project-page="${route.tab}"]`,
              ),
            ).toHaveAttribute("aria-current", "page");
          const more = page.locator(".project-more");
          const trigger = more.locator(":scope > summary");
          await trigger.click();
          await expect(more.locator("[data-project-page]")).toHaveCount(4);
          for (const item of await more.locator("[data-project-page]").all()) {
            await expect(item).toBeInViewport({ ratio: 1 });
            const box = await item.boundingBox();
            expect(box.height).toBeGreaterThanOrEqual(44);
          }
          await page.keyboard.press("Escape");
          await expect(more).not.toHaveAttribute("open");
          await expect(trigger).toBeFocused();
        }
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          route.name,
        ).toBe(true);
        await page.screenshot({
          path: `runtime/browser-report/redesign/${theme}-${width}-${route.name}.png`,
          fullPage: true,
        });
      }
      expect(errors).toEqual([]);
    });
  }
}

test("horizontal project navigation reaches all ten pages on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto("/#project=012345abcdef&tab=overview");
  for (const tab of tabs) {
    await openProjectPage(page, tab);
    await expect(page).toHaveURL(new RegExp("tab=" + tab));
    await expect(page.locator(".project-more")).not.toHaveAttribute("open");
    await expect(page.locator("main .loading")).toHaveCount(0);
  }
});

test("legacy project links retain child pages and browser history", async ({
  page,
  request,
}) => {
  await page.goto("/#project=012345abcdef&tab=quality");
  await expect(
    page.locator('.project-subnavigation [data-project-page="quality"]'),
  ).toHaveAttribute("aria-current", "page");
  await openProjectPage(page, "series");
  await expect(
    page.locator('.project-subnavigation [data-project-page="series"]'),
  ).toHaveAttribute("aria-current", "page");
  await openProjectPage(page, "team");
  await expect(page).toHaveURL(/tab=team/);
  await page.goBack();
  await expect(page).toHaveURL(/tab=series/);
  await expect(
    page.locator('.project-subnavigation [data-project-page="series"]'),
  ).toHaveAttribute("aria-current", "page");
  await page.goBack();
  await expect(page).toHaveURL(/tab=materials/);
  await page.goBack();
  await expect(page).toHaveURL(/tab=quality/);
  await page.goForward();
  await expect(page).toHaveURL(/tab=materials/);
  await page.goForward();
  await expect(page).toHaveURL(/tab=series/);
  await openProjectPage(page, "costs");
  await expect(page).toHaveURL(/tab=costs/);
  const calls = await (await request.get("/__test/calls")).json();
  expect(
    calls.filter(
      (call) => call.method === "POST" && call.path.startsWith("/api/"),
    ),
  ).toEqual([]);
});

test("model and shot drafts survive navigating away while keeping their original revisions", async ({
  page,
}) => {
  await page.goto("/#view=models");
  await page.getByRole("tab", { name: "制作分工" }).click();
  await page.getByText("按角色单独配置 · 总制片代理", { exact: true }).click();
  await page.getByLabel("总制片代理", { exact: true }).selectOption("writer");
  await page.locator('.main-nav [data-view="dashboard"]').click();
  await page.locator('.main-nav [data-view="settings"]').click();
  await page.getByRole("button", { name: "模型与服务", exact: true }).click();
  await page.getByRole("tab", { name: "制作分工" }).click();
  await page.getByText("按角色单独配置 · 总制片代理", { exact: true }).click();
  await expect(page.getByLabel("总制片代理", { exact: true })).toHaveValue(
    "writer",
  );
  await expect(
    page.getByRole("button", { name: "保存分工", exact: true }),
  ).toBeEnabled();
  await page.goto("/#project=012345abcdef&tab=shots");
  await page.getByText("单镜精修", { exact: true }).click();
  await page.getByLabel("首帧提示词").fill("A retained shot draft");
  await openProjectPage(page, "team");
  await openProjectPage(page, "shots");
  await expect(page.getByLabel("首帧提示词")).toHaveValue(
    "A retained shot draft",
  );
  await expect(page.locator('[data-shot-operation="edit"]')).toHaveAttribute(
    "data-revision",
    "7",
  );
});

test("search and filters request the complete project collection and reset pagination", async ({
  page,
}) => {
  const projects = Array.from({ length: 103 }, (_, i) => ({
    id: String(i).padStart(12, "0"),
    title: i === 102 ? "百条以外的目标项目" : "制作 " + i,
    brief: "原创",
    status: i === 102 ? "approved" : "producing",
    current_activity: "制作中",
  }));
  const requests = [];
  await page.route("**/api/factory/console?*", (route) => {
    const query = new URL(route.request().url()).searchParams;
    requests.push(Object.fromEntries(query));
    const q = query.get("q") || "",
      filter = query.get("filter") || "all",
      offset = Number(query.get("offset") || 0);
    const matches = projects.filter(
      (p) =>
        (!q || p.title.includes(q)) &&
        (filter !== "delivered" || p.status === "approved"),
    );
    return route.fulfill({
      json: {
        projects: matches.slice(offset, offset + 100),
        pagination: {
          total: matches.length,
          offset,
          limit: 100,
          has_more: offset + 100 < matches.length,
          next_offset: offset + 100 < matches.length ? offset + 100 : null,
        },
      },
    });
  });
  await page.goto("/#view=projects");
  await expect(page.locator(".console-project-table tbody tr")).toHaveCount(
    100,
  );
  await page.getByLabel("搜索项目").fill("无匹配内容");
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
  await expect(page.locator(".console-project-table tbody tr")).toHaveCount(0);
  await page.getByLabel("搜索项目").fill("");
  await expect(page.locator(".console-project-table tbody tr")).toHaveCount(
    100,
  );
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".console-project-table tbody tr")).toHaveCount(3);
  await page.getByLabel("搜索项目").fill("百条以外");
  await expect(page.getByRole("button", { name: "下一页" })).toBeDisabled();
  await expect(page.locator(".console-project-table tbody tr")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "百条以外的目标项目 制作中" }),
  ).toBeVisible();
  expect(requests.at(-1)).toMatchObject({ q: "百条以外", offset: "0" });
  await page.getByRole("button", { name: "已交付", exact: true }).click();
  await expect.poll(() => requests.at(-1)?.filter).toBe("delivered");
  expect(requests.at(-1).offset).toBe("0");
});

test("review shot references select the exact shot", async ({
  page,
  request,
}) => {
  await request.post("/__test/scenario", { data: { kind: "creative" } });
  await page.route(
    "**/api/factory/projects/012345abcdef/workspace",
    async (route) => {
      const response = await route.fetch(),
        snapshot = await response.json();
      snapshot.operator.attention.issues = [
        { severity: "major", description: "人物动作不连续", shot_ids: ["S02"] },
      ];
      await route.fulfill({ json: snapshot });
    },
  );
  await page.route(
    "**/api/factory/projects/012345abcdef/shots",
    async (route) => {
      const response = await route.fetch(),
        board = await response.json();
      board.shots.push({
        ...board.shots[0],
        id: "S02",
        dramatic_beat: "需要定位的镜头",
        candidates: [],
      });
      await route.fulfill({ json: board });
    },
  );
  await page.goto("/#project=012345abcdef&tab=decisions");
  await page
    .locator(".decision-issues")
    .getByRole("button", { name: "S02", exact: true })
    .click();
  await expect(page.locator(".shot-detail-heading h2")).toContainText(
    "S02 · 需要定位的镜头",
  );
});

for (const theme of ["light", "dark"])
  test(`key text and button colors meet contrast requirements in ${theme}`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: theme });
    await page.goto("/#view=models");
    await expect(
      page.getByRole("heading", { name: "系统设置" }),
    ).toBeVisible();
    const results = await page.evaluate(() => {
      const rgb = (value) =>
        (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
      const luminance = (color) =>
        rgb(color)
          .map((x) => {
            const v = x / 255;
            return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          })
          .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
      return [
        ".page-heading h1",
        ".page-heading p",
        ".button.primary:not(:disabled)",
        ".notice",
        ".service-detail-footnote",
        ".service-card-address",
        ".field > label",
        ".nav-item.active",
      ].flatMap((selector) => {
        const element = document.querySelector(selector);
        if (!element || element.matches(":disabled")) return [];
        let background = element;
        while (
          background.parentElement &&
          getComputedStyle(background).backgroundColor === "rgba(0, 0, 0, 0)"
        )
          background = background.parentElement;
        const fg = luminance(getComputedStyle(element).color),
          bg = luminance(getComputedStyle(background).backgroundColor);
        return [
          {
            selector,
            ratio: (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05),
          },
        ];
      });
    });
    for (const result of results)
      expect(result.ratio, result.selector).toBeGreaterThanOrEqual(4.5);
  });
