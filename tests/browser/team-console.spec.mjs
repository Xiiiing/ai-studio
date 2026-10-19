import { selectOption, nativeControl } from "./select-control.mjs";
import { refreshPage } from "./header-flow.mjs";
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
  await page
    .locator("summary")
    .filter({ hasText: /^制作规格与团队概况$/ })
    .click();
  await page.getByRole("button", { name: "打开团队指挥台" }).click();
  await expect(page.locator(".team-department")).toHaveCount(7);
  const detail = page.getByRole("complementary", { name: "制作任务详情" });
  await expect(
    detail.getByRole("heading", { name: "第 1 镜视频" }),
  ).toBeVisible();
  await expect(detail).toContainText("审核通过");
  await expect(detail).toContainText("已采用");
  await selectOption(page.getByLabel("部门", { exact: true }), "visual");
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
  await refreshPage(page);
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
  await page
    .locator("summary")
    .filter({ hasText: /^制作规格与团队概况$/ })
    .click();
  await expect(
    page.getByRole("button", { name: "打开团队指挥台" }),
  ).toBeVisible();
});

test("task controls preserve draft, preview lock conflicts, recover baseline and retain media", async ({
  page,
  request,
}) => {
  await request.post("/__test/reset");
  let control = 1,
    generation = 1,
    locked = false,
    rejectOnce = true;
  const submissions = [],
    quickActions = [],
    instructions = [];
  const post = {
    ...task,
    id: "edit:project",
    label: "后期交付",
    kind: "edit",
    owner: "editor",
    department: "post",
    artifacts: [],
    dependencies: [task.id],
  };
  const controlledTask = () => ({
    ...task,
    generation,
    locked,
    actions: [
      {
        id: locked ? "unlock" : "lock",
        label: locked ? "解锁成果" : "锁定成果",
        enabled: true,
      },
      { id: "hold_after", label: "完成后等我确认", enabled: true },
      { id: "intervene", label: "介入并下达要求", enabled: true },
    ],
  });
  await page.route(`**/api/factory/projects/${id}/team`, (route) =>
    route.fulfill({
      json: {
        schema: 1,
        enabled: true,
        control_revision: control,
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
        events: [],
        instructions,
        locks: [],
        actions: [{ id: "intervene", enabled: true }],
      },
    }),
  );
  await page.route(`**/api/factory/projects/${id}/tasks**`, (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/actions")) {
      const body = route.request().postDataJSON();
      quickActions.push(body);
      if (body.action === "lock") locked = true;
      if (body.action === "unlock") locked = false;
      return route.fulfill({
        json: { task: controlledTask(), control_revision: ++control },
      });
    }
    return route.fulfill({
      json: url.pathname.endsWith("/tasks")
        ? {
            tasks: [controlledTask(), image],
            total: 2,
            control_revision: control,
          }
        : {
            task: decodeURIComponent(url.pathname).endsWith(image.id)
              ? image
              : decodeURIComponent(url.pathname).endsWith(post.id)
                ? post
                : controlledTask(),
            control_revision: control,
          },
    });
  });
  await page.route(`**/api/factory/projects/${id}/interventions**`, (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({
        json: {
          instructions,
          total: instructions.length,
          offset: 0,
          limit: 20,
        },
      });
    const body = route.request().postDataJSON();
    if (route.request().url().endsWith("/preview"))
      return route.fulfill({
        json: {
          affected_tasks: [task.id, "edit:project"],
          preserved_tasks: body.preserve,
          conflicts: locked
            ? [{ task_id: task.id, reason: "视频成果已锁定，请解锁后重试" }]
            : [],
        },
      });
    submissions.push(body);
    if (rejectOnce) {
      rejectOnce = false;
      generation++;
      control++;
      return route.fulfill({
        status: 409,
        json: { error: "任务版本已变化，请重新读取基线" },
      });
    }
    const instruction = {
      ...body,
      id: "instruction-1",
      status: "pending",
      affected_tasks: [task.id, "edit:project"],
    };
    instructions.push(instruction);
    return route.fulfill({
      json: { instruction, control_revision: ++control },
    });
  });
  await page.goto(`/#project=${id}`);
  await page
    .locator("summary")
    .filter({ hasText: /^制作规格与团队概况$/ })
    .click();
  await page.getByRole("button", { name: "打开团队指挥台" }).click();
  const detail = page.getByRole("complementary", { name: "制作任务详情" });
  const video = detail.locator("video");
  await video.evaluate(async (element) => {
    await element.play();
  });
  await expect
    .poll(() => video.evaluate((element) => element.currentTime))
    .toBeGreaterThan(0.2);
  const before = await video.evaluate((element) => element.currentTime);
  await detail.getByRole("button", { name: "锁定成果" }).click();
  await expect(detail.getByRole("button", { name: "解锁成果" })).toBeVisible();
  await expect
    .poll(() => video.evaluate((element) => element.currentTime))
    .toBeGreaterThan(before);
  await detail.getByRole("button", { name: "介入并下达要求" }).click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("新的制作要求", { exact: true })
    .fill("只调整人物表情，保留构图和服装");
  await dialog.getByRole("button", { name: "查看影响", exact: true }).click();
  await expect(dialog).toContainText("视频成果已锁定，请解锁后重试");
  await expect(dialog.getByLabel("新的制作要求", { exact: true })).toHaveValue(
    "只调整人物表情，保留构图和服装",
  );
  await dialog.getByRole("button", { name: "稍后继续编辑" }).click();
  await detail.getByRole("button", { name: "解锁成果" }).click();
  await openProjectPage(page, "overview");
  await page
    .locator("summary")
    .filter({ hasText: /^制作规格与团队概况$/ })
    .click();
  await page.getByRole("button", { name: "打开团队指挥台" }).click();
  await detail.getByRole("button", { name: "介入并下达要求" }).click();
  await expect(dialog.getByLabel("新的制作要求", { exact: true })).toHaveValue(
    "只调整人物表情，保留构图和服装",
  );
  await dialog
    .getByRole("group", { name: "保留现有成果" })
    .getByRole("checkbox", { name: /第 1 镜首帧/ })
    .check();
  await selectOption(
    dialog.getByLabel("完成后的安排", { exact: true }),
    "wait",
  );
  await dialog.getByRole("button", { name: "查看影响", exact: true }).click();
  await expect(dialog.getByLabel("返工影响预览")).toContainText("后期交付");
  await page.screenshot({
    path: "runtime/browser-report/team-controls-light-desktop.png",
    fullPage: false,
  });
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".sidebar")).toBeHidden();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "runtime/browser-report/team-controls-dark-mobile.png",
    fullPage: false,
  });
  await dialog.getByLabel("返工影响预览").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "runtime/browser-report/team-controls-dark-mobile-impact.png",
    fullPage: false,
  });
  await dialog.getByRole("button", { name: "登记指令并执行" }).click();
  await expect(dialog).toContainText("任务版本已变化，请重新读取基线");
  await expect(dialog.getByLabel("新的制作要求", { exact: true })).toHaveValue(
    "只调整人物表情，保留构图和服装",
  );
  await dialog
    .getByRole("button", { name: "重新读取基线（保留要求）" })
    .click();
  await expect(
    dialog.getByRole("button", { name: "登记指令并执行" }),
  ).toBeEnabled();
  await dialog.getByRole("button", { name: "登记指令并执行" }).click();
  await expect(
    page.getByText("指令已登记，当前调用收尾后生效。", { exact: true }),
  ).toBeVisible();
  expect(submissions).toHaveLength(2);
  expect(submissions[0].task_generations).toEqual({ [task.id]: 1 });
  expect(submissions[1].task_generations).toEqual({ [task.id]: 2 });
  expect(submissions[1].preserve).toEqual([image.id]);
  expect(submissions[1].after).toBe("wait");
  expect(quickActions.map((action) => action.action)).toEqual([
    "lock",
    "unlock",
  ]);
});

