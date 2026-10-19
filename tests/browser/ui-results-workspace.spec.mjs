import { openDetails, closeDetails } from "./fixed-canvas.mjs";
import { setTheme } from "./theme.mjs";
import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { openProjectPage, openWorkflow } from "./navigation.mjs";

const id = "012345abcdef";
test.beforeEach(async ({ request, page }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

for (const theme of ["light", "dark"])
  for (const width of [320, 390, 768, 1024, 1440, 1920])
    test(`results and download are reachable before scrolling at ${width}px in ${theme}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(`/#project=${id}&tab=overview`);
      await setTheme(page, theme);
      const view = page.locator("[data-project-result]");
      const download = page.locator("[data-project-download]");
      await expect(view).toBeInViewport({ ratio: 1 });
      await expect(download).toBeInViewport({ ratio: 1 });
      expect(await page.evaluate(() => scrollY)).toBe(0);
      await expect(page.locator(".project-navigation")).toBeVisible();
      await expect(
        page.locator(".project-primary-navigation [data-tab]"),
      ).toHaveCount(6);
      await expect(page.locator(".project-process")).not.toHaveAttribute(
        "open",
      );
      const geometry = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth,
        outputTop: document
          .querySelector(".project-focus")
          .getBoundingClientRect().top,
        processTop: document
          .querySelector(".project-process")
          .getBoundingClientRect().top,
        sidebarRight: document.querySelector(".sidebar").getBoundingClientRect()
          .right,
      }));
      expect(geometry.overflow).toBe(false);
      expect(geometry.outputTop).toBeLessThan(600);
      expect(geometry.outputTop).toBeLessThan(geometry.processTop);
      if (width < 1024) expect(geometry.sidebarRight).toBeLessThanOrEqual(0);
      if ([390, 1440].includes(width))
        await page.screenshot({
          path: `runtime/browser-report/results-${theme}-${width}.png`,
          fullPage: true,
        });
      const before = await page.locator(".project-pane").boundingBox();
      const more = page.locator(".project-more");
      const trigger = more.locator(":scope > summary");
      await trigger.click();
      const menu = more.locator(".project-navigation-menu");
      await expect(menu.locator("button")).toHaveCount(3);
      expect((await page.locator(".project-pane").boundingBox()).y).toBe(
        before.y,
      );
      const bounds = await menu.boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(844);
      await page.keyboard.press("Escape");
      await expect(menu).toBeHidden();
      await expect(trigger).toBeFocused();
      const filePromise = page.waitForEvent("download");
      await download.click();
      const file = await filePromise;
      expect(await file.failure()).toBeNull();
      const bytes = await readFile(await file.path());
      expect(bytes.subarray(4, 8).toString()).toBe("ftyp");
      await view.click();
      await expect(page).toHaveURL(/tab=delivery/);
      await expect(page.locator(".delivery-video")).toBeVisible();
      expect(
        await page
          .locator(".delivery-video")
          .evaluate((el) => el.getBoundingClientRect().top + scrollY),
      ).toBeLessThan(700);
      await openProjectPage(page, "overview");
      await openWorkflow(page);
      await page.locator('[data-stage="script"]').click();
      await openDetails(page);
      await page.locator('[data-flow-node="screenplay"]').click();
      await expect(page.locator(".workflow-step-heading h3")).toHaveText(
        "剧本创作",
      );
      await closeDetails(page);
      // Folding the process preserves the selected summary and canvas.
      const inspector = await page.locator(".workflow-summary").elementHandle();
      await page.locator(".project-process > summary").click();
      await openWorkflow(page);
      expect(
        await inspector.evaluate(
          (element) => element === document.querySelector(".workflow-summary"),
        ),
      ).toBe(true);
      await expect(page.locator(".workflow-summary h3")).toHaveText("剧本创作");
    });

test("unfinished projects show available shots and explain why full video cannot be downloaded", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.route("**/workspace", async (route) => {
    const response = await route.fetch(),
      body = await response.json();
    body.project.delivery = {};
    body.project.title = "这是用于检查中文较长项目名称换行的短剧作品";
    await route.fulfill({ response, json: body });
  });
  await page.goto(`/#project=${id}`);
  await expect(page.locator("[data-project-download]")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "下载成片", exact: true }),
  ).toBeDisabled();
  await expect(page.getByText("成片尚未生成", { exact: true })).toBeVisible();
  await expect(page.locator("[data-project-result]")).toBeInViewport({
    ratio: 1,
  });
  await expect(page.locator(".current-output-video")).toContainText("镜头预览");
  await page.locator("[data-project-result]").click();
  await expect(page).toHaveURL(/tab=shots/);
  await expect(page.locator(".shot-workbench")).toBeVisible();
});

test("a team receipt opens its exact workflow step while project actions remain keyboard accessible", async ({
  page,
  request,
}) => {
  await page.goto(`/#project=${id}&tab=team`);
  await page
    .locator('[data-execution="writer-current"]')
    .getByRole("button", { name: "定位步骤与记录" })
    .click();
  await expect(page.locator(".project-process")).toHaveAttribute("open", "");
  await expect(page.locator(".workflow-step-heading h3")).toHaveText(
    "剧本创作",
  );
  await expect(page.locator(".execution-record-focus")).toContainText(
    "mock-writer-current",
  );
  await closeDetails(page);
  const summary = page.locator(".project-actions-menu > summary");
  await summary.click();
  await expect(
    page.getByRole("button", { name: "按新规格重制" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(summary).toBeFocused();
  await expect(page.locator(".project-actions-menu")).not.toHaveAttribute(
    "open",
  );
  const calls = await (await request.get("/__test/calls")).json();
  expect(
    calls.filter(
      (call) => call.method === "POST" && call.path.startsWith("/api/"),
    ),
  ).toEqual([]);
});
