import { test, expect } from "@playwright/test";
import { openProjectPage } from "./navigation.mjs";
const id = "012345abcdef";
const departments = [
  ["production", "制片统筹"],
  ["story", "故事编剧"],
  ["director", "导演分镜"],
  ["visual", "美术与影像"],
  ["sound", "声音制作"],
  ["post", "后期制作"],
  ["quality", "独立质检"],
];
const leads = [
  ["executive_producer", "总制片"],
  ["story_editor", "故事编辑"],
  ["storyboard_director", "分镜导演"],
  ["visual_designer", "视觉设计"],
  ["dialogue_editor", "对白编辑"],
  ["edit_director", "剪辑导演"],
  ["independent_critic", "独立审稿"],
];
const task = {
  id: "video:S01",
  label: "第 1 镜视频",
  kind: "video",
  scope: "S01",
  shot_id: "S01",
  owner: "video_generator",
  department: "visual",
  status: "completed",
  generation: 1,
  attempt: 1,
  input_version: "分镜 v3",
  dependencies: ["image:S01"],
  inputs: [{ label: "制作要求", value: "表情克制，保留构图和服装。" }],
  artifacts: [
    {
      label: "镜头视频候选",
      kind: "video",
      url: `/media/production/factory-${id}/release/v1-aaaaaaaaaaaa/episode.mp4`,
    },
  ],
  review_status: "passed",
  adoption_status: "selected",
  locked: true,
  model: "模拟视频模型",
  attempts: [{ attempt: 1, status: "completed", elapsed_seconds: 12 }],
};
const image = {
  ...task,
  id: "image:S01",
  label: "第 1 镜首帧",
  dependencies: [],
  artifacts: [
    {
      label: "首帧候选",
      url: `/media/production/factory-${id}/frames/frame.png`,
    },
  ],
};

test("team console desktop/mobile themes, filters, evidence and playback survive refresh", async ({
  page,
  request,
}) => {
  await request.post("/__test/reset");
  const calls = [];
  await page.route(`**/api/factory/projects/${id}/team`, async (route) => {
    await route.fulfill({
      json: {
        schema: 1,
        enabled: true,
        control_revision: 1,
        departments: departments.map(([id, label], index) => ({
          id,
          label,
          lead: leads[index][0],
          responsibility: `${label}负责实际任务与成果交接`,
        })),
        members: [
          ...departments.map(([department], index) => ({
            id: leads[index][0],
            role: leads[index][0],
            label: leads[index][1],
            department,
            leader: true,
            status: "idle",
            current_tasks: [],
          })),
          {
            id: "video_generator",
            role: "video_generator",
            label: "视频制作",
            department: "visual",
            status: "completed",
            current_tasks: [task.id],
          },
        ],
        task_summary: {
          total: 2,
          running: 0,
          waiting: 0,
          blocked: 0,
          completed: 2,
        },
        events: [
          {
            id: "e1",
            at: "2026-10-16T04:00:00Z",
            message: "首帧审核通过，已交付视频制作",
            task_id: image.id,
          },
        ],
        instructions: [],
        locks: [],
        actions: [],
      },
    });
  });
  await page.route(`**/api/factory/projects/${id}/tasks**`, async (route) => {
    const url = new URL(route.request().url());
    calls.push(url);
    await route.fulfill({
      json: url.pathname.endsWith("/tasks")
        ? { tasks: [task, image], total: 2, offset: 0, limit: 30 }
        : {
            task: decodeURIComponent(url.pathname).endsWith(image.id)
              ? image
              : task,
            control_revision: 1,
          },
    });
  });
  await page.goto(`/#project=${id}`);
  await page.getByRole("button", { name: "打开团队指挥台" }).click();
  await expect(page.locator(".team-department")).toHaveCount(7);
  const detail = page.getByRole("complementary", { name: "制作任务详情" });
  await expect(
    detail.getByRole("heading", { name: "第 1 镜视频" }),
  ).toBeVisible();
  await expect(detail).toContainText("审核通过");
  await expect(detail).toContainText("已采用");
  await page.getByLabel("部门", { exact: true }).selectOption("visual");
  await page.getByLabel("镜头编号", { exact: true }).fill("S01");
  await expect
    .poll(() =>
      calls.some(
        (url) =>
          url.searchParams.get("department") === "visual" &&
          url.searchParams.get("shot_id") === "S01",
      ),
    )
    .toBeTruthy();
  const video = detail.locator("video");
  await expect(video).toBeVisible();
  await video.evaluate(async (element) => {
    await element.play();
  });
  await expect
    .poll(() => video.evaluate((element) => element.currentTime))
    .toBeGreaterThan(0.2);
  const before = await video.evaluate((element) => element.currentTime);
  await page.locator('.topbar [data-action="refresh"]').click();
  await expect(page.getByLabel("镜头编号", { exact: true })).toHaveValue("S01");
  await expect(
    detail.getByRole("heading", { name: "第 1 镜视频" }),
  ).toBeVisible();
  await expect
    .poll(() => video.evaluate((element) => element.currentTime))
    .toBeGreaterThan(before);
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (value) => (document.documentElement.dataset.theme = value),
      theme,
    );
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({
      path: `runtime/browser-report/team-console-${theme}-desktop.png`,
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator(".sidebar")).toBeHidden();
    await expect(page.locator(".team-department").first()).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBeTruthy();
    await page.screenshot({
      path: `runtime/browser-report/team-console-${theme}-mobile.png`,
      fullPage: true,
    });
  }
  await page
    .getByRole("button", { name: "首帧审核通过，已交付视频制作" })
    .click();
  await expect(
    detail.getByRole("heading", { name: "第 1 镜首帧" }),
  ).toBeVisible();
  await expect(detail.getByRole("img", { name: "首帧候选" })).toBeVisible();
  await page.getByText("工作流与历史执行回执", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "实际调用与回执" }),
  ).toBeVisible();
  await openProjectPage(page, "overview");
  await expect(
    page.getByRole("button", { name: "打开团队指挥台" }),
  ).toBeVisible();
});
