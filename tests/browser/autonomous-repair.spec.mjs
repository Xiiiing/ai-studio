import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { lifecycleFixture } from "./lifecycle-fixture.mjs";

const id = "dd0330330330";
function fixture() {
  return {
    id,
    title: "自主返修停止与恢复模拟",
    record_kind: "drama",
    revision: 8,
    status: "paused",
    pause_requested: true,
    episode_count: 2,
    duration_seconds: 120,
    format: "animation",
    plan_version: 1,
    plans: [],
    batches: [],
    automation: {
      enabled: true,
      suspended: true,
      episode_numbers: [1, 2],
      budget: { gpu_minutes: 120, wall_minutes: 180, max_attempts: 3 },
    },
    automation_state: {
      status: "paused",
      reason: "全剧已在安全边界暂停",
      episode_numbers: [1, 2],
      attention: [],
    },
    episodes: [
      {
        id: "ee0330330331",
        episode_number: 1,
        title: "第 1 集",
        duration_seconds: 120,
        status: "blocked",
        progress: 40,
        active_unit_id: "unit-033",
        error: "S02 视频已用完本次授权尝试，原片段和费用保留。",
        needs_decision: {
          code: "retry_limit",
          details: {
            asset: "S02",
            stage: "video",
            attempts_used: 3,
            attempts_limit: 3,
          },
        },
        outline: { synopsis: "CPU与API模拟验收，不调用真实模型。" },
      },
    ],
    workflow: lifecycleFixture({ active_job: false, tasks: [] }, id),
  };
}

for (const width of [1440, 390])
  for (const theme of ["light", "dark"]) {
    test(`repair activation retains pause and requires explicit resume ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await request.post("/__test/reset");
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.addInitScript(
        (t) => localStorage.setItem("studio.theme", t),
        theme,
      );
      const drama = fixture(),
        writes = [],
        allMutations = [];
      page.on("request", (request) => {
        if (
          request.url().includes("/api/factory/") &&
          request.method() !== "GET"
        )
          allMutations.push({
            url: request.url(),
            body: request.postDataJSON(),
          });
      });
      await page.route(`**/api/factory/projects/${id}/workspace`, (route) =>
        route.fulfill({
          json: {
            project: { id, title: drama.title, record_kind: "drama" },
            revision: drama.revision,
            operator: {},
            workflow: {},
          },
        }),
      );
      await page.route(`**/api/factory/dramas/${id}**`, async (route) => {
        if (route.request().method() === "POST") {
          const body = route.request().postDataJSON();
          writes.push(body);
          expect(route.request().url()).toMatch(/\/actions$/);
          expect(body.expected_revision).toBe(drama.revision);
          drama.revision++;
          if (body.action === "enable_autonomous_repair") {
            drama.automation.production_recovery = {
              version: "ai-studio.production-recovery.v1",
              enabled: true,
            };
            expect(drama.status).toBe("paused");
            expect(drama.pause_requested).toBe(true);
          } else if (body.action === "resume") {
            drama.status = "queued";
            drama.pause_requested = false;
            drama.automation.suspended = false;
            drama.automation_state = {
              status: "blocked",
              reason: drama.episodes[0].error,
              episode_numbers: [1, 2],
              attention: [],
            };
          } else throw new Error(`Unexpected production write: ${body.action}`);
        }
        await route.fulfill({ json: drama });
      });
      await page.goto(`/#project=${id}&tab=production`);
      await expect(
        page.getByRole("heading", { name: drama.title }),
      ).toBeVisible();
      const command = page.getByRole("region", { name: "连续制作状态" });
      await expect(
        command.getByRole("button", { name: "启用自主返修" }),
      ).toBeVisible();
      const attention = page.locator(".production-attention");
      await expect(attention).toContainText(
        "制作对象：S02 · 视频 · 已用尝试 3 / 3",
      );
      await expect(
        attention.getByRole("button", { name: "继续此集", exact: true }),
      ).toHaveCount(0);
      expect(allMutations).toHaveLength(0);
      await command.getByRole("button", { name: "启用自主返修" }).click();
      await expect(command).toContainText("自主返修已启用");
      await expect(command.getByRole("heading")).toContainText(
        "全剧已在安全边界暂停",
      );
      await expect(
        command.getByRole("button", { name: "继续制作", exact: true }),
      ).toBeEnabled();
      expect(writes.map((body) => body.action)).toEqual([
        "enable_autonomous_repair",
      ]);
      expect(allMutations).toHaveLength(1);
      expect(drama.automation.budget).toEqual({
        gpu_minutes: 120,
        wall_minutes: 180,
        max_attempts: 3,
      });
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await mkdir("runtime/autonomous-repair-browser/screenshots", {
        recursive: true,
      });
      await command.screenshot({
        path: `runtime/autonomous-repair-browser/screenshots/paused-${width}-${theme}.png`,
      });
      await attention.screenshot({
        path: `runtime/autonomous-repair-browser/screenshots/stop-${width}-${theme}.png`,
      });
      await command
        .getByRole("button", { name: "继续制作", exact: true })
        .click();
      await expect(
        command.getByRole("button", { name: "暂停制作", exact: true }),
      ).toBeVisible();
      expect(writes.map((body) => body.action)).toEqual([
        "enable_autonomous_repair",
        "resume",
      ]);
      expect(allMutations).toHaveLength(2);
      expect(writes.every((body) => !body.episode_id && !body.automation)).toBe(
        true,
      );
      await expect(
        attention.getByRole("button", { name: "继续此集", exact: true }),
      ).toHaveCount(0);
      await expect(attention).toContainText(
        "核对材料与授权后，再使用顶部入口继续制作。",
      );
    });
  }
