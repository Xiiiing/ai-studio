import { refreshPage } from "./header-flow.mjs";
import { test, expect } from "@playwright/test";
import {
  canvasState,
  openDetails,
  closeDetails,
  expandRole,
  assertUnobstructedLines,
} from "./fixed-canvas.mjs";
for (const width of [1440, 1024, 390, 320])
  for (const theme of ["light", "dark"]) {
    test(`compact canvas keeps camera during inspection and restores disclosures ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await request.post("/__test/reset");
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(
        (t) => localStorage.setItem("studio.theme", t),
        theme,
      );
      const errors = [],
        writes = [];
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("request", (r) => {
        if (r.url().includes("/api/") && r.method() !== "GET")
          writes.push(r.url());
      });
      await page.goto("/#project=012345abcdef&tab=workflow");
      const canvas = page.locator(".workflow-canvas-viewport");
      await expect(canvas).toBeVisible();
      const stage = page.locator('[data-stage="script"]');
      await stage.scrollIntoViewIfNeeded();
      const initial = await canvasState(page),
        url = page.url();
      const pagePosition = await page.evaluate(() => [scrollX, scrollY]);
      await stage.click();
      expect(await page.evaluate(() => [scrollX, scrollY])).toEqual(
        pagePosition,
      );
      expect(await canvasState(page)).toEqual(initial);
      await expect(page.locator(".workflow-summary h3")).toHaveText(
        "故事目标落实与独立审核",
      );
      const disclosure = stage
        .locator("..")
        .locator(":scope > button[aria-expanded]");
      await disclosure.click();
      const expanded = await canvasState(page);
      // Explicit disclosure adds only its visible row height; columns and order stay fixed.
      for (const [id, style] of initial.nodes) {
        const next = expanded.nodes.find(([key]) => key === id)[1];
        expect(next.match(/left: [^;]+/)[0]).toBe(
          style.match(/left: [^;]+/)[0],
        );
      }
      expect([
        expanded.left,
        expanded.top,
        expanded.width,
        expanded.height,
      ]).toEqual([initial.left, initial.top, initial.width, initial.height]);
      const role = await expandRole(page, "screenwriter", "script");
      const beforeRole = await canvasState(page);
      await role.click();
      expect(await canvasState(page)).toEqual(beforeRole);
      await assertUnobstructedLines(page);
      await request.post("/__test/progress");
      await refreshPage(page);
      await expect(page.locator(".workflow-summary h3").first()).toContainText(
        "编剧",
      );
      expect(await canvasState(page)).toEqual(beforeRole);
      await page.getByRole("button", { name: "关闭摘要", exact: true }).click();
      expect(await canvasState(page)).toEqual(beforeRole);
      // Collapsing explicitly releases hierarchy space, while keeping stage columns.
      const department = role.locator("..").getAttribute("data-map-node");
      const parentId = (await department).split("/").slice(0, -1).join("/");
      const parent = page.locator(
        `[data-map-node="${parentId}"] > button[aria-expanded]`,
      );
      await parent.scrollIntoViewIfNeeded();
      const beforeCollapse = await canvasState(page);
      await parent.click();
      const collapsed = await canvasState(page);
      expect(
        collapsed.nodes.some(([id]) => id.startsWith(parentId + "/")),
      ).toBe(false);
      for (const [id, style] of collapsed.nodes) {
        const previous = beforeCollapse.nodes.find(([key]) => key === id)[1];
        expect(style.match(/left: [^;]+/)[0]).toBe(
          previous.match(/left: [^;]+/)[0],
        );
      }
      await page.reload();
      await expect(canvas).toBeVisible();
      expect(await canvasState(page)).toEqual(collapsed);
      expect(page.url()).toBe(url);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.locator(".workflow-fixed-workspace").screenshot({
        path: `runtime/browser-report/fixed-canvas-${width}-${theme}.png`,
      });
      expect(errors).toEqual([]);
      expect(writes).toEqual([]);
    });
  }

for (const width of [1440, 390])
  for (const theme of ["light", "dark"]) {
    test(`summary opens a single full reading card without nested scrolling ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await request.post("/__test/reset");
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(
        (t) => localStorage.setItem("studio.theme", t),
        theme,
      );
      const text = Array.from(
        { length: 120 },
        (_, i) => `第 ${i + 1} 段完整材料：核对这一段的交付、分工和原始内容。`,
      ).join("\n");
      await page.route("**/api/factory/projects/*/workspace", async (route) => {
        const response = await route.fetch(),
          body = await response.json();
        body.flow.steps = [
          {
            id: "research",
            inputs: [{ label: "完整长材料", value: text }],
            outputs: [{ label: "完整交付", value: "独立卡片的交付全文" }],
          },
        ];
        await route.fulfill({ response, json: body });
      });
      await page.goto("/#project=012345abcdef&tab=workflow");
      const root = page.locator('[data-stage="story"]');
      await root.click();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      const summary = page.getByRole("complementary", { name: "工作摘要" });
      await expect(summary).not.toContainText("第 120 段完整材料");
      const viewport = page.locator(".workflow-canvas-viewport");
      expect(
        await summary.evaluate((el) => el.scrollHeight <= el.clientHeight + 1),
      ).toBe(true);
      if (width === 1440) {
        const ys = await page
          .locator("[data-stage]")
          .evaluateAll((nodes) =>
            nodes.map((el) => el.getBoundingClientRect().y),
          );
        expect(Math.max(...ys) - Math.min(...ys)).toBeLessThan(220);
        expect(
          await viewport.evaluate(
            (el) => el.scrollHeight <= el.clientHeight + 1,
          ),
        ).toBe(true);
      }
      await page.locator(".workflow-fixed-workspace").screenshot({
        path: `runtime/browser-report/summary-overview-${width}-${theme}.png`,
      });
      const before = await canvasState(page);
      const card = await openDetails(page);
      await expect(card).toContainText("第 120 段完整材料");
      const scrolling = await card.evaluate((el) =>
        [...el.querySelectorAll("*")]
          .filter((n) => {
            const css = getComputedStyle(n);
            return (
              n.getClientRects().length &&
              /(auto|scroll)/.test(css.overflowY) &&
              n.scrollHeight > n.clientHeight + 2
            );
          })
          .map((n) => n.className),
      );
      expect(scrolling).toEqual(["dialog-body"]);
      await card.locator(".dialog-body").evaluate((el) => {
        el.scrollTop = el.scrollHeight;
      });
      expect(await canvasState(page)).toEqual(before);
      await card.screenshot({
        path: `runtime/browser-report/summary-full-card-${width}-${theme}.png`,
      });
      await closeDetails(page);
      expect(await canvasState(page)).toEqual(before);
    });
  }
