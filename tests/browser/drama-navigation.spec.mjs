import {
  expandRole,
  openDetails,
  closeDetails,
  canvasState,
  pointerEdge,
  assertUnobstructedLines,
} from "./fixed-canvas.mjs";
import { test, expect } from "@playwright/test";
import { lifecycleFixture } from "./lifecycle-fixture.mjs";

const dramaId = "dd1122334455";

function blockedDrama() {
  return {
    id: dramaId,
    record_kind: "drama",
    title: "整剧页签回归 · 未确认规划",
    revision: 8,
    episode_count: 3,
    duration_seconds: 300,
    format: "animation",
    status: "blocked",
    stage: "plan_repair",
    plan_version: 0,
    plans: [],
    episodes: [],
    batches: [],
    error: "全剧规划未通过独立审核，已保留问题与草稿",
    workflow: {
      stages: [
        {
          id: "planning",
          title: "全剧规划",
          role: "screenwriter",
          status: "blocked",
          input: "已导入剧本",
          output: "草稿保留，待处理规划审核问题",
        },
      ],
      tasks: [
        {
          id: "outline-1",
          title: "分集大纲",
          role: "screenwriter",
          status: "completed",
          input: "固定全剧设定与人物关系，编写三集连续大纲。",
          output: "已生成三集大纲，并交由独立审稿检查。",
        },
        {
          id: "critic-1",
          title: "全剧规划审核",
          role: "independent_critic",
          status: "blocked",
          input: "已生成三集大纲，并交由独立审稿检查。",
          received: [
            {
              source_task_id: "outline-1",
              source_role: "screenwriter",
              label: "分集大纲",
              input_field: "episodes",
              value: "已生成三集大纲，并交由独立审稿检查。",
              basis: "recorded_input_content_match",
            },
          ],
          output: "未通过，等待修复跨集连续性。",
          issues: ["第2集开场与第1集结尾的角色状态冲突。"],
        },
      ],
    },
    plan_review: {
      passed: false,
      issues: ["第2集开场与第1集结尾的角色状态冲突。"],
    },
    draft_plan: {
      world: "车站与旧城。",
      storyline: "成年旧友沿着旧信寻找真相，最终在车站和解。",
      visual_rules: "统一水彩画风",
      relationships: ["林与陈是多年前的旧友。"],
      characters: [{ character_id: "lead", name: "林", voice_id: "female" }],
      scenes: [],
      props: [],
      uncertainties: ["旧信的寄出时间待确认。"],
      episodes: [1, 2, 3].map((number) => ({
        number,
        title: `旧信${number}`,
        synopsis: `第${number}集沿着旧信寻找真相。`,
      })),
    },
  };
}

async function installDrama(page, drama, calls = []) {
  drama.workflow = lifecycleFixture(drama.workflow, drama.id);
  await page.route(`**/api/factory/projects/${dramaId}/workspace`, (route) =>
    route.fulfill({
      json: {
        project: { id: dramaId, title: drama.title, record_kind: "drama" },
        revision: drama.revision,
        operator: {},
        workflow: {},
        flow: {},
      },
    }),
  );
  await page.route(`**/api/factory/dramas/${dramaId}**`, async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      calls.push(body);
      drama.revision++;
      if (route.request().url().endsWith("/generate")) {
        drama.episodes.forEach((episode) => {
          if (body.episode_numbers.includes(episode.episode_number))
            episode.status = "queued";
        });
        drama.batches = [
          {
            id: "bb1122334455",
            episode_numbers: body.episode_numbers,
            plan_version: 1,
            members: drama.episodes
              .filter((episode) => episode.status === "queued")
              .map((episode) => ({
                number: episode.episode_number,
                status: episode.status,
              })),
          },
        ];
      }
    }
    await route.fulfill({ json: drama });
  });
}

async function noHorizontalOverflow(page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
}

