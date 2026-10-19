import { manageService } from "./model-flow.mjs";
import { setTheme } from "./theme.mjs";
import { test, expect } from "@playwright/test";
import { openProjectPage } from "./navigation.mjs";

test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) =>
    ["localhost", "127.0.0.1"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
  await page.route(
    "**/api/factory/projects/012345abcdef/workspace",
    async (route) => {
      const response = await route.fetch(),
        body = await response.json();
      body.project.shots = Array.from({ length: 24 }, (_, i) => ({
        id: `S${String(i + 1).padStart(2, "0")}`,
        seconds: 6,
        status: i < 2 ? "failed" : "passed",
        qa: {
          technical: {
            status: "pass",
            metrics: {
              nested: {
                scope:
                  "Nested technical explanation with enough text to reproduce narrow recursive columns. ".repeat(
                    10,
                  ),
                width: 720,
                height: 1280,
              },
            },
          },
          semantic: {
            status: i < 2 ? "fail" : "pass",
            summary: "人物动作与镜头连续性已有独立审核结论。",
            issues: [{ detail: "审核提示可展开核对。" }],
          },
        },
        attempts: [{ attempt: 1, parameters: { width: 720, height: 1280 } }],
      }));
      body.project.events = Array.from({ length: 80 }, (_, i) => ({
        at: 1700000000 + i,
        kind: i % 3 ? "progress" : "error",
        message: "保留历史事件 " + i,
      }));
      body.project.script = {
        title: "合成剧本",
        synopsis: "成年角色通过行动解释误会。",
        cast: { char_lead: "Long visual reference. ".repeat(20) },
        character_names: { char_lead: "合成人物" },
        characters: [
          { description: "Long English reference specification. ".repeat(100) },
        ],
      };
      await route.fulfill({ json: body });
    },
  );
});

for (const width of [320, 390, 768, 1440])
  test(`dense project content remains readable and primary work reachable at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/#project=012345abcdef&tab=quality");
    await setTheme(page, "dark");
    await expect(page.locator(".quality-shot")).toHaveCount(6);
    await expect(page.locator(".activity-list > article")).toHaveCount(8);
    expect(
      await page.evaluate(() => document.documentElement.scrollHeight),
    ).toBeLessThan(6500);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    if (width < 1024) {
      await expect(page.locator("#studio-navigation")).toHaveAttribute(
        "inert",
        "",
      );
      await expect(
        page.getByRole("button", { name: "展开导航", exact: true }),
      ).toBeVisible();
    }
    await page.getByRole("button", { name: "需关注", exact: true }).click();
    await expect(page.locator(".quality-shot")).toHaveCount(2);
    await page
      .locator(".quality-shot")
      .first()
      .getByText("完整质检记录", { exact: true })
      .click();
    const content = page
      .locator(".quality-shot")
      .first()
      .getByLabel("S01 完整质检记录");
    expect(
      await content
        .locator("dd")
        .evaluateAll((elements) =>
          elements
            .filter((element) => element.getClientRects().length)
            .every((element) => element.getBoundingClientRect().width > 100),
        ),
    ).toBeTruthy();
    await openProjectPage(page, "script");
    await expect(
      page
        .locator(".script-workspace > .panel:first-child > .readable")
        .getByText("成年角色通过行动解释误会。", { exact: true }),
    ).toBeVisible();
    await expect(
      page
        .locator(".script-workspace > .panel:first-child > .readable")
        .getByText("合成人物", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".shot-story-card")).toHaveCount(6);
    await page.getByRole("button", { name: "下一页分镜" }).click();
    await expect(
      page.locator('.shot-story-card [data-shot="S07"]'),
    ).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollHeight),
    ).toBeLessThan(6500);
    await openProjectPage(page, "shots");
    const video = page.locator(".candidate-card video").first();
    await expect(video).toBeVisible();
    await expect(page.getByLabel("首帧提示词")).not.toBeVisible();
    const y = await video.evaluate(
      (element) => element.getBoundingClientRect().top + scrollY,
    );
    expect(y).toBeLessThan(width < 1024 ? 1100 : 800);
    await page.getByText("单镜精修", { exact: true }).click();
    await page.getByLabel("首帧提示词").fill("A retained refinement draft");
    await openProjectPage(page, "quality");
    await openProjectPage(page, "shots");
    await expect(page.getByLabel("首帧提示词")).toHaveValue(
      "A retained refinement draft",
    );
  });

test("project navigation does not push content away or stretch its grouped buttons", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#project=012345abcdef&tab=quality");
  const before = await page.locator(".quality-panel").boundingBox();
  const more = page.locator(".project-more");
  const trigger = more.locator(":scope > summary");
  await trigger.click();
  const menu = more.locator(".project-navigation-menu");
  await expect(menu.locator("button")).toHaveCount(4);
  expect(
    await menu
      .locator("button")
      .evaluateAll((buttons) =>
        buttons.every(
          (button) =>
            button.getBoundingClientRect().height >= 44 &&
            button.getBoundingClientRect().height < 65,
        ),
      ),
  ).toBeTruthy();
  expect((await page.locator(".quality-panel").boundingBox()).y).toBe(before.y);
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(trigger).toBeFocused();
});

for (const width of [320, 390, 768])
  test(`connection changes save once with a reachable footer and preserve failed input at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/#view=models");
    await setTheme(page, "dark");
    await manageService(page);
    const dialog = page.locator("#service-dialog");
    await dialog.locator(".model-options summary").first().click();
    await dialog
      .getByLabel("模型 ID", { exact: true })
      .fill("revised-local-draft");
    const save = dialog.getByRole("button", { name: "保存连接", exact: true });
    await expect(save).toBeInViewport();
    await page.route("**/api/factory/models", (route) =>
      route.request().method() === "POST"
        ? route.fulfill({ status: 500, json: { error: "合成保存失败" } })
        : route.continue(),
    );
    await save.click();
    await expect(dialog.getByRole("alert")).toContainText("合成保存失败");
    await expect(save).toBeEnabled();
    await expect(save).toBeInViewport();
    await expect(dialog.getByLabel("模型 ID", { exact: true })).toHaveValue(
      "revised-local-draft",
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  });
