import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";

const id = "dd0011223344";
function fixture() {
  return {
    id,
    record_kind: "drama",
    title: "车站的约定 · 整剧演示",
    revision: 4,
    episode_count: 3,
    duration_seconds: 60,
    format: "animation",
    status: "ready",
    plan_version: 1,
    plans: [],
    batches: [],
    episodes: [1, 2, 3].map((n) => ({
      id: `ee001122334${n}`,
      episode_number: n,
      title: `第${n}集 · 旧信`,
      status: "draft",
      progress: 0,
      plan_version: 1,
      outline: { synopsis: "林在车站发现旧信，并沿着信件寻找多年以前的约定。" },
    })),
  };
}

for (const width of [1440, 390])
  test(`whole drama selection and batch controls at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const p = fixture(),
      calls = [];
    await page.route(`**/api/factory/projects/${id}/workspace`, (r) =>
      r.fulfill({
        json: {
          project: { id, title: p.title, record_kind: "drama" },
          revision: p.revision,
          operator: {},
          flow: {},
          workflow: {},
        },
      }),
    );
    await page.route("**/api/factory/dramas/**", async (route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        calls.push(body);
        p.revision++;
        if (route.request().url().endsWith("/generate")) {
          p.episodes.forEach((e) => {
            if (body.episode_numbers.includes(e.episode_number))
              e.status = "queued";
          });
          p.batches = [
            {
              id: "bb0011223344",
              episode_numbers: body.episode_numbers,
              plan_version: 1,
              members: p.episodes
                .filter((e) => e.status === "queued")
                .map((e) => ({ number: e.episode_number, status: e.status })),
            },
          ];
        }
      }
      return route.fulfill({ json: p });
    });
    await page.goto("/#project=" + id + "&tab=episodes");
    await expect(page.getByRole("heading", { name: p.title })).toBeVisible();
    await page.getByRole("checkbox", { name: "选择第1集" }).check();
    await page.getByRole("checkbox", { name: "选择第3集" }).check();
    await page.getByRole("button", { name: "生成所选 2 集" }).click();
    await expect.poll(() => calls.length).toBe(1);
    expect(calls[0].episode_numbers).toEqual([1, 3]);
    expect(calls[0].expected_revision).toBe(4);
    await page
      .getByRole("navigation", { name: "整剧工作区" })
      .locator("summary")
      .click();
    await page.getByRole("button", { name: "生成批次", exact: true }).click();
    await expect(page.getByText("第1、3集 · 设定v1")).toBeVisible();
    await page.getByRole("button", { name: "暂停批次", exact: true }).click();
    await expect.poll(() => calls.length).toBe(2);
    expect(calls[1].action).toBe("pause");
    expect(calls[1].batch_id).toBe("bb0011223344");
    await page.getByRole("button", { name: "剧集", exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await fs.mkdir("docs/images/drama-010", { recursive: true });
    await page.screenshot({
      path: `docs/images/drama-010/episodes-${width}.png`,
      fullPage: true,
    });
  });

test("new project imports a UTF-8 script before planning", async ({ page }) => {
  let submitted;
  await page.route("**/api/factory/dramas", async (r) => {
    submitted = r.request().postDataJSON();
    await r.fulfill({
      status: 201,
      json: { id, record_kind: "drama", revision: 1 },
    });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "新建制作", exact: true })
    .first()
    .click();
  const form = page.getByRole("dialog");
  await expect(form.getByLabel("每集时长（秒）")).toHaveValue("300");
  await expect(form.getByLabel("每集时长（秒）")).toHaveAttribute("max", "900");
  await form.getByLabel("剧名", { exact: true }).fill("导入剧本演示");
  await form.getByLabel("创作起点").selectOption("script");
  await form.getByLabel("导入剧本").setInputFiles({
    name: "剧本.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "第一集：两位成年人在车站重逢，并找到一封旧信。",
      "utf8",
    ),
  });
  await expect(form.getByLabel("剧本正文")).toContainText("第一集");
  await form.getByRole("button", { name: "建立整剧项目" }).click();
  await expect.poll(() => submitted?.source_name).toBe("剧本.md");
  expect(submitted.source_text).toContain("车站重逢");
});

for (const width of [1440, 390])
  test(`long episode playback, unit entry and scoped repair at ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const eid = "ee0011223341",
      uid = "aa0011223341",
      calls = [];
    const episode = {
      id: eid,
      record_kind: "episode",
      drama_id: id,
      title: "第1集 · 旧信",
      duration_seconds: 900,
      revision: 12,
      drama_revision: 7,
      status: "awaiting_final",
      progress: 95,
      plan_version: 1,
      budget: { spent_gpu_minutes: 23 },
      outline: { synopsis: "从遗失信件发现真相，逐步完成和解。" },
      delivery: {
        video_url:
          "/media/production/factory-012345abcdef/release/v1-aaaaaaaaaaaa/episode.mp4",
      },
      units: [
        {
          id: uid,
          title: "E001-C01-U001 · 车站相遇",
          global_prefix: "E001-C01-U001",
          status: "unit_ready",
          duration_seconds: 75,
          progress: 100,
          outline: { synopsis: "林在车站发现第一封信。" },
        },
        {
          id: "aa0011223342",
          title: "E001-C01-U002 · 遗失的约定",
          global_prefix: "E001-C01-U002",
          status: "unit_ready",
          duration_seconds: 75,
          progress: 100,
          outline: { synopsis: "林继续沿信件寻找约定。" },
        },
      ],
    };
    await page.route(`**/api/factory/projects/${eid}/workspace`, (route) =>
      route.fulfill({
        json: {
          project: episode,
          revision: episode.revision,
          flow: {},
          workflow: {},
          operator: {},
        },
      }),
    );
    await page.route(`**/api/factory/dramas/${id}/episodes/${eid}`, (route) =>
      route.fulfill({ json: episode }),
    );
    await page.route(`**/api/factory/dramas/${id}/actions`, async (route) => {
      calls.push(route.request().postDataJSON());
      await route.fulfill({ json: {} });
    });
    await page.goto("/#project=" + eid);
    await expect(
      page.getByRole("heading", { name: episode.title }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "进入镜头工作台" }),
    ).toHaveCount(2);
    const video = page.locator("video");
    await video.evaluate((v) => {
      v.muted = true;
      return v.play();
    });
    await expect
      .poll(() => video.evaluate((v) => v.currentTime))
      .toBeGreaterThan(0);
    await page.getByLabel("修改范围").selectOption(uid);
    await page
      .getByLabel("局部修改或重新剪辑意见")
      .fill("只修改第一单元的第一镜，保留其他单元。");
    await page.getByRole("button", { name: "局部修改", exact: true }).click();
    await expect.poll(() => calls.length).toBe(1);
    expect(calls[0]).toMatchObject({
      action: "revise",
      episode_id: eid,
      unit_id: uid,
      expected_revision: 7,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `docs/images/drama-010/long-episode-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: "进入镜头工作台" }).first().click();
    await expect(page).toHaveURL(new RegExp("project=" + uid));
  });

test("mobile planning confirmation preserves relationship edits and fixed voice", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 1000 });
  const p = {
    ...fixture(),
    plan_version: 0,
    status: "awaiting_plan",
    episodes: [],
    draft_plan: {
      world: "当代虚构城市的车站。",
      storyline: "成年角色从遗失的信件开始调查，最终和解。",
      visual_rules: "统一水彩画风",
      relationships: ["lead与friend是多年前的朋友。"],
      characters: [
        {
          character_id: "lead",
          name: "林",
          identity_constraints: "成年女性，短发",
          default_wardrobe: "蓝色外衣",
          voice_id: "female",
        },
      ],
      scenes: [],
      props: [],
      uncertainties: ["信件寄出的具体年份尚待确认。"],
      episodes: [
        {
          number: 1,
          title: "旧信",
          synopsis: "林在车站发现旧信，沿着线索寻找旧友。",
        },
      ],
    },
    plan_review: { passed: true },
  };
  let submitted;
  await page.route(`**/api/factory/projects/${id}/workspace`, (r) =>
    r.fulfill({
      json: {
        project: { id, record_kind: "drama" },
        revision: 4,
        operator: {},
        flow: {},
        workflow: {},
      },
    }),
  );
  await page.route(`**/api/factory/dramas/${id}**`, async (r) => {
    if (r.request().method() === "POST") submitted = r.request().postDataJSON();
    await r.fulfill({ json: p });
  });
  await page.goto("/#project=" + id + "&tab=plan");
  const relation = page.getByLabel("人物关系（每行一项）");
  await relation.fill("lead与friend是重逢的旧友。");
  await relation.press("End");
  await relation.press("Enter");
  await expect(relation).toHaveValue("lead与friend是重逢的旧友。\n");
  await relation.pressSequentially("friend保留着另一封信。");
  await page.getByLabel("林声线").selectOption("male");
  await page.getByRole("button", { name: "确认全剧规划", exact: true }).click();
  await expect.poll(() => submitted?.plan?.relationships.length).toBe(2);
  expect(submitted.plan.characters[0].voice_id).toBe("male");
  expect(submitted.expected_revision).toBe(4);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
});