for (const width of [1440, 390]) {
  test(`unconfirmed blocked drama tabs are distinct and keep routes at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const drama = blockedDrama();
    await installDrama(page, drama);
    await page.goto("/#project=" + dramaId);
    const nav = page.getByRole("navigation", { name: "整剧工作区" });
    await expect(
      nav.getByRole("button", { name: "项目总览", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(
      page.locator(".drama-overview-issues > summary"),
    ).toContainText("规划审核问题");
    await page.locator(".drama-overview-issues > summary").click();
    await expect(page.getByText(drama.error, { exact: true })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "查看全剧规划", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "打开制作流程", exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel("全剧主线", { exact: true })).toHaveCount(0);
    await noHorizontalOverflow(page);

    for (const [tab, label, heading] of [
      ["episodes", "剧集", "剧集尚未建立"],
      ["plan", "全剧规划", "全剧规划"],
      ["assets", "共享设定", "全剧共享设定"],
      ["batches", "生成批次", "尚无生成批次"],
      ["results", "成果", "暂无剧集成果"],
    ]) {
      if (["plan", "assets", "batches"].includes(tab))
        await nav
          .getByRole("button", { name: "项目资料", exact: true })
          .click();
      await (
        ["plan", "assets", "batches"].includes(tab)
          ? page.getByRole("menuitem", { name: label, exact: true })
          : nav.getByRole("button", { name: label, exact: true })
      ).click();
      await expect(page).toHaveURL(new RegExp("tab=" + tab));
      await expect(
        page.getByRole("heading", { name: heading, exact: true }),
      ).toBeVisible();
      if (["plan", "assets", "batches"].includes(tab))
        await expect(page.locator(".drama-location")).toHaveText(label);
      else
        await expect(
          nav.getByRole("button", { name: label, exact: true }),
        ).toHaveAttribute("aria-pressed", "true");
      if (tab === "plan") {
        await expect(page.getByLabel("全剧主线", { exact: true })).toHaveValue(
          drama.draft_plan.storyline,
        );
        await expect(
          page.getByRole("button", { name: "继续规划", exact: true }),
        ).toBeVisible();
      } else {
        await expect(page.getByLabel("全剧主线", { exact: true })).toHaveCount(
          0,
        );
        await expect(
          page.getByRole("button", { name: "确认全剧规划", exact: true }),
        ).toHaveCount(0);
        await expect(
          page.getByRole("button", { name: "生成所选 0 集", exact: true }),
        ).toHaveCount(0);
      }
      await noHorizontalOverflow(page);
    }

    await page.reload();
    await expect(
      nav.getByRole("button", { name: "成果", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByLabel("全剧主线", { exact: true })).toHaveCount(0);
    await page.goBack();
    await expect(page).toHaveURL(new RegExp("tab=batches"));
    await expect(page.locator(".drama-location")).toHaveText("生成批次");
    await expect(
      nav.getByRole("button", { name: "剧集", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await nav.getByRole("button", { name: "项目总览", exact: true }).click();
    await page
      .getByRole("button", { name: "查看全剧规划", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp("tab=plan"));
    await expect(page.getByLabel("全剧主线", { exact: true })).toBeVisible();
  });

  test(`planning confirmation waits for the active job at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const drama = blockedDrama();
    drama.workflow.active_job = true;
    const calls = [];
    await installDrama(page, drama, calls);
    await page.goto("/#project=" + dramaId + "&tab=plan");
    await expect(
      page.getByRole("status").filter({ hasText: "规划任务正在执行" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "提交修改并重新审核", exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "继续规划", exact: true }),
    ).toBeDisabled();
    expect(calls).toEqual([]);
    await noHorizontalOverflow(page);
  });

  test(`drama workflow exposes separate team inputs and results at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const drama = blockedDrama();
    await installDrama(page, drama);
    await page.goto("/#project=" + dramaId);
    await page
      .getByRole("button", { name: "打开制作流程", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp("tab=workflow"));
    await expect(
      page.getByRole("heading", { name: "制作工作流", exact: true }),
    ).toBeVisible();
    await page.locator('[data-stage="planning"]').click();
    await openDetails(page);
    await page.getByRole("button", { name: "执行记录", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "执行记录", exact: true }),
    ).toBeVisible();
    const writer = page.locator("details").filter({
      has: page.locator("summary", { hasText: "分集大纲 · 编剧 ·" }),
    });
    const critic = page.locator("details").filter({
      has: page.locator("summary", {
        hasText: "全剧规划审核 · 独立审稿 · 需要处理",
      }),
    });
    await expect(writer).toHaveCount(1);
    await expect(critic).toHaveCount(0);
    await expect(
      writer.getByText(drama.workflow.tasks[0].input, { exact: true }),
    ).not.toBeVisible();
    await writer.locator("summary").click();
    await expect(
      writer.locator("dt").filter({ hasText: /^输入$/ }),
    ).toBeVisible();
    await expect(
      writer.locator("dt").filter({ hasText: /^输出$/ }),
    ).toBeVisible();
    await expect(
      writer.getByText(drama.workflow.tasks[0].input, { exact: true }),
    ).toBeVisible();
    await expect(
      writer.getByText(drama.workflow.tasks[0].output, { exact: true }),
    ).toBeVisible();
    await closeDetails(page);
    await page.locator('[data-stage="review"]').click();
    await openDetails(page);
    await page.getByRole("button", { name: "执行记录", exact: true }).click();
    await expect(writer).toHaveCount(0);
    await critic.locator("summary").click();
    await expect(
      critic.getByText(drama.workflow.tasks[1].output, { exact: true }),
    ).toBeVisible();
    await expect(critic.getByRole("note")).toContainText(
      drama.plan_review.issues[0],
    );
    await expect(page.getByLabel("全剧主线", { exact: true })).toHaveCount(0);
    await noHorizontalOverflow(page);
    await page.screenshot({
      path: `runtime/browser-report/drama-workflow-${width}.png`,
      fullPage: true,
    });
    await page.reload();
    await expect(
      page
        .getByRole("navigation", { name: "整剧工作区" })
        .getByRole("button", { name: "制作流程与团队", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  test(`whole drama opens episode workflow and unit team at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const episodeId = "ee1122334455",
      unitId = "012345abcdef";
    const episode = {
      id: episodeId,
      record_kind: "episode",
      drama_id: dramaId,
      title: "第1集 · 旧信与车站",
      duration_seconds: 300,
      revision: 12,
      drama_revision: 8,
      status: "running",
      progress: 50,
      plan_version: 1,
      outline: { synopsis: "沿着旧信寻找多年以前的约定。" },
      workflow: {
        stages: [
          {
            id: "units",
            title: "制作单元",
            role: "orchestrator",
            status: "running",
            output: "第1个单元已开始制作。",
          },
        ],
        tasks: [
          {
            id: "unit:" + unitId,
            project_id: unitId,
            title: "车站相遇",
            role: "orchestrator",
            status: "completed",
            input: "单集大纲",
            output: "车站场景与镜头方案",
          },
        ],
      },
      units: [
        {
          id: unitId,
          title: "E001-C01-U001 · 车站相遇",
          status: "running",
          progress: 50,
          duration_seconds: 75,
        },
      ],
    };
    const drama = {
      ...blockedDrama(),
      status: "ready",
      plan_version: 1,
      error: "",
      plan_review: { passed: true },
      episodes: [{ ...episode, episode_number: 1 }],
    };
    episode.workflow = lifecycleFixture(episode.workflow, episode.id, true);
    await installDrama(page, drama);
    await page.route(
      `**/api/factory/projects/${episodeId}/workspace`,
      (route) =>
        route.fulfill({
          json: {
            project: episode,
            revision: episode.revision,
            workflow: {},
            operator: {},
            flow: {},
          },
        }),
    );
    await page.route(
      `**/api/factory/dramas/${dramaId}/episodes/${episodeId}`,
      (route) => route.fulfill({ json: episode }),
    );
    await page.route(`**/api/factory/projects/${unitId}/team`, (route) =>
      route.fulfill({
        json: {
          enabled: false,
          departments: [],
          members: [],
          events: [],
          task_summary: {},
        },
      }),
    );
    await page.route(`**/api/factory/projects/${unitId}/tasks?**`, (route) =>
      route.fulfill({ json: { tasks: [], total: 0 } }),
    );
    await page.goto("/#project=" + dramaId);
    await page
      .getByRole("button", { name: "查看剧集流程", exact: true })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`project=${episodeId}&tab=workflow`),
    );
    await expect(
      page.getByRole("heading", { name: episode.title, exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "制作工作流", exact: true }),
    ).toBeVisible();
    await expect(
      page
        .getByRole("navigation", { name: "剧集工作区" })
        .getByRole("button", { name: "制作流程与团队", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await noHorizontalOverflow(page);
    await (await expandRole(page, "orchestrator")).click();
    await openDetails(page);
    const collaboration = page.locator(".workflow-fixed-workspace");
    await collaboration.locator(".collaboration-work-record > summary").click();
    await collaboration
      .getByRole("button", { name: "打开此任务的工作流", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`project=${unitId}&tab=workflow`));
    await expect(
      page.getByRole("heading", { name: "制作工作流", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "项目协作画布", exact: true }),
    ).toBeVisible();
    await noHorizontalOverflow(page);
    expect(errors).toEqual([]);
  });

  test(`confirmed drama preserves episode selection and batch controls at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const drama = {
      ...blockedDrama(),
      status: "ready",
      plan_version: 1,
      error: "",
      plan_review: { passed: true },
      episodes: [1, 2, 3].map((number) => ({
        id: `ee112233445${number}`,
        episode_number: number,
        title: `第${number}集 · 旧信`,
        status: "draft",
        progress: 0,
        plan_version: 1,
        outline: { synopsis: "成年旧友在车站沿着信件寻找真相。" },
      })),
    };
    const calls = [];
    await installDrama(page, drama, calls);
    await page.goto(`/#project=${dramaId}&tab=episodes`);
    await page.getByRole("checkbox", { name: "选择第1集" }).check();
    await page.getByRole("checkbox", { name: "选择第3集" }).check();
    await page
      .getByRole("button", { name: "生成所选 2 集", exact: true })
      .click();
    await expect.poll(() => calls.length).toBe(1);
    expect(calls[0]).toMatchObject({
      episode_numbers: [1, 3],
      expected_revision: 8,
    });
    await page
      .getByRole("navigation", { name: "整剧工作区" })
      .getByRole("button", { name: "项目资料", exact: true })
      .click();
    await page.getByRole("menuitem", { name: "生成批次", exact: true }).click();
    await expect(
      page.getByText("第1、3集 · 设定v1", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "暂停批次", exact: true }).click();
    await expect.poll(() => calls.length).toBe(2);
    expect(calls[1]).toMatchObject({
      action: "pause",
      batch_id: "bb1122334455",
    });
    await noHorizontalOverflow(page);
  });
}

