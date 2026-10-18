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
        await expect(page.locator("main h1").first()).toBeVisible();
        await expect(page.locator("main .loading")).toHaveCount(0);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await expect(page.getByRole("alert")).toHaveCount(0);
        if (route.tab) {
          await expect(
            page.locator(`.project-navigation [data-tab="${route.tab}"]`),
          ).toHaveAttribute("aria-current", "page");
          if (width < 1280) {
            const trigger = page.getByRole("button", { name: /^项目页面：/ });
            await expect(trigger).toBeVisible();
            await trigger.click();
            await expect(page.locator("[data-project-page]")).toHaveCount(10);
            expect(
              await page.locator("[data-project-page]").evaluateAll((items) =>
                items.every((item) => {
                  const box = item.getBoundingClientRect();
                  return (
                    box.left >= 0 && box.right <= innerWidth && box.height >= 44
                  );
                }),
              ),
            ).toBe(true);
            await page.keyboard.press("Escape");
            await expect(trigger).toBeFocused();
          }
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

test("mobile project navigation reaches all ten pages and supports keyboard dismissal", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto("/#project=012345abcdef&tab=overview");
  for (const tab of tabs) {
    await openProjectPage(page, tab);
    await expect(page).toHaveURL(new RegExp("tab=" + tab));
    await expect(page.locator(".project-navigation-trigger")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await expect(page.locator("main .loading")).toHaveCount(0);
  }
});

test("model and shot drafts survive navigating away while keeping their original revisions", async ({
  page,
}) => {
  await page.goto("/#view=models");
  await page.getByRole("tab", { name: "制作分工" }).click();
  await page.getByText("按角色单独配置 · 总制片代理", { exact: true }).click();
  await page.getByLabel("总制片代理", { exact: true }).selectOption("writer");
  await page.locator('.main-nav [data-view="dashboard"]').click();
  await page.locator('.main-nav [data-view="models"]').click();
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
      page.getByRole("heading", { name: "模型与连接" }),
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
        ".service-list-item small",
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
