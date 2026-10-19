import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { setTheme } from "./theme.mjs";
import {
  controlGeometry,
  expectCenteredControl,
  documentAnchors,
  expectAnchorsUnchanged,
} from "./control-alignment.mjs";

const projectId = "012345abcdef";
const titles = {
  short: "浏览器模拟样片",
  long: "雨夜车站重逢与城市救援行动：人物关系和完整故事版本的制作审核计划",
};
test.beforeAll(async () => {
  await mkdir("runtime/ui-control-alignment", { recursive: true });
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

for (const width of [320, 390, 1440])
  for (const theme of ["light", "dark"])
    for (const title of ["short", "long"])
      for (const delivery of ["pending", "ready"])
        test(`project control contents align at ${width}px ${theme} ${title} ${delivery}`, async ({
          page,
          request,
        }) => {
          await page.setViewportSize({ width, height: 1000 });
          await page.route(
            `**/projects/${projectId}/workspace`,
            async (route) => {
              const response = await route.fetch(),
                body = await response.json();
              body.project.title = titles[title];
              if (delivery === "pending") body.project.delivery = {};
              await route.fulfill({ response, json: body });
            },
          );
          await page.goto(`/#project=${projectId}&tab=overview`);
          await setTheme(page, theme);
          await expect(page.locator(".shared-page-header h1")).toHaveText(
            titles[title],
          );
          await expect(
            page.locator(".shared-page-header h1"),
          ).toHaveAccessibleName(titles[title]);
          await expect(page.locator(".shared-page-header h1")).toHaveAttribute(
            "title",
            titles[title],
          );
          const renderedTitle = await page
            .locator(".header-title-text")
            .evaluate((element) => ({
              height: element.getBoundingClientRect().height,
              lineHeight: parseFloat(getComputedStyle(element).lineHeight),
            }));
          expect(renderedTitle.height).toBeLessThanOrEqual(
            renderedTitle.lineHeight * 2 + 1,
          );
          const summary = page.getByRole("button", {
            name: "项目操作",
            exact: true,
          });
          const summaryGeometry = await controlGeometry(summary);
          await test.info().attach("summary-content-geometry", {
            body: JSON.stringify(summaryGeometry, null, 2),
            contentType: "application/json",
          });
          // Equal outer top coordinates did not detect the old native summary:
          // its actual text sat near the top while neighboring button text centered.
          expectCenteredControl(summaryGeometry, "项目操作");
          await expect(summary).toHaveAccessibleName("项目操作");
          expect(
            await summary.evaluate(
              (element) =>
                element.tagName === "BUTTON" &&
                element.getAttribute("aria-haspopup") === "menu",
            ),
          ).toBe(true);
          await expect(summary.locator("svg")).toHaveCount(1);
          await expect(summary.locator("svg")).toHaveAttribute(
            "aria-hidden",
            "true",
          );
          await expect(summary.locator("svg")).toHaveCSS("width", "16px");
          await expect(summary.locator("svg")).toHaveCSS("height", "16px");

          const business = [
            page.locator("[data-project-result]"),
            delivery === "ready"
              ? page.locator("[data-project-download]")
              : page.getByRole("button", { name: "下载成片", exact: true }),
            summary,
          ];
          const businessBox = await page
            .locator(".heading-actions")
            .boundingBox();
          const businessGeometry = [];
          for (const control of business) {
            const geometry = await controlGeometry(control);
            expectCenteredControl(geometry);
            businessGeometry.push(geometry);
          }
          for (const left of businessGeometry)
            for (const right of businessGeometry) {
              const overlap =
                Math.min(
                  left.box.y + left.box.height,
                  right.box.y + right.box.height,
                ) - Math.max(left.box.y, right.box.y);
              if (overlap > 22)
                expect(
                  Math.abs(left.box.y - right.box.y),
                  "business controls sharing a visual row align",
                ).toBeLessThanOrEqual(1);
            }
          const businessTop = Math.min(
            ...businessGeometry.map((geometry) => geometry.box.y),
          );
          const businessBottom = Math.max(
            ...businessGeometry.map(
              (geometry) => geometry.box.y + geometry.box.height,
            ),
          );
          expect(
            Math.abs(businessBox.height - businessBottom + businessTop),
            "pending reason must not add a caption slot to the action toolbar",
          ).toBeLessThanOrEqual(1);
          const titleRow = await page
            .locator(".header-title-row")
            .boundingBox();
          for (const control of await page
            .locator(
              ".header-title-row .icon-button:visible, .page-tools-trigger:visible, .page-tools-content .icon-button:visible",
            )
            .all()) {
            const geometry = await controlGeometry(control);
            expectCenteredControl(geometry);
            expect(
              Math.abs(geometry.box.y - titleRow.y),
              `${geometry.label}: title/toolbar first row`,
            ).toBeLessThanOrEqual(1);
          }
          if (width >= 1024)
            expect(
              Math.abs(businessTop - titleRow.y),
              "desktop business actions share the title/toolbar row",
            ).toBeLessThanOrEqual(1);
          expect(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth,
            ),
          ).toBe(true);

          if (delivery === "pending") {
            const download = business[1];
            await expect(download).toBeDisabled();
            await expect(download).toHaveAccessibleDescription("成片尚未生成");
            const describedBy = (
              await download.getAttribute("aria-describedby")
            ).split(/\s+/);
            expect(describedBy.length).toBeGreaterThan(0);
            const reason = page.locator(`[id="${describedBy[0]}"]`);
            await expect(reason).toHaveText("成片尚未生成");
            await expect(reason).toBeVisible();
            expect(
              await page
                .locator("[id]")
                .evaluateAll(
                  (elements, value) =>
                    elements.filter((element) => element.id === value).length,
                  describedBy[0],
                ),
            ).toBe(1);
            const reasonBox = await reason.boundingBox();
            for (const { box } of businessGeometry)
              expect(
                reasonBox.x < box.x + box.width &&
                  reasonBox.x + reasonBox.width > box.x &&
                  reasonBox.y < box.y + box.height &&
                  reasonBox.y + reasonBox.height > box.y,
                "download explanation does not cover business controls",
              ).toBe(false);
          } else {
            await expect(
              page.getByText("成片尚未生成", { exact: true }),
            ).toHaveCount(0);
            await expect(business[1]).toHaveAttribute("download", "");
          }
          await page.locator(".shared-page-header").screenshot({
            path: `runtime/ui-control-alignment/header-${width}-${theme}-${title}-${delivery}.png`,
            animations: "disabled",
          });

          const baseline = await documentAnchors(page),
            menu = page.getByRole("menu");
          await summary.focus();
          await page.keyboard.press("Enter");
          await expect(menu).toBeVisible();
          for (const action of await menu.getByRole("menuitem").all()) {
            const box = await action.boundingBox();
            expect(box.x).toBeGreaterThanOrEqual(0);
            expect(box.x + box.width).toBeLessThanOrEqual(width);
            expect(box.y).toBeGreaterThanOrEqual(0);
            expect(box.y + box.height).toBeLessThanOrEqual(1000);
            expect(
              await action.evaluate((element) => {
                const rect = element.getBoundingClientRect();
                return element.contains(
                  document.elementFromPoint(
                    rect.x + rect.width / 2,
                    rect.y + rect.height / 2,
                  ),
                );
              }),
              "project menu action is actually reachable at its visible center",
            ).toBe(true);
          }
          await expectAnchorsUnchanged(page, baseline);
          await expect(menu.getByRole("menuitem").first()).toBeFocused();
          await page.keyboard.press("ArrowDown");
          await expect(menu.getByRole("menuitem").nth(1)).toBeFocused();
          await page.keyboard.press("Escape");
          await expect(menu).toBeHidden();
          await expect(summary).toBeFocused();
          await expectAnchorsUnchanged(page, baseline);
          await page.keyboard.press("Space");
          await expect(menu).toBeVisible();
          await page.locator(".project-heading h1").click();
          await expect(menu).toBeHidden();
          await expectAnchorsUnchanged(page, baseline);
          await summary.click();
          await page
            .getByRole("button", { name: "查看项目状态", exact: true })
            .focus();
          await expect(menu).toBeHidden();
          await expectAnchorsUnchanged(page, baseline);
          const calls = await (await request.get("/__test/calls")).json();
          expect(
            calls.filter(
              (call) => call.method === "POST" && call.path.startsWith("/api/"),
            ),
          ).toEqual([]);
        });