for (const width of [1440, 390])
  test(`project list opens a blocked project diagram at ${width}`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const drama = blockedDrama();
    const consoleData = await (
      await request.get("/api/factory/console")
    ).json();
    await page.route("**/api/factory/console**", (r) =>
      r.fulfill({
        json: { ...consoleData, projects: [drama], running_tasks: [] },
      }),
    );
    await installDrama(page, drama);
    await page.goto("/#view=projects");
    await page
      .locator(".console-project-table")
      .getByText(drama.title, { exact: true })
      .click();
    await page
      .getByRole("navigation", { name: "整剧工作区" })
      .getByRole("button", { name: "制作流程与团队", exact: true })
      .click();
    await expect(page).toHaveURL(
      new RegExp("project=" + dramaId + "&tab=workflow"),
    );
    await expect(page.locator("[data-stage]")).toHaveCount(5);
    await expect(page.locator("[data-flow-edge]")).not.toHaveCount(0);
    const sizes = await page
      .locator(".workflow-workspace .badge")
      .evaluateAll((nodes) =>
        nodes
          .filter((n) => n.getClientRects().length)
          .map((n) => n.getBoundingClientRect().height),
      );
    expect(sizes.length).toBeGreaterThan(0);
    for (const height of sizes) expect(height).toBeLessThan(40);
    await noHorizontalOverflow(page);
  });

