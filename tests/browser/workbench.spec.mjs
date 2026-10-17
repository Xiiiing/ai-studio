import { refreshPage } from "./header-flow.mjs";
import { manageService, openServiceDetails } from "./model-flow.mjs";
import { openProjectPage } from "./navigation.mjs";
import { test, expect } from "@playwright/test";

const id = "012345abcdef";
test.beforeEach(async ({ request, page }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname))
      return route.abort("blockedbyclient");
    return route.continue();
  });
});
async function openProject(page) {
  await page.goto("/");
  await page.locator('#project-list [data-id="' + id + '"]').click();
  await expect(page.locator("#workspace h1")).toHaveText("浏览器模拟样片");
}

test("project list, model settings and connection preflight use only mock APIs", async ({
  page,
  request,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#project-list")).toContainText("浏览器模拟样片");
  await page.locator('.main-nav [data-action="models"]').click();
  await expect(page.locator("#workspace h1")).toHaveText("模型与连接");
  await manageService(page);
  await expect(page.locator("#service-dialog")).toBeVisible();
  await page.locator("#service-dialog .model-options summary").first().click();
  await page.getByLabel("模型 ID", { exact: true }).fill("mock-writer-revised");
  await page.locator('#service-dialog button[type="submit"]').click();
  await openServiceDetails(page);
  await expect(
    page.locator("#model-service-detail-dialog .model-name").first(),
  ).toHaveText("mock-writer-revised");
  await page
    .locator('[data-model-action="test"][data-profile="writer"]')
    .click();
  await expect(page.locator("#workspace")).toContainText(
    "模拟预检，不调用模型",
  );
  await page.screenshot({
    path: "runtime/browser-report/models.png",
    fullPage: true,
  });
  const calls = await (await request.get("/__test/calls")).json();
  expect(
    calls.some(
      (call) => call.path === "/api/factory/models" && call.method === "POST",
    ),
  ).toBeTruthy();
  expect(
    calls.some((call) => call.path.includes("video_generation")),
  ).toBeFalsy();
  expect(errors).toEqual([]);
});

test("shot preview opens actual media and the final producer decision is actionable", async ({
  page,
  request,
}) => {
  await openProject(page);
  await openProjectPage(page, "script");
  await page.locator('[data-action="preview"][data-shot="S01"]').click();
  await expect(page.locator("#preview-dialog video")).toBeVisible();
  await page.waitForFunction(
    () => document.querySelector("#preview-dialog video")?.readyState >= 2,
  );
  await page.locator('#preview-dialog [data-action="close-dialog"]').click();
  await expect(page.locator("#preview-dialog video")).toHaveCount(0);
  await openProjectPage(page, "decisions");
  await page.locator('form[data-form="final"] button[value="approve"]').click();
  await expect(page.locator("#workspace .project-meta")).toContainText(
    "已通过",
  );
  const calls = await (await request.get("/__test/calls")).json();
  expect(calls.filter((call) => call.path.endsWith("/decision"))).toHaveLength(
    1,
  );
});

test("playing candidate remains the same connected node across progress refresh", async ({
  page,
  request,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openProject(page);
  await openProjectPage(page, "shots");
  await expect(page.locator(".candidate-card video")).toBeVisible();
  await page.locator(".candidate-card video").evaluate(async (video) => {
    window.__playing = video;
    window.__reloads = 0;
    video.addEventListener("emptied", () => window.__reloads++);
    video.muted = true;
    video.loop = true;
    await video.play();
  });
  await page.waitForFunction(() => window.__playing.currentTime > 0.2);
  const before = await page.evaluate(() => window.__playing.currentTime);
  await request.post("/__test/progress");
  await refreshPage(page);
  await expect(page.locator(".shot-detail-heading h2")).toContainText(
    "其他区域已更新，视频继续播放",
  );
  await page.waitForFunction(
    (before) => window.__playing.currentTime > before + 0.15,
    before,
  );
  expect(
    await page.evaluate(
      () =>
        window.__playing === document.querySelector(".candidate-card video") &&
        window.__playing.isConnected &&
        !window.__playing.paused &&
        window.__reloads === 0,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "runtime/browser-report/shot-workbench.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("failed shot refresh displays the error and does not destroy an active player", async ({
  page,
  request,
}) => {
  await openProject(page);
  await openProjectPage(page, "shots");
  await page.locator(".candidate-card video").evaluate(async (video) => {
    window.__playing = video;
    video.muted = true;
    video.loop = true;
    await video.play();
  });
  await request.post("/__test/failure", { data: { route: "/shots" } });
  await refreshPage(page);
  await expect(page.locator('.feedback-region [role="alert"]')).toContainText(
    "模拟服务暂时不可用",
  );
  expect(
    await page.evaluate(
      () =>
        window.__playing === document.querySelector(".candidate-card video") &&
        window.__playing.isConnected &&
        !window.__playing.paused,
    ),
  ).toBeTruthy();
  await expect(
    page
      .locator(
        '.shot-detail form[data-shot-operation="edit"] button[type="submit"],.shot-detail form[data-shot-operation="edit"] button:not([type])',
      )
      .last(),
  ).toBeDisabled();
});

test("CAS draft remains old after an external update and retake never calls a paid API", async ({
  page,
  request,
}) => {
  await openProject(page);
  await openProjectPage(page, "shots");
  await page.getByText("单镜精修", { exact: true }).click();
  const editor = page.locator('form[data-shot-operation="edit"]');
  await editor.locator('[name="prompt"]').fill("A revised station frame");
  await request.post("/__test/progress");
  await refreshPage(page);
  await expect(page.locator(".shot-detail")).toContainText("草稿仍绑定旧版本");
  await expect(editor).toHaveAttribute("data-revision", "7");
  await page.locator("[data-shot-discard]").click();
  await editor
    .locator('[name="prompt"]')
    .fill("A new frame after reviewing current revision");
  await editor.locator("button").last().click();
  await expect(page.locator('.feedback-region [role="status"]')).toContainText(
    "局部版本已保存",
  );
  const calls = await (await request.get("/__test/calls")).json();
  const edits = calls.filter(
    (call) => call.path.endsWith("/shots/S01") && call.method === "POST",
  );
  expect(edits).toHaveLength(1);
  expect(edits[0].body.expected_revision).toBe(8);
  expect(
    calls.some((call) => call.path.includes("video_generation")),
  ).toBeFalsy();
});

for (const width of [390, 1440])
  test(`historical shot versions stay compact and two versions can be compared at ${width}px`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.route(
      "**/api/factory/projects/012345abcdef/shots",
      async (route) => {
        const response = await route.fetch(),
          board = await response.json();
        const current = board.shots[0].candidates[0];
        board.shots[0].candidates.push(
          ...["take_002", "take_003"].map((take, index) => ({
            ...current,
            id: "video:" + take,
            take,
            plan_current: false,
            status: "failed",
            selectable: false,
            qa: {
              semantic: {
                status: "fail",
                summary: "历史版本的人物接触位置不符。",
              },
              technical: { status: "pass" },
            },
            attempt: index + 2,
          })),
        );
        await route.fulfill({ response, json: board });
      },
    );
    await page.goto("/#project=012345abcdef&tab=shots");
    await expect(page.locator("[data-shot-version]")).toHaveCount(3);
    await expect(page.locator(".candidate-card video")).toHaveCount(1);
    await expect(
      page.locator('.candidate-card[data-candidate-id="video:take_001"]'),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "对比所选版本", exact: true }),
    ).toBeDisabled();
    await page
      .getByRole("button", { name: "查看 视频 take_002", exact: true })
      .click();
    const historical = page.locator(
      '.candidate-card[data-candidate-id="video:take_002"]',
    );
    await expect(
      historical.getByLabel("审核结论", { exact: true }),
    ).toContainText("历史版本的人物接触位置不符。");
    await expect(
      historical.getByRole("button", { name: "设为选定素材" }),
    ).toBeDisabled();
    await page
      .getByRole("button", { name: "返回当前素材", exact: true })
      .click();
    await expect(
      page.locator('.candidate-card[data-candidate-id="video:take_001"]'),
    ).toBeVisible();
    await page
      .getByRole("checkbox", { name: "对比 视频 take_001", exact: true })
      .check();
    await page
      .getByRole("checkbox", { name: "对比 视频 take_002", exact: true })
      .check();
    await expect(
      page.getByRole("checkbox", { name: "对比 视频 take_003", exact: true }),
    ).toBeDisabled();
    await page
      .getByRole("button", { name: "对比所选版本（2/2）", exact: true })
      .click();
    await expect(
      page.locator('[data-shot-preview="compare"] .candidate-card'),
    ).toHaveCount(2);
    await expect(
      page.locator(
        '[data-shot-preview="compare"] [data-candidate-id="video:take_002"]',
      ),
    ).toContainText("历史版本的人物接触位置不符。");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `runtime/browser-report/shot-comparison-${width}.png`,
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "返回当前素材", exact: true })
      .click();
    await expect(
      page.locator('[data-shot-preview="current"] .candidate-card'),
    ).toHaveCount(1);
    const calls = await (await request.get("/__test/calls")).json();
    expect(
      calls.filter(
        (call) => call.method === "POST" && call.path.startsWith("/api/"),
      ),
    ).toEqual([]);
  });
