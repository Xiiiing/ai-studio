import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { openSystemSettings } from "./header-flow.mjs";
import { manageService } from "./model-flow.mjs";
import { startRealServer } from "./real-server.mjs";

const fit = async (page) =>
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
const categories = ["外观显示", "模型与服务", "运行状态", "关于与版本"];

test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

for (const width of [1440, 390, 320])
  for (const theme of ["light", "dark"]) {
    test(`settings navigation exposes appearance, configuration and actual system data at ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ colorScheme: theme });
      const base = await (await request.get("/api/factory/console")).json();
      await page.route("**/api/factory/console**", (route) =>
        route.fulfill({
          json: {
            ...base,
            queue: {
              running: 2,
              queued: 3,
              waiting_remote: 1,
              waiting_resources: 0,
              worker_concurrency: 1,
              durable: true,
            },
            capabilities: {
              ...base.capabilities,
              worker: { available: true, heartbeat: 1792317600 },
            },
          },
        }),
      );
      const errors = [],
        writes = [];
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("request", (r) => {
        if (r.url().includes("/api/") && !["GET", "HEAD"].includes(r.method()))
          writes.push(r.url());
      });
      await page.goto("/");
      await expect(
        page.getByRole("button", { name: "外观设置", exact: true }),
      ).toHaveCount(0);
      await openSystemSettings(page);
      const nav = page.getByRole("navigation", {
        name: "系统设置分类",
        exact: true,
      });
      expect(await nav.getByRole("button").allTextContents()).toHaveLength(4);
      for (const label of categories)
        await expect(
          nav.getByRole("button", { name: label, exact: true }),
        ).toBeVisible();
      await expect(
        page.locator('.main-nav [data-view="settings"]'),
      ).toHaveAttribute("aria-current", "page");
      await page
        .getByRole("radio", { name: "青柠绿", exact: true })
        .locator("..")
        .click();
      await mkdir("docs/images/system-settings", { recursive: true });
      for (const [label, id] of [
        ["外观显示", "appearance"],
        ["模型与服务", "models"],
        ["运行状态", "runtime"],
        ["关于与版本", "about"],
      ]) {
        await nav.getByRole("button", { name: label, exact: true }).click();
        await expect(page).toHaveURL(new RegExp(`view=settings&tab=${id}$`));
        await expect(
          nav.getByRole("button", { name: label, exact: true }),
        ).toHaveAttribute("aria-current", "page");
        await expect(page.locator("#workspace h1")).toHaveCount(1);
        await expect(page.locator("html")).toHaveAttribute(
          "data-accent",
          "jade",
        );
        const content = page.getByRole("region", { name: label, exact: true });
        if (id === "models") {
          await expect(
            content.locator(".model-service-card").first(),
          ).toBeVisible();
          for (const tab of ["制作分工", "高级工具", "服务连接"]) {
            await content.getByRole("tab", { name: tab, exact: true }).click();
            await expect(content.getByRole("tabpanel")).toBeVisible();
          }
          await manageService(page);
          await expect(page.locator("#service-dialog")).toBeVisible();
          await page.keyboard.press("Escape");
        }
        if (id === "runtime") {
          await expect(
            content.getByText("在线", { exact: true }),
          ).toBeVisible();
          await expect(
            content
              .locator(".settings-facts > div")
              .filter({ has: page.getByText("正在执行", { exact: true }) })
              .locator("dd"),
          ).toHaveText("2");
          await expect(content).toContainText("任务持久化");
          await expect(content).toContainText("已启用");
        }
        if (id === "about") {
          await expect(content).toContainText("3.0.0-browser-fixture");
          await expect(content).toContainText("c".repeat(40));
          await expect(content).toContainText("测试版");
          await page.reload();
          await expect(content).toContainText("3.0.0-browser-fixture");
        }
        await fit(page);
        if (width !== 320)
          await page.screenshot({
            path: `docs/images/system-settings/${id}-${theme}-${width}.png`,
            fullPage: true,
          });
      }
      await page.goBack();
      await expect(
        nav.getByRole("button", { name: "运行状态", exact: true }),
      ).toHaveAttribute("aria-current", "page");
      expect(writes).toEqual([]);
      expect(errors).toEqual([]);
    });
  }

for (const width of [1440, 390])
  test(`embedded model configuration saves once and shares the global registry at ${width}`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/#view=settings&tab=models");
    await manageService(page);
    await page
      .locator("#service-dialog .model-options summary")
      .first()
      .click();
    await page
      .getByLabel("模型 ID", { exact: true })
      .fill("mock-system-settings-writer");
    await page.locator('#service-dialog button[type="submit"]').click();
    await expect(page.locator("#service-dialog")).toHaveCount(0);
    await expect(page.locator(".model-service-card").first()).toContainText(
      "mock-system-settings-writer",
    );
    await page.goto("/#view=models");
    await expect(page.locator(".model-service-card").first()).toContainText(
      "mock-system-settings-writer",
    );
    const calls = await (await request.get("/__test/calls")).json();
    const writes = calls.filter((c) => c.method === "POST");
    expect(writes).toHaveLength(1);
    expect(writes[0].path).toBe("/api/factory/models");
    expect(writes[0].body.expected_revision).toBe("config-1");
  });

test("settings keeps appearance usable through API errors and retries missing system identity", async ({
  page,
}) => {
  let failed = true;
  await page.route("**/api/factory/system", (route) =>
    failed
      ? route.fulfill({ status: 503, json: { error: "版本读取暂时失败" } })
      : route.fulfill({
          json: {
            version: "fixture-recovered",
            source_commit: null,
            started_at: null,
            environment: "unspecified",
          },
        }),
  );
  await page.goto("/#view=settings&tab=about");
  await expect(
    page.getByText("版本读取暂时失败", { exact: true }),
  ).toBeVisible();
  failed = false;
  await page
    .getByRole("button", { name: "重新读取版本信息", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "关于与版本", exact: true }),
  ).toContainText("fixture-recovered");
  await expect(page.locator(".settings-facts")).toContainText("未提供");
  await expect(page.locator(".settings-facts")).toContainText("未标记");
  await page
    .getByRole("navigation", { name: "系统设置分类", exact: true })
    .getByRole("button", { name: "外观显示", exact: true })
    .click();
  await page
    .getByRole("radio", { name: "雾紫", exact: true })
    .locator("..")
    .click();
  await expect(page.locator("html")).toHaveAttribute("data-accent", "iris");
  await page.goto("/#view=settings&tab=unknown-category");
  await expect(
    page.getByRole("radio", { name: "雾紫", exact: true }),
  ).toBeChecked();
});

test("version page reads the real Python identity endpoint without any configuration write", async ({
  page,
}) => {
  const server = await startRealServer();
  try {
    const writes = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/") && !["GET", "HEAD"].includes(r.method()))
        writes.push(r.url());
    });
    await page.goto(server.endpoint + "/#view=settings&tab=about");
    const content = page.getByRole("region", {
      name: "关于与版本",
      exact: true,
    });
    await expect(content).toContainText("3.0.0");
    await expect(content).toContainText("未标记");
    await expect(page.locator("#workspace h1")).toHaveCount(1);
    expect(writes).toEqual([]);
    expect(
      server.responses.some(
        (r) => r.path === "/api/factory/system" && r.status === 200,
      ),
    ).toBe(true);
  } finally {
    await server.stop();
  }
});
