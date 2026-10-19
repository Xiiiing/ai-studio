import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const id = "dd0310310310";
function fixture() {
  const nodes = [
    ["planning", "全剧设定与分集大纲", "succeeded"],
    ["review", "全剧规划独立审核", "succeeded"],
    ["confirmation", "固化审核通过的规划", "succeeded"],
    ["production", "按集制作与团队协作", "blocked"],
    ["acceptance", "逐集验收与成果", "pending"],
  ].map(([id, label, status]) => ({
    id,
    label,
    status,
    owner: "orchestrator",
    members: ["orchestrator"],
    description: label,
  }));
  return {
    id,
    title: "连续制作验收项目",
    record_kind: "drama",
    revision: 8,
    status: "blocked",
    episode_count: 2,
    duration_seconds: 60,
    format: "animation",
    plan_version: 1,
    plans: [],
    batches: [],
    automation: {
      enabled: true,
      episode_numbers: [1, 2],
      budget: { gpu_minutes: 120, wall_minutes: 180, max_attempts: 3 },
    },
    automation_state: {
      status: "blocked",
      stage: "production",
      reason: "第 1 集需要处理",
      episode_numbers: [1, 2],
      attention: [
        {
          code: "episode_blocked",
          message: "素材审核返修额度用尽",
          episode_number: 1,
        },
      ],
    },
    episodes: [
      {
        id: "ee0310310311",
        title: "第 1 集 · 旧信",
        episode_number: 1,
        duration_seconds: 60,
        status: "blocked",
        progress: 40,
        error: "素材审核返修额度用尽",
        outline: { synopsis: "成年人在车站发现旧信。" },
      },
      {
        id: "ee0310310312",
        title: "第 2 集 · 重逢",
        episode_number: 2,
        duration_seconds: 60,
        status: "awaiting_final",
        progress: 100,
        delivery: {
          video_url:
            "/media/production/factory-012345abcdef/release/v1-aaaaaaaaaaaa/episode.mp4",
        },
        outline: { synopsis: "多年误会终于解开。" },
      },
    ],
    workflow: {
      graph: {
        project_id: id,
        revision: 8,
        active_node: "production",
        nodes,
        edges: [],
      },
      flow: { project_id: id, revision: 8, steps: [] },
      stages: [],
      tasks: [
        {
          id: "t-1",
          title: "画面审核退回",
          role: "visual_critic",
          status: "blocked",
          stage_id: "production",
          output: { summary: "角色服饰需要保持一致" },
        },
      ],
    },
  };
}
for (const width of [1982, 1440, 390]) {
  test(`continuous production keeps attention and artifacts inline at ${width}`, async ({
    page,
    request,
  }) => {
    await request.post("/__test/reset");
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({
      colorScheme: "dark",
      reducedMotion: width === 390 ? "reduce" : "no-preference",
    });
    const project = fixture(),
      calls = [];
    await page.route(`**/api/factory/projects/${id}/workspace`, (route) =>
      route.fulfill({
        json: {
          project: { id, record_kind: "drama", title: project.title },
          revision: project.revision,
          operator: {},
          workflow: {},
        },
      }),
    );
    await page.route("**/api/factory/dramas/**", async (route) => {
      if (route.request().method() === "POST") {
        calls.push(route.request().postDataJSON());
        project.revision++;
      }
      await route.fulfill({ json: project });
    });
    await page.route(
      "**/api/factory/projects/ee0310310311/workspace",
      (route) =>
        route.fulfill({
          json: {
            project: {
              script: { synopsis: "已保存的单集剧本" },
              shots: [
                {
                  id: "S01",
                  dramatic_beat: "打开旧信",
                  image_url:
                    "/media/production/factory-012345abcdef/frames/frame.png",
                },
              ],
            },
          },
        }),
    );
    await page.goto(`/#project=${id}&tab=overview`);
    await expect(
      page.getByRole("heading", { name: project.title }),
    ).toBeVisible();
    const nav = page.getByRole("navigation", { name: "整剧工作区" });
    await expect(
      nav.getByRole("button", { name: "制作", exact: true }),
    ).toHaveAttribute("aria-current", "page");
    await expect(
      nav.getByRole("button", { name: "素材", exact: true }),
    ).toBeVisible();
    const attention = page.locator(".production-attention");
    await attention
      .getByRole("button", { name: "继续此集", exact: true })
      .click();
    await expect.poll(() => calls[0]?.action).toBe("retry");
    expect(calls[0].episode_id).toBe("ee0310310311");
    await page
      .getByRole("button", { name: "查看当前产物", exact: true })
      .first()
      .click();
    await expect(
      page.getByText("已保存的单集剧本", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "分镜与素材", exact: true }).click();
    await expect(
      page.getByRole("img", { name: "分镜 S01", exact: true }),
    ).toBeVisible();
    expect(page.url()).toContain("tab=overview");
    const reviewTrigger = attention.getByRole("button", {
      name: "观看与验收第 2 集",
      exact: true,
    });
    await reviewTrigger.click();
    await expect(page.getByRole("dialog", { name: /终片验收/ })).toBeVisible();
    expect(calls).toHaveLength(1);
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "关闭", exact: true })
      .click();
    await expect(reviewTrigger).toBeFocused();
    const skipBounds = await page.locator(".skip-link").boundingBox();
    expect(skipBounds.y + skipBounds.height).toBeLessThanOrEqual(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    if (width === 390) {
      const boxes = await Promise.all([
        page.locator(".production-artifacts").boundingBox(),
        page.locator(".production-activity").boundingBox(),
      ]);
      expect(boxes[1].y).toBeGreaterThanOrEqual(boxes[0].y + boxes[0].height);
    }
    await mkdir("runtime/browser-report/production-031", { recursive: true });
    await page.screenshot({
      path: `runtime/browser-report/production-031/viewport-${width}.png`,
      fullPage: false,
    });
    // Full-page capture can paint offscreen fixed elements at the current page
    // scroll offset. Capture from document start so the hidden skip link remains
    // outside the full-page image too; focus stays on the review trigger.
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    await page.screenshot({
      path: `runtime/browser-report/production-031/workspace-${width}.png`,
      fullPage: true,
    });
  });
}
