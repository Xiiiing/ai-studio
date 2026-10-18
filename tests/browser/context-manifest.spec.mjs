import { test, expect } from "@playwright/test";

for (const width of [1440, 390])
  for (const theme of ["light", "dark"]) {
    test(`recorded context in employee detail ${width}px ${theme}`, async ({
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
      await page.getByRole("button", { name: "部门协作", exact: true }).click();
      const collaboration = page.getByRole("region", {
        name: "部门协作工作流",
      });
      await collaboration
        .getByRole("navigation", { name: "项目部门" })
        .locator('[data-map-node="story"]')
        .click();
      await collaboration
        .getByRole("navigation", { name: "部门岗位" })
        .locator('[data-map-node="screenwriter"]')
        .click();
      const call = collaboration.locator('[data-execution="writer-current"]');
      await call.getByText("本次收到的材料 · 1 项", { exact: true }).click();
      await expect(call.getByText("合成验收要求：结尾保留悬念")).toBeVisible();
      await call.getByText("来源与版本", { exact: true }).click();
      await expect(
        call.getByText("材料版本：version: 1", { exact: true }),
      ).toBeVisible();
      await expect(call).toContainText("不代表模型已正确理解或落实");
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `runtime/browser-report/context-${theme}-${width}.png`,
        fullPage: true,
      });
      expect(errors).toEqual([]);
      expect(writes).toEqual([]);
    });
  }
