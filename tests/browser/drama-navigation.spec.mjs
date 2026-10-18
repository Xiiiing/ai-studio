import { test, expect } from "@playwright/test";

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
          input: "三集分集大纲与全剧角色状态。",
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
      await nav.getByRole("button", { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp("tab=" + tab));
      await expect(
        page.getByRole("heading", { name: heading, exact: true }),
      ).toBeVisible();
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
    await expect(
      nav.getByRole("button", { name: "生成批次", exact: true }),
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
      page.getByRole("button", { name: "确认全剧规划", exact: true }),
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
      page.getByRole("heading", { name: "制作流程与团队", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "团队工作与结果", exact: true }),
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
    await expect(critic).toHaveCount(1);
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
            id: "unit-1",
            title: "车站相遇",
            role: "screenwriter",
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
      page.getByRole("heading", { name: "制作流程与团队", exact: true }),
    ).toBeVisible();
    await expect(
      page
        .getByRole("navigation", { name: "剧集工作区" })
        .getByRole("button", { name: "制作流程与团队", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await noHorizontalOverflow(page);
    await page
      .getByRole("button", { name: "查看单元团队", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`project=${unitId}&tab=team`));
    await expect(
      page.getByRole("heading", { name: "团队指挥台", exact: true }),
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
      .getByRole("button", { name: "生成批次", exact: true })
      .click();
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