for (const width of [1440, 390, 320]) {
  test(`compact navigation and progressive node inspection at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await installDrama(page, blockedDrama());
    const writes = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/") && r.method() === "POST")
        writes.push(r.url());
    });
    await page.goto("/#project=" + dramaId + "&tab=workflow");
    for (const theme of ["light", "dark"]) {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      const nav = page.getByRole("navigation", { name: "整剧工作区" });
      await expect(
        nav.locator(".drama-primary-tabs").getByRole("button"),
      ).toHaveCount(4);
      await expect(
        nav.getByRole("button", { name: "项目资料", exact: true }),
      ).toBeVisible();
      await expect(page.locator(".workflow-inspector")).toBeVisible();
      await expect(
        page.getByRole("heading", { name: "审核与自动返修" }),
      ).toHaveCount(0);
      await expect(page.locator("[data-stage]")).toHaveCount(5);
      await assertUnobstructedLines(page);
      await noHorizontalOverflow(page);
      await page.locator("#workspace h1").click();
      await page.screenshot({
        path: `runtime/browser-report/compact-flow-${theme}-${width}.png`,
        fullPage: true,
      });
      await page.getByRole("button", { name: "当前步骤", exact: true }).click();
      await openDetails(page);
      const inspector = page.getByRole("complementary", { name: "节点详情" });
      await expect(inspector).toBeVisible();
      await expect(
        inspector.getByRole("heading", {
          name: "全剧规划独立审核",
          exact: true,
        }),
      ).toBeVisible();
      await expect(
        inspector.getByRole("region", { name: "步骤执行记录" }),
      ).toBeHidden();
      await inspector
        .getByRole("button", { name: "执行记录", exact: true })
        .click();
      await expect(
        inspector.getByRole("region", { name: "步骤执行记录" }),
      ).toBeVisible();
      await inspector.locator(".drama-task summary").first().click();
      await page.screenshot({
        path: `runtime/browser-report/compact-node-${theme}-${width}.png`,
        fullPage: true,
      });
      await expect(
        inspector.getByRole("region", { name: "输入材料" }),
      ).toBeHidden();
      await inspector
        .getByRole("button", { name: "材料与职责", exact: true })
        .click();
      await expect(
        inspector.getByRole("region", { name: "输入材料" }),
      ).toBeVisible();
      const beforeClose = await canvasState(page);
      await closeDetails(page);
      await expect(inspector).toHaveCount(0);
      expect(await canvasState(page)).toEqual(beforeClose);
      await nav.getByRole("button", { name: "项目资料", exact: true }).click();
      await expect(
        page.getByRole("menuitem", { name: "全剧规划", exact: true }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(
        page.getByRole("menuitem", { name: "全剧规划", exact: true }),
      ).toBeHidden();
    }
    expect(writes).toEqual([]);
  });
}

for (const width of [1440, 390]) {
  test(`fixed canvas expands employees and reads exact results at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const drama = blockedDrama();
    await installDrama(page, drama);
    const errors = [],
      writes = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (r.url().includes("/api/") && r.method() !== "GET")
        writes.push(r.url());
    });
    await page.goto("/#project=" + dramaId + "&tab=workflow");
    await expect(page.locator(".workflow-canvas-board")).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: "部门协作", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "完整流程", exact: true }),
    ).toHaveCount(0);
    const writer = await expandRole(page, "screenwriter", "planning");
    const before = await canvasState(page);
    await writer.click();
    expect(await canvasState(page)).toEqual(before);
    await openDetails(page);
    const member = page.getByRole("complementary", { name: "节点详情" });
    await expect(
      member.getByRole("heading", { name: "编剧", exact: true }),
    ).toBeVisible();
    await member
      .locator(".collaboration-work-record > summary")
      .first()
      .click();
    await expect(member).toContainText(drama.workflow.tasks[0].input);
    await expect(member).toContainText(drama.workflow.tasks[0].output);
    await page
      .locator(".workflow-fixed-workspace")
      .screenshot({ path: `runtime/browser-report/fixed-writer-${width}.png` });
    await expect(
      member.getByRole("region", { name: "岗位实际工作记录" }),
    ).not.toContainText(drama.workflow.tasks[1].output);
    await page.reload();
    await openDetails(page);
    await expect(
      member.getByRole("heading", { name: "编剧", exact: true }),
    ).toBeVisible();
    expect(await canvasState(page)).toEqual(before);
    await member
      .getByRole("button", { name: "查看步骤材料", exact: true })
      .click();
    await expect(
      member.getByRole("heading", { name: "全剧设定与分集大纲", exact: true }),
    ).toBeVisible();
    expect(await canvasState(page)).toEqual(before);
    await closeDetails(page);
    const critic = await expandRole(page, "independent_critic", "review");
    await critic.click();
    await openDetails(page);
    await member
      .locator(".collaboration-work-record > summary")
      .first()
      .click();
    await expect(member).toContainText(drama.workflow.tasks[1].output);
    await closeDetails(page);
    const empty = await expandRole(page, "story_critic");
    await empty.click();
    await openDetails(page);
    await expect(member).toContainText("此岗位暂无已保存的执行记录");
    await noHorizontalOverflow(page);
    await page.locator(".workflow-fixed-workspace").screenshot({
      path: `runtime/browser-report/fixed-employees-${width}.png`,
    });
    expect(errors).toEqual([]);
    expect(writes).toEqual([]);
  });
  test(`fixed department and employee lines expose actual deliveries at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({
      colorScheme: width === 390 ? "dark" : "light",
      reducedMotion: "reduce",
    });
    const drama = blockedDrama();
    await installDrama(page, drama);
    drama.workflow.tasks[0].finished_at = 1792281600;
    drama.workflow.tasks[1].status = "running";
    drama.workflow.tasks[1].started_at = 1792281660;
    drama.workflow.graph.nodes.find((n) => n.id === "review").status =
      "running";
    const errors = [],
      writes = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (r.url().includes("/api/") && r.method() !== "GET")
        writes.push(r.url());
    });
    await page.goto("/#project=" + dramaId + "&tab=workflow");
    for (const phase of ["planning", "review"]) {
      await page
        .locator(`[data-stage="${phase}"]`)
        .locator("..")
        .locator(":scope > button[aria-expanded]")
        .click();
    }
    const dept = page
      .locator('[data-flow-edge][data-source="story"][data-target="review"]')
      .first();
    await pointerEdge(page, dept);
    await openDetails(page);
    const detail = page.getByRole("complementary", { name: "节点详情" });
    await expect(
      detail.getByRole("heading", { name: /编剧.*独立审稿/ }),
    ).toBeVisible();
    const received = detail.getByRole("region", { name: "已核对接收的交付" });
    await expect(
      received
        .getByText(drama.workflow.tasks[0].output, { exact: true })
        .first(),
    ).toBeVisible();
    await expect(dept).toHaveClass(/is-running/);
    const stream = dept.locator(".workflow-flow-stream");
    expect(
      await stream.evaluate((el) => getComputedStyle(el).animationName),
    ).toBe("none");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    expect(
      await stream.evaluate((el) => getComputedStyle(el).animationName),
    ).toBe("fixed-flow");
    const offset = await stream.evaluate(
      (el) => getComputedStyle(el).strokeDashoffset,
    );
    await expect
      .poll(() =>
        stream.evaluate((el) => getComputedStyle(el).strokeDashoffset),
      )
      .not.toBe(offset);
    await page.emulateMedia({ reducedMotion: "reduce" });

    const beforeClose = await canvasState(page);
    await closeDetails(page);
    expect(await canvasState(page)).toEqual(beforeClose);
    await expandRole(page, "screenwriter", "planning");
    await expandRole(page, "independent_critic", "review");
    const edge = page
      .locator(
        '[data-flow-edge][data-source="screenwriter"][data-target="independent_critic"]',
      )
      .first();
    await pointerEdge(page, edge);
    await openDetails(page);
    await assertUnobstructedLines(page);
    await expect(
      received
        .getByText(drama.workflow.tasks[0].output, { exact: true })
        .first(),
    ).toBeVisible();
    const selected = await canvasState(page),
      url = page.url();
    await page.reload();
    await openDetails(page);
    await expect(
      received
        .getByText(drama.workflow.tasks[0].output, { exact: true })
        .first(),
    ).toBeVisible();
    expect(await canvasState(page)).toEqual(selected);
    expect(page.url()).toBe(url);
    await page.locator(".workflow-fixed-workspace").screenshot({
      path: `runtime/browser-report/fixed-delivery-${width}.png`,
    });
    drama.workflow.graph.nodes.find((n) => n.id === "review").status = "paused";
    drama.workflow.tasks[1].status = "paused";
    await page.reload();
    await expect(page.locator(".workflow-canvas-board")).toBeVisible();
    await expect(page.locator(".workflow-flow-stream")).toHaveCount(0);
    await noHorizontalOverflow(page);
    await page.locator(".workflow-fixed-workspace").screenshot({
      path: `runtime/browser-report/fixed-paused-delivery-${width}.png`,
    });
    expect(errors).toEqual([]);
    expect(writes).toEqual([]);
  });
}

for (const width of [1920, 1440, 390, 320]) {
  test(`status notice is centered and stage overview wraps at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1100 });
    const drama = blockedDrama();
    drama.plan_phase = "draft";
    await installDrama(page, drama);
    await page.goto("/#project=" + dramaId);
    const notice = page.locator(".drama-next-action");
    await expect(notice).toBeVisible();
    const aligned = await notice.evaluate((el) => {
      const badge = el.querySelector(".badge").getBoundingClientRect(),
        text = el
          .querySelector(":scope > span:nth-child(2)")
          .getBoundingClientRect();
      return Math.abs(badge.y + badge.height / 2 - text.y - text.height / 2);
    });
    expect(aligned).toBeLessThan(1);
    await notice.screenshot({
      path: `runtime/browser-report/aligned-status-notice-${width}.png`,
    });
    await page
      .getByRole("navigation", { name: "整剧工作区" })
      .getByRole("button", { name: "制作流程与团队", exact: true })
      .click();
    const positions = await page.locator("[data-stage]").evaluateAll((nodes) =>
      nodes.map((el) => {
        const b = el.getBoundingClientRect();
        return { x: b.x, y: b.y };
      }),
    );
    expect(new Set(positions.map((p) => Math.round(p.y))).size).toBeGreaterThan(
      1,
    );
    if (width >= 1200) {
      expect(positions[0].y).toBe(positions[2].y);
      expect(positions[3].y).toBe(positions[4].y);
      expect(positions[3].y).toBeGreaterThan(positions[0].y);
    }
    await noHorizontalOverflow(page);
    await assertUnobstructedLines(page);
    await page.locator(".workflow-fixed-workspace").screenshot({
      path: `runtime/browser-report/compact-stage-overview-${width}.png`,
    });
  });
}
