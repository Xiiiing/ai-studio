import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
const id = "dd1122334466";
for (const width of [1440, 390])
  for (const theme of ["light", "dark"]) {
    test(`review returns to authors and brand stays consistent at ${width} ${theme}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      const p = {
        id,
        record_kind: "drama",
        title: "审核自动返修演示",
        revision: 8,
        episode_count: 2,
        duration_seconds: 300,
        format: "animation",
        status: "creating",
        stage: "plan_repair",
        plan_version: 0,
        plans: [],
        episodes: [],
        batches: [],
        plan_phase: "returned",
        plan_cycle: 1,
        plan_attempt: 1,
        plan_max_attempts: 3,
        plan_returned_to: ["story_editor", "screenwriter"],
        plan_review: {
          passed: false,
          issues: ["开场需承接前集持有信件的状态"],
        },
        review_history: [
          {
            cycle: 1,
            attempt: 1,
            review: { passed: false, issues: ["开场需承接前集持有信件的状态"] },
            returned_to: ["story_editor", "screenwriter"],
          },
        ],
        workflow: { active_job: true, stages: [], tasks: [] },
      };
      await page.route(`**/api/factory/projects/${id}/workspace`, (r) =>
        r.fulfill({
          json: {
            project: { id, record_kind: "drama" },
            revision: 8,
            operator: {},
            flow: {},
            workflow: {},
          },
        }),
      );
      await page.route(`**/api/factory/dramas/${id}`, (r) =>
        r.fulfill({ json: p }),
      );
      const logoResponse = page.waitForResponse((r) =>
        r.url().endsWith("/brand.svg"),
      );
      await page.goto("/#project=" + id + "&tab=plan");
      await expect(
        page.getByRole("heading", { name: "审核与自动返修", exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText("策划与编剧正在返修", { exact: true }),
      ).toBeVisible();
      await page.getByText("查看审核与退回记录", { exact: true }).click();
      await expect(
        page.getByText("退回：故事统筹、编剧", { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText("第1 / 3轮审核", { exact: true }),
      ).toBeVisible();
      if (width < 1024)
        await page.getByRole("button", { name: "展开导航" }).click();
      expect((await logoResponse).status()).toBe(200);
      expect(await page.title()).toBe("映序");
      await expect(
        page.getByText("AI PRODUCTION STUDIO", { exact: true }),
      ).toHaveCount(0);
      await expect(
        page.getByText("自主协作 · 人工掌舵", { exact: true }),
      ).toHaveCount(0);
      await expect(page.locator(".sidebar-footer, .app-footer")).toHaveCount(0);
      const iconHref = await page
        .locator('link[rel="icon"]')
        .getAttribute("href");
      expect(iconHref).toContain("brand.svg");
      const symbol = page.locator(".brand-symbol");
      await expect(symbol).toHaveAttribute("src", /brand.svg$/);
      if (width < 1024) {
        await expect(page.locator(".brand-symbol")).toBeVisible();
      } else await expect(page.locator(".brand-symbol")).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await fs.mkdir("docs/images/review-return", { recursive: true });
      await page.screenshot({
        path: `docs/images/review-return/brand-${theme}-${width}.png`,
      });
      if (width < 1024) await page.keyboard.press("Escape");
      p.plan_phase = "approved";
      p.plan_attempt = 2;
      p.status = "awaiting_plan";
      p.plan_review = { passed: true, issues: [] };
      p.workflow.active_job = false;
      p.review_history.push({
        cycle: 1,
        attempt: 2,
        review: { passed: true, issues: [] },
        returned_to: [],
      });
      await page.reload();
      await expect(
        page.getByText("审核通过，待确认", { exact: true }),
      ).toBeVisible();
      expect(errors).toEqual([]);
    });
  }