test("leader scopes, actionable plans, disabled models and management evidence", async ({
  page,
  request,
}) => {
  await request.post("/__test/reset");
  const requests = [],
    instructions = [];
  const sound = {
    ...task,
    id: "speech:S01:D1",
    label: "第 1 镜配音",
    owner: "speech",
    department: "sound",
    kind: "speech",
    artifacts: [],
    dependencies: [],
  };
  const edit = {
    ...task,
    id: "edit:project",
    label: "后期交付",
    owner: "editor",
    department: "post",
    kind: "edit",
    artifacts: [],
    dependencies: [task.id, sound.id],
  };
  const reportTask = {
    ...task,
    id: "management:total",
    label: "总制片安排回执",
    owner: "executive_producer",
    department: "production",
    kind: "management",
    model: "模拟管理模型",
    artifacts: [],
    inputs: [
      { label: "结构化安排", value: "限定在已选择的任务内调整优先级。" },
    ],
    dependencies: [],
  };
  const candidates = [
    {
      ...task,
      locked: false,
      allowed_owners: ["video_generator"],
      default_owner: "video_generator",
    },
    sound,
    edit,
  ];
  let control = 1;
  await page.route(`**/api/factory/projects/${id}/team`, (route) =>
    route.fulfill({
      json: {
        schema: 1,
        enabled: true,
        control_revision: control,
        departments: departments.map(([id, label], index) => ({
          id,
          label,
          lead: leads[index][0],
          responsibility: `${label}负责实际任务与成果交接`,
          management_available: id !== "sound",
          management_unavailable_reason:
            id === "sound" ? "对白编辑尚未配置管理模型" : undefined,
        })),
        members: [
          ...departments.map(([department], index) => ({
            id: leads[index][0],
            role: leads[index][0],
            label: leads[index][1],
            department,
            leader: true,
            leader_available: department !== "sound",
            leader_unavailable_reason:
              department === "sound" ? "对白编辑尚未配置管理模型" : undefined,
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
          total: 3,
          running: 0,
          waiting: 0,
          blocked: 0,
          completed: 3,
        },
        events: [],
        instructions,
        locks: [],
        actions: [
          { id: "intervene", enabled: true },
          { id: "replan", enabled: true },
        ],
      },
    }),
  );
  await page.route(`**/api/factory/projects/${id}/tasks**`, (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    if (url.pathname.endsWith("/tasks")) {
      const scoped = url.searchParams.get("department")
        ? candidates.filter(
            (candidate) =>
              candidate.department === url.searchParams.get("department"),
          )
        : candidates;
      return route.fulfill({
        json: {
          tasks: scoped,
          total: scoped.length,
          control_revision: control,
        },
      });
    }
    const selected = decodeURIComponent(url.pathname.split("/").at(-1));
    return route.fulfill({
      json: {
        task:
          selected === reportTask.id
            ? reportTask
            : candidates.find((candidate) => candidate.id === selected),
        control_revision: control,
      },
    });
  });
  await page.route(`**/api/factory/projects/${id}/interventions**`, (route) => {
    if (route.request().method() === "GET")
      return route.fulfill({
        json: {
          instructions,
          total: instructions.length,
          offset: 0,
          limit: 20,
        },
      });
    const body = route.request().postDataJSON();
    if (route.request().url().endsWith("/preview"))
      return route.fulfill({
        json: {
          affected_tasks: body.task_ids,
          preserved_tasks: body.preserve,
          conflicts: [],
          control_revision: control,
        },
      });
    const instruction = {
      ...body,
      id: `leader-${instructions.length + 1}`,
      status: "applied",
      affected_tasks: body.task_ids,
      plan: {
        management_tasks: [reportTask.id],
        summary:
          body.target_id === "executive_producer"
            ? "先完成第 1 镜，再交接后期制作"
            : "优先完成本部门的第 1 镜",
        operations: body.task_ids.map((id, index) => ({
          action: "prioritize",
          task_id: id,
          priority: 10 - index,
        })),
        delegations:
          body.target_id === "executive_producer"
            ? [
                {
                  department: "post",
                  task_ids: [edit.id],
                  summary: "收到镜头后开始后期交付",
                },
              ]
            : [],
      },
    };
    instructions.unshift(instruction);
    return route.fulfill({
      json: {
        instruction: { ...instruction, status: "queued" },
        control_revision: ++control,
      },
    });
  });
  await page.goto(`/#project=${id}`);
  await page
    .locator("summary")
    .filter({ hasText: /^制作规格与团队概况$/ })
    .click();
  await page.getByRole("button", { name: "打开团队指挥台" }).click();
  const disabled = page.getByRole("button", { name: "请声音制作负责人安排" });
  await expect(disabled).toBeDisabled();
  await disabled.locator("..").getByText("查看不可用原因").click();
  await expect(disabled.locator("..")).toContainText(
    "对白编辑尚未配置管理模型",
  );
  await page.getByRole("button", { name: "向总制片下达要求" }).click();
  const dialog = page.getByRole("dialog");
  await expect(
    nativeControl(dialog.getByLabel("处理方式", { exact: true })),
  ).toHaveValue("replan");
  await dialog
    .getByRole("group", { name: "修改范围" })
    .getByRole("checkbox", { name: /第 1 镜视频/ })
    .check();
  await dialog
    .getByRole("group", { name: "修改范围" })
    .getByRole("checkbox", { name: /后期交付/ })
    .check();
  await dialog
    .getByLabel("新的制作要求", { exact: true })
    .fill("优先完成关键镜头，再协调后期交付顺序");
  await dialog.getByRole("button", { name: "查看影响", exact: true }).click();
  await expect(dialog.getByLabel("返工影响预览")).toContainText("后期交付");
  await page.screenshot({
    path: "runtime/browser-report/team-leadership-light-desktop.png",
  });
  await dialog.getByRole("button", { name: "登记指令并执行" }).click();
  const history = page.getByLabel("已登记的制作指令");
  await expect(history).toContainText("先完成第 1 镜，再交接后期制作");
  await expect(history).toContainText("已应用的安排");
  await expect(history).toContainText("后期交付");
  await history.screenshot({
    path: "runtime/browser-report/team-leadership-plan-light-desktop.png",
  });
  expect(instructions[0].target_type).toBe("leader");
  expect(instructions[0].target_id).toBe("executive_producer");
  expect(instructions[0].task_ids).toEqual([task.id, edit.id]);
  await page.getByRole("button", { name: "请美术与影像负责人安排" }).click();
  const scope = dialog.getByRole("group", { name: "修改范围" });
  await expect(scope.getByRole("checkbox")).toHaveCount(1);
  await scope.getByRole("checkbox", { name: /第 1 镜视频/ }).check();
  await dialog
    .getByLabel("新的制作要求", { exact: true })
    .fill("请在本部门优先处理这一镜");
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".sidebar")).toBeHidden();
  await page.screenshot({
    path: "runtime/browser-report/team-leadership-dark-mobile.png",
  });
  await dialog.getByRole("button", { name: "登记指令并执行" }).click();
  await expect.poll(() => instructions[0].target_id).toBe("visual_designer");
  expect(instructions[0].operation).toBe("replan");
  expect(instructions[0].task_ids).toEqual([task.id]);
  expect(
    requests.some(
      (url) =>
        url.searchParams.get("department") === "visual" &&
        url.searchParams.get("limit") === "100",
    ),
  ).toBe(true);
  await history
    .getByRole("button", {
      name: /查看管理任务：(?:management:total|总制片安排回执)/,
    })
    .first()
    .click();
  const detail = page.getByRole("complementary", { name: "制作任务详情" });
  await expect(
    detail.getByRole("heading", { name: "总制片安排回执" }),
  ).toBeVisible();
  await expect(detail).toContainText("模拟管理模型");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBeTruthy();
});
