import { test, expect } from "@playwright/test";
const pid = "012345abcdef";
const base = `/api/factory/projects/${pid}/quality-baselines`;
const first = {
  schema: "ai-studio.quality-baseline.v1",
  protocol: "recorded-project-comparison.v1",
  id: "a".repeat(32),
  project_id: pid,
  label: "优化前的固定版本",
  created_at: "2026-10-19T02:00:00Z",
  project_revision: 7,
  versions: { production_revision: 1 },
  input_sha256: "1".repeat(64),
  project_sha256: "2".repeat(64),
  source_sha256: "3".repeat(64),
  gaps: [],
  metrics: {
    observed_operations: 2,
    failed_operations: 1,
    elapsed_seconds_sum: 7.5,
    elapsed_known_count: 1,
    schema_retries: 1,
    review_repairs: {},
    costs: [],
    unconfirmed_cost_count: 0,
  },
  reviews: [
    {
      scope: "script_review",
      status: "fail",
      summary: "S01 缺少可见的行动理由",
      issues: [],
    },
  ],
  records: [],
};
const second = {
  ...first,
  id: "b".repeat(32),
  label: "优化后的固定版本",
  project_revision: 8,
  reviews: [
    {
      scope: "script_review",
      status: "pass",
      summary: "已有新的审核记录",
      issues: [],
    },
  ],
};

test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

for (const width of [1440, 390, 320])
  for (const theme of ["light", "dark"]) {
    test(`freeze and compare recorded evidence at ${width}px ${theme}`, async ({
      page,
    }) => {
      const errors = [],
        writes = [];
      let saved = [second, first];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.addInitScript(
        (theme) => localStorage.setItem("studio.theme", theme),
        theme,
      );
      await page.route(`**${base}**`, async (route) => {
        const req = route.request(),
          url = new URL(req.url());
        let result;
        if (req.method() === "POST") {
          const body = req.postDataJSON();
          writes.push(body);
          result = { ...second, id: body.request_id, label: body.label };
          saved = [result, ...saved];
        } else if (url.pathname.endsWith("/compare")) {
          result = {
            baseline: first,
            candidate: second,
            same_inputs: false,
            reasons: ["固定反馈不同，不能作为同输入效果实验"],
            note: "历史记录比较",
            review_changes: [],
          };
        } else if (url.pathname === base)
          result = { baselines: saved, unreadable_baselines: 0 };
        else result = saved.find((s) => url.pathname.endsWith(s.id)) || second;
        await route.fulfill({ json: result });
      });
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`/#project=${pid}&tab=decisions`);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await page.locator('[data-project-page="comparison"]').click();
      await expect(page.locator("[data-quality-comparison]")).toBeVisible();
      await page.getByLabel("基线版本", { exact: true }).selectOption(first.id);
      await page
        .getByLabel("候选版本", { exact: true })
        .selectOption(second.id);
      await expect(page.getByText("尚未建立真实效果改善结论")).toBeVisible();
      await expect(
        page.getByText("固定反馈不同，不能作为同输入效果实验"),
      ).toBeVisible();
      await expect(
        page.getByText("费用：未知（未记录可汇总的云任务费用）").first(),
      ).toBeVisible();
      expect(writes).toHaveLength(0);
      await page.getByLabel("快照名称").fill("手工冻结的新版本");
      await page.getByRole("button", { name: "冻结当前版本" }).click();
      await expect(page.getByLabel("快照名称")).toHaveValue("");
      expect(writes).toHaveLength(1);
      expect(writes[0].expected_revision).toBe(7);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `runtime/browser-report/quality-comparison-${theme}-${width}.png`,
        fullPage: true,
      });
      expect(errors).toEqual([]);
    });
  }
