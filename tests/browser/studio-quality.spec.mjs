import { openDetails, closeDetails } from "./fixed-canvas.mjs";
import { refreshPage } from "./header-flow.mjs";
import { test, expect } from "@playwright/test";
import { startRealServer } from "./real-server.mjs";
import { mkdir, readFile } from "node:fs/promises";
import { openProjectPage, openWorkflow } from "./navigation.mjs";

let server;
test.beforeAll(async () => {
  server = await startRealServer();
  await mkdir("runtime/browser-report", { recursive: true });
});
test.afterAll(async () => {
  await server?.stop();
});
test.beforeEach(async ({ page }) => {
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

for (const width of [1440, 1024, 390]) {
  test(`all studio views and project tabs work with real Python data at ${width}px`, async ({
    page,
  }) => {
    const errors = [],
      failures = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("response", (r) => {
      if (r.status() >= 500) failures.push(r.url());
    });
    await page.setViewportSize({ width, height: 1000 });
    for (const [view, title] of [
      ["dashboard", "制片总览"],
      ["projects", "全部项目"],
      ["library", "统一素材库"],
      ["inbox", "制片总览"],
      ["workflow", "全部项目"],
      ["models", "系统设置"],
      ["activity", "运行记录"],
    ]) {
      await page.goto(server.endpoint + "/#view=" + view);
      await expect(page.locator("main h1").first()).toHaveText(title);
      await expect(page.locator("main .loading")).toHaveCount(0);
      await expect(page.getByRole("alert")).toHaveCount(0);
      if (width === 390 && page.url().includes("project=")) {
        const tabsFit = await page
          .locator(".project-primary-navigation [data-tab]")
          .evaluateAll((buttons) =>
            buttons.every((button) => {
              const box = button.getBoundingClientRect();
              return box.left >= 0 && box.right <= innerWidth;
            }),
          );
        expect(tabsFit).toBeTruthy();
      }
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
    }
    for (const tab of [
      "overview",
      "decisions",
      "team",
      "shots",
      "script",
      "quality",
      "delivery",
      "series",
      "materials",
      "costs",
    ]) {
      await page.goto(
        `${server.endpoint}/#project=${server.projectId}&tab=${tab}`,
      );
      await expect(page.locator(".project-heading h1")).toHaveText(
        "HTTP 边界隔离测试",
      );
      const navigation = page.locator(".project-primary-navigation");
      await expect(navigation.locator("[data-tab]")).toHaveCount(6);
      const group =
        tab === "series" ? "materials" : tab === "quality" ? "decisions" : tab;
      if (["team", "costs"].includes(tab)) {
        await expect(
          page.locator(`.project-more [data-project-page="${tab}"]`),
        ).toHaveAttribute("aria-current", "page");
        await expect(
          page.locator(".project-more > summary"),
        ).toHaveAccessibleName(
          tab === "team"
            ? "更多项目页面：团队与回执"
            : "更多项目页面：云任务与费用",
        );
      } else {
        await expect(
          navigation.locator(`[data-tab="${group}"]`),
        ).toHaveAttribute("aria-current", "page");
        if (["materials", "series", "decisions", "quality"].includes(tab))
          await expect(
            page.locator(`.project-subnavigation [data-project-page="${tab}"]`),
          ).toHaveAttribute("aria-current", "page");
      }
      await expect(page.locator("main .loading")).toHaveCount(0);
      await expect(page.getByRole("alert")).toHaveCount(0);
      if (width === 390 && page.url().includes("project=")) {
        const tabsFit = await page
          .locator(".project-primary-navigation [data-tab]")
          .evaluateAll((buttons) =>
            buttons.every((button) => {
              const box = button.getBoundingClientRect();
              return box.left >= 0 && box.right <= innerWidth;
            }),
          );
        expect(tabsFit).toBeTruthy();
      }
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
    }
    await page.goto(
      `${server.endpoint}/#project=${server.projectId}&tab=overview`,
    );
    await openWorkflow(page);
    await expect(page.locator("[data-stage]")).toHaveCount(5);
    await page.screenshot({
      path: `runtime/browser-report/studio-real-${width}.png`,
      fullPage: true,
    });
    expect(errors).toEqual([]);
    expect(failures).toEqual([]);
  });
}

test("real Markdown attachments, retried calls and step attribution remain readable during refresh", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.goto(
    `${server.endpoint}/#project=${server.projectId}&tab=overview`,
  );
  await openWorkflow(page);
  await page.locator('[data-stage="script"]').click();
  await openDetails(page);
  await page.locator('[data-flow-node="screenplay"]').click();
  const inspector = page.getByRole("complementary", { name: "节点详情" });
  const input = inspector
    .getByRole("region", { name: "输入材料" })
    .locator(".material-card")
    .filter({ has: page.getByText("故事目标", { exact: true }) });
  await input.getByRole("button", { name: "阅读文件" }).click();
  await expect(
    input.getByRole("heading", { name: "站台上的约定" }),
  ).toBeVisible();
  await expect(input.getByRole("table")).toContainText("履行约定");
  const image = input.getByRole("img", { name: "场景参考" });
  await expect(image).toBeVisible();
  expect(
    await image.evaluate((el) => el.complete && el.naturalWidth > 0),
  ).toBeTruthy();
  const link = input.getByRole("link", { name: "分镜原文" });
  expect(
    (await page.request.get(await link.getAttribute("href"))).status(),
  ).toBe(200);
  const output = inspector.getByRole("region", { name: "交付内容" });
  await output.getByRole("button", { name: "阅读文件" }).click();
  await expect(output.getByRole("heading", { name: "站台重逢" })).toBeVisible();
  await closeDetails(page);
  await refreshPage(page);
  await openDetails(page);
  await output.getByRole("button", { name: "阅读文件" }).click();
  await expect(page.locator('[data-flow-node="screenplay"]')).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(output.getByRole("heading", { name: "站台重逢" })).toBeVisible();
  await page.screenshot({
    path: "runtime/browser-report/studio-real-document-390.png",
    fullPage: true,
  });
  await inspector
    .getByRole("button", { name: "执行记录", exact: true })
    .click();
  const current = inspector
    .locator(".execution-record")
    .filter({ hasText: "当前步骤执行" });
  await expect(current).toHaveCount(1);
  await current.getByText("第 2 次请求与响应", { exact: true }).click();
  const request = current
    .locator("details")
    .filter({ has: page.getByText("第 2 次请求与响应", { exact: true }) })
    .locator(".material-card")
    .filter({ has: page.getByText("请求原文", { exact: true }) });
  await request.getByRole("button", { name: "阅读文件" }).click();
  await expect(request).toContainText("完整输入与人物约束");
  await request.getByRole("button", { name: "原文", exact: true }).click();
  await expect(request.locator(".document-raw")).toContainText('"prompt"');
  await inspector
    .getByRole("button", { name: "材料与职责", exact: true })
    .click();
  await expect(output.getByRole("heading", { name: "站台重逢" })).toBeVisible();
  await inspector
    .getByRole("button", { name: "执行记录", exact: true })
    .click();
  await expect(request.locator(".document-raw")).toContainText('"prompt"');
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await closeDetails(page);
  await openProjectPage(page, "team");
  await expect(
    page
      .locator(".execution-record")
      .filter({ hasText: "角色的项目历史 · 未记录步骤归属" }),
  ).toHaveCount(1);
});

test("real project creation, partial image recovery, full-size preview and visual confirmation use fixed versions", async ({
  page,
}) => {
  test.setTimeout(45000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.goto(server.endpoint + "/#view=library");
  await page.getByRole("button", { name: "新建素材", exact: true }).click();
  await page.getByLabel("素材名称", { exact: true }).fill("林夏完整参考");
  await page
    .getByLabel("素材说明", { exact: true })
    .fill("成年人物，短发，灰色外套；保留身份与服装连续性。");
  await page.getByLabel("添加参考图片").setInputFiles([
    {
      name: "frame.png",
      mimeType: "image/png",
      buffer: await readFile("runtime/browser-fixtures/frame.png"),
    },
    {
      name: "damaged.png",
      mimeType: "image/png",
      buffer: Buffer.from("damaged image"),
    },
  ]);
  await page.getByRole("button", { name: "保存素材", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("图片");
  await expect(page.getByLabel("素材名称")).toHaveValue("林夏完整参考");
  await expect(
    page.getByRole("button", { name: "移除待上传图片 frame.png" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "移除待上传图片 damaged.png" })
    .click();
  await page
    .getByLabel("添加参考图片")
    .setInputFiles("runtime/browser-fixtures/frame.png");
  await page.getByRole("button", { name: "保存素材", exact: true }).click();
  await expect(page.getByLabel("素材名称", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "林夏完整参考 · v3" }),
  ).toBeVisible();
  const inventory = await (
    await page.request.get(server.endpoint + "/api/factory/library")
  ).json();
  expect(inventory.assets).toHaveLength(1);
  expect(inventory.assets[0].data.images).toHaveLength(2);
  await page
    .getByRole("button", { name: "放大查看 林夏完整参考 · frame.png" })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("img", { name: "林夏完整参考 · frame.png" }),
  ).toBeVisible();
  expect(
    (
      await page.request.get(
        await dialog
          .getByRole("link", { name: "下载参考原图" })
          .getAttribute("href"),
      )
    ).status(),
  ).toBe(200);
  await page.screenshot({
    path: "runtime/browser-report/studio-real-material-preview-390.png",
  });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "展开导航" }).click();
  await page.locator('[data-action="new-project"]').click();
  await page.getByRole("button", { name: "使用原单集入口" }).click();
  await page
    .getByLabel("项目名称 / 可选", { exact: true })
    .fill("素材准备闭环测试");
  await page
    .getByLabel("创作方向", { exact: true })
    .fill("成年主角与搭档在车站重逢并解决误会，完成一部完整的短片。");
  await page
    .getByRole("checkbox", { name: "先准备人物、场景与画风，再开始制作" })
    .check();
  await page.getByLabel("创作规格", { exact: true }).selectOption("compact6");
  await page.getByRole("button", { name: "开始自主策划" }).click();
  await expect(page.locator("main h1")).toHaveText("素材准备闭环测试");
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: "本剧素材与定稿",
      exact: true,
    }),
  ).toBeVisible();
  const pid = new URLSearchParams(new URL(page.url()).hash.slice(1)).get(
    "project",
  );
  await page.getByLabel("林夏完整参考对应角色").selectOption("char_partner");
  await page.getByRole("button", { name: "选入本剧", exact: true }).click();
  await expect(page.locator("main")).toContainText(
    "林夏完整参考 · v3 · char_partner",
  );
  await page.getByRole("button", { name: "确认本批视觉定稿" }).click();
  await expect(page.locator("main")).toContainText("已有定稿");
  const project = await (
    await page.request.get(`${server.endpoint}/api/factory/projects/${pid}`)
  ).json();
  expect(project.status).toBe("paused");
  expect(project.material_package.references[0]).toMatchObject({
    version: 3,
    role: "char_partner",
  });
  const selection = await (
    await page.request.get(
      `${server.endpoint}/api/factory/projects/${pid}/materials`,
    )
  ).json();
  const selectedRevision = selection.revision;
  // A second client changes the project; the open view must pick up its actual CAS revision.
  const nextSnapshot = page.waitForResponse(
    async (r) =>
      r.request().method() === "GET" &&
      r.url().endsWith(`/projects/${pid}/materials`) &&
      (await r.json()).revision > selectedRevision,
  );
  const external = await page.request.post(
    `${server.endpoint}/api/factory/projects/${pid}/materials`,
    {
      data: {
        action: "select",
        expected_revision: selectedRevision,
        references: project.material_package.references.map(
          ({ asset_id, version, role }) => ({ asset_id, version, role }),
        ),
      },
    },
  );
  expect(external.status()).toBe(202);
  await nextSnapshot;
  await page.getByRole("button", { name: "确认本批视觉定稿" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "确认本批视觉定稿" }),
  ).toBeEnabled();
  await page
    .locator('.feedback-region [role="status"]')
    .waitFor({ state: "hidden", timeout: 10000 });
  await page.evaluate(() => {
    document.querySelector(".main-shell").scrollTop = 0;
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: "runtime/browser-report/studio-real-materials-390.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
