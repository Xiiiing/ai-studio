import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
for (const width of [1440, 390])
  for (const scheme of ["light", "dark"]) {
    test(`one sentence authorizes fixed three-episode scope ${width} ${scheme}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
      let submitted;
      await page.route("**/api/factory/dramas", (route) => {
        submitted = route.request().postDataJSON();
        return route.fulfill({
          status: 201,
          json: { id: "dd0340340340", record_kind: "drama", revision: 1 },
        });
      });
      await page.goto("/");
      if (width === 390) await page.getByRole("button", {name:"展开导航",exact:true}).click();
      await page
        .getByRole("button", { name: "新建制作", exact: true })
        .first()
        .click();
      const form = page.getByRole("dialog");
      await expect(
        form.getByRole("checkbox", { name: "启用整剧制作闭环" }),
      ).toBeChecked();
      await form
        .getByLabel("创作方向", { exact: true })
        .fill("成年人在车站发现旧信，逐集查明真相并完成和解。");
      await form
        .getByRole("button", { name: "使用 3 集 × 60 秒预设", exact: true })
        .click();
      await expect(form.getByText(/整剧固定额度：360 GPU 分钟/)).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await mkdir("runtime/browser-034/screenshots", { recursive: true });
      await page.screenshot({
        path: `runtime/browser-034/screenshots/create-${width}-${scheme}.png`,
        fullPage: true,
      });
      await form
        .getByRole("button", { name: "创建并开始制作", exact: true })
        .click();
      await expect.poll(() => submitted?.production_mode).toBe("one_sentence");
      expect(submitted.episode_count).toBe(3);
      expect(submitted.duration_seconds).toBe(60);
      expect(submitted.automation.episode_numbers).toEqual([1, 2, 3]);
    });
  }
