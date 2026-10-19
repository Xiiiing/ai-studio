import { test, expect } from "@playwright/test";
import { lifecycleFixture } from "./lifecycle-fixture.mjs";
import { mkdir } from "node:fs/promises";

const id = "dd1234567890";
function fixture() {
  return {
    id,
    title: "两集规划恢复样例",
    record_kind: "drama",
    revision: 8,
    episode_count: 2,
    duration_seconds: 120,
    format: "animation",
    status: "blocked",
    stage: "plan_repair",
    plan_version: 0,
    plan_phase: "exhausted",
    plan_attempt: 3,
    plan_max_attempts: 3,
    plan_cycle: 1,
    episodes: [],
    batches: [],
    plans: [],
    error: "规划审核仍未通过，已保留草稿",
    plan_review: {
      passed: false,
      issues: ["第二集重复第一集，需要新的事件。"],
    },
    workflow: lifecycleFixture({ active_job: false, tasks: [] }, id),
    draft_plan: {
      world: "成年人生活的虚构城市",
      storyline: "两位旧友寻找旧信的寄信人，最终查明真相。",
      visual_rules: "统一水彩画风",
      relationships: [],
      scenes: [],
      props: [],
      uncertainties: [],
      characters: [
        {
          character_id: "lead",
          name: "林",
          voice_id: "female",
          identity_constraints: "成年短发女性",
          default_wardrobe: "蓝色外套",
        },
      ],
      episodes: [1, 2].map((number) => ({
        number,
        title: `第${number}封信`,
        synopsis: `沿着第${number}封信发现新的线索。`,
        characters: ["lead"],
        opening_state: "抵达",
        ending_state: "找到线索",
        depends_on: [],
      })),
    },
  };
}

for (const width of [1440, 390])
  for (const theme of ["light", "dark"]) {
    test(`blocked planning has an actionable repair, confirmation and generation path ${width} ${theme}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(
        (t) => localStorage.setItem("studio.theme", t),
        theme,
      );
      const drama = fixture(),
        writes = [];
      await page.route(`**/api/factory/projects/${id}/workspace`, (route) =>
        route.fulfill({
          json: {
            project: { id, title: drama.title, record_kind: "drama" },
            revision: drama.revision,
            workflow: {},
            flow: {},
            operator: {},
          },
        }),
      );
      await page.route(`**/api/factory/dramas/${id}**`, async (route) => {
        if (route.request().method() === "POST") {
          const body = route.request().postDataJSON();
          writes.push(body);
          if (writes.length === 1)
            return route.fulfill({
              status: 503,
              json: { error: "模拟提交响应中断" },
            });
          drama.revision++;
          if (route.request().url().endsWith("/confirm")) {
            drama.plan_version = 1;
            drama.stage = "episodes";
            drama.status = "ready";
            drama.episodes = [1, 2].map((number) => ({
              id: `ee123456789${number}`,
              episode_number: number,
              status: "draft",
              title: `第${number}集`,
              duration_seconds: 120,
              outline: { synopsis: "沿着旧信发现新的线索。" },
              plan_version: 1,
              progress: 0,
            }));
          } else {
            drama.status = "queued";
            drama.stage = "plan_repair";
            drama.plan_phase = "writing";
            drama.workflow.active_job = true;
          }
        }
        await route.fulfill({ json: drama });
      });
      await page.goto(`/#project=${id}&tab=workflow`);
      const next = page.getByRole("region", { name: "制作下一步" });
      await expect(next).toContainText("视频制作尚未开始");
      await next.getByRole("button", { name: "查看问题并修复规划" }).click();
      await expect(page).toHaveURL(/tab=plan/);
      await expect(
        page.getByText("第二集重复第一集，需要新的事件。", { exact: true }),
      ).toBeVisible();
      const input = next.getByRole("textbox", {
        name: "补充本轮规划要求（可选）",
      });
      await input.fill("第二集调查新线索，不重复第一集。保持每集120秒。");
      const world = page.getByRole("textbox", {
        name: "世界与背景",
        exact: true,
      });
      await world.fill("我正在修改的世界设定，尚未提交。");
      await expect(
        next.getByRole("button", { name: "修复规划并继续" }),
      ).toBeDisabled();
      await expect(
        page.getByRole("button", { name: "继续规划", exact: true }),
      ).toBeDisabled();
      await next.getByRole("button", { name: "前往提交规划修改" }).click();
      await expect(
        page.getByRole("button", { name: "提交修改并重新审核", exact: true }),
      ).toBeFocused();
      await world.fill(drama.draft_plan.world);
      await expect(
        next.getByRole("button", { name: "修复规划并继续" }),
      ).toBeEnabled();
      await expect(input).toHaveValue(
        "第二集调查新线索，不重复第一集。保持每集120秒。",
      );
      if (
        (width === 1440 && theme === "light") ||
        (width === 390 && theme === "dark")
      ) {
        await mkdir("docs/images/planning-recovery", { recursive: true });
        await page.locator(".main-shell").evaluate((el) => {
          el.scrollTop = 0;
        });
        await page.evaluate(() => window.scrollTo(0, 0));
        await expect(next.getByRole("heading")).toBeInViewport();
        await page.screenshot({
          path: `docs/images/planning-recovery/repair-${width}-${theme}.png`,
        });
      }
      await next.getByRole("button", { name: "修复规划并继续" }).click();
      await expect(
        page.getByText("模拟提交响应中断", { exact: true }),
      ).toBeVisible();
      await expect(input).toHaveValue(
        "第二集调查新线索，不重复第一集。保持每集120秒。",
      );
      await next.getByRole("button", { name: "修复规划并继续" }).click();
      await expect(
        next.getByRole("heading", { name: "正在规划与审核" }),
      ).toBeVisible();
      expect(writes[0]).toEqual(writes[1]);
      expect(writes[1]).toMatchObject({
        action: "resume",
        expected_revision: 8,
        feedback: "第二集调查新线索，不重复第一集。保持每集120秒。",
      });
      await expect(
        next.getByRole("button", { name: "修复规划并继续" }),
      ).toHaveCount(0);
      // A later independent result unlocks confirmation; the repair action itself
      // must never approve a plan or submit a paid generation.
      drama.status = "awaiting_plan";
      drama.plan_phase = "approved";
      drama.stage = "plan_confirmation";
      drama.plan_review = { passed: true, issues: [] };
      drama.error = null;
      drama.workflow.active_job = false;
      await page.goto(`/#project=${id}&tab=overview`);
      await next.getByRole("button", { name: "检查并确认规划" }).click();
      await next.getByRole("button", { name: "前往确认规划" }).click();
      await expect(
        page.getByRole("button", { name: "确认全剧规划", exact: true }),
      ).toBeFocused();
      await page
        .getByRole("button", { name: "确认全剧规划", exact: true })
        .click();
      await next.getByRole("button", { name: "选择剧集并设置预算" }).click();
      await expect(page).toHaveURL(/tab=episodes/);
      await expect(
        page.getByRole("heading", { name: "选择本次制作的剧集" }),
      ).toBeVisible();
      await page
        .getByRole("checkbox", { name: "选择第1集", exact: true })
        .check();
      await expect(
        page.getByRole("button", { name: "生成所选 1 集" }),
      ).toBeEnabled();
      expect(writes).toHaveLength(3);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
    });
  }
