import { refreshPage } from "./header-flow.mjs";
import { test, expect } from "@playwright/test";
import {
  canvasState,
  expandRole,
  assertUnobstructedLines,
} from "./fixed-canvas.mjs";
for (const width of [1440, 1024, 390, 320])
  for (const theme of ["light", "dark"]) {
    test(`fixed canvas keeps coordinates during inspection, expansion and refresh ${width} ${theme}`, async ({
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
      await expect(page.locator(".workflow-step-heading h3")).toHaveText(
        "故事目标落实与独立审核",
      );
      const disclosure = stage
        .locator("..")
        .locator(":scope > button[aria-expanded]");
      await disclosure.click();
      const expanded = await canvasState(page);
      expect(
        expanded.nodes.filter(([id]) =>
          initial.nodes.some(([old]) => id === old),
        ),
      ).toEqual(initial.nodes);
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
      await expect(
        page.locator(".workflow-fixed-inspector h3").first(),
      ).toContainText("编剧");
      expect(await canvasState(page)).toEqual(beforeRole);
      await page.getByRole("button", { name: "关闭详情", exact: true }).click();
      expect(await canvasState(page)).toEqual(beforeRole);
      // Collapse the department without repacking another card or shrinking the board.
      const department = role.locator("..").getAttribute("data-map-node");
      const parentId = (await department).split("/").slice(0, -1).join("/");
      const parent = page.locator(
        `[data-map-node="${parentId}"] > button[aria-expanded]`,
      );
      await parent.scrollIntoViewIfNeeded();
      const beforeCollapse = await canvasState(page);
      await parent.click();
      const collapsed = await canvasState(page);
      expect(collapsed.nodes).toEqual(
        beforeCollapse.nodes.filter(([id]) => !id.startsWith(parentId + "/")),
      );
      expect([collapsed.left, collapsed.top, collapsed.board]).toEqual([
        beforeCollapse.left,
        beforeCollapse.top,
        beforeCollapse.board,
      ]);
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
