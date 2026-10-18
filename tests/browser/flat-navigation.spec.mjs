import { test, expect } from "@playwright/test";
import { exposePageControl } from "./header-flow.mjs";
import { openWorkflow } from "./navigation.mjs";

const projectId = "012345abcdef";
const navigation = ["dashboard", "projects", "library", "activity", "models"];
const fit = async (page) => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);

async function openNavigation(page, width) {
  if (width < 1024) await page.getByRole("button", { name: "展开导航", exact: true }).click();
  return page.getByRole("navigation", { name: "主导航", exact: true });
}

async function appearance(page) {
  await (await exposePageControl(page, page.getByRole("button", { name: "外观设置", exact: true }))).click();
  await expect(page).toHaveURL(/view=settings$/);
}

test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
});

for (const width of [1440, 390]) {
  test(`five flat navigation destinations and legacy links remain usable at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    const nav = await openNavigation(page, width);
    await expect(nav.locator("[data-view]")).toHaveCount(5);
    expect(await nav.locator("[data-view]").evaluateAll((nodes) => nodes.map((node) => node.dataset.view))).toEqual(navigation);
    expect(await nav.locator("[data-view]").evaluateAll((nodes) => nodes.map((node) => Array.from(node.childNodes).filter((child) => child.nodeType === Node.TEXT_NODE).map((child) => child.textContent).join("").trim()))).toEqual(["总览", "项目", "素材库", "运行记录", "模型配置"]);
    await expect(nav.locator(".nav-group, .sidebar-label")).toHaveCount(0);
    await expect(nav.getByText(/^(工作室|管理|制作流程与团队|待办|设置)$/)).toHaveCount(0);
    await page.screenshot({ path: `runtime/browser-report/flat-navigation-${width}.png` });
    for (const [view, title] of [["dashboard", "制片总览"], ["projects", "全部项目"], ["library", "统一素材库"], ["activity", "运行记录"], ["models", "模型配置"]]) {
      // First navigation is already expanded. Selecting each item closes the mobile drawer.
      await nav.locator(`[data-view="${view}"]`).click();
      await expect(page.locator(".shared-page-header h1")).toHaveText(title);
      await fit(page);
      if (view !== "models" && width < 1024) await openNavigation(page, width);
    }
    for (const [view, title, selected] of [["inbox", "制片总览", "dashboard"], ["workflow", "全部项目", "projects"]]) {
      await page.goto("/#view=" + view);
      await expect(page.locator(".shared-page-header h1")).toHaveText(title);
      await expect(page).toHaveURL(new RegExp("view=" + view + "$"));
      const legacyNav = await openNavigation(page, width);
      await expect(legacyNav.locator('[aria-current="page"]')).toHaveAttribute("data-view", selected);
      await fit(page);
    }
    expect(errors).toEqual([]);
  });

  test(`overview retains all 101 pending items, retries pagination, and opens the older project's exact stage at ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    const base = await (await request.get("/api/factory/console")).json();
    const source = base.projects[0];
    const pending = Array.from({ length: 101 }, (_, i) => ({
      ...source,
      id: i === 100 ? projectId : (i + 1).toString(16).padStart(12, "0"),
      title: i === 100 ? "一百项之后的历史制作" : `待处理制作 ${i + 1}`,
      attention: { blocking: true, title: "终片等待验收", summary: `第 ${i + 1} 个待办`, target_tab: "decisions", action_label: "处理该集验收" },
    }));
    let rejectOlderPage = true;
    const offsets = [];
    await page.route("**/api/factory/console**", async (route) => {
      const url = new URL(route.request().url());
      if (!url.searchParams.has("attention_offset")) {
        return route.fulfill({ json: { ...base, projects: [{ ...source, id: "eeeeeeeeeeee", title: "最近制作，与历史待办不同" }], attention_projects: [], metrics: { ...base.metrics, attention: 101 } } });
      }
      expect(url.searchParams.get("attention_limit")).toBe("100");
      const offset = Number(url.searchParams.get("attention_offset"));
      offsets.push(offset);
      if (offset === 100 && rejectOlderPage) return route.fulfill({ status: 503, json: { error: "模拟待办分页中断" } });
      return route.fulfill({ json: { ...base, attention_projects: pending.slice(offset, offset + 100), attention_pagination: { offset, limit: 100, total: 101, has_more: offset === 0, next_offset: offset === 0 ? 100 : null } } });
    });
    await page.goto("/");
    const inbox = page.locator(".dashboard-inbox");
    await expect(inbox.getByRole("heading", { name: "待办", exact: true })).toBeVisible();
    await expect(inbox.locator(".attention-row")).toHaveCount(100);
    await expect(inbox).toContainText("待处理制作 100");
    await expect(inbox).toContainText("共 101 项");
    await inbox.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(page.getByText("模拟待办分页中断", { exact: true })).toBeVisible();
    rejectOlderPage = false;
    await inbox.getByRole("button", { name: "重新读取待办", exact: true }).click();
    await expect(inbox.locator(".attention-row")).toHaveCount(1);
    await expect(inbox).toContainText("一百项之后的历史制作");
    await expect(inbox.getByRole("button", { name: "下一页", exact: true })).toBeDisabled();
    await inbox.getByRole("button", { name: "上一页", exact: true }).click();
    await expect(inbox.locator(".attention-row")).toHaveCount(100);
    await inbox.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(inbox.locator(".attention-row")).toHaveCount(1);
    await fit(page);
    await inbox.getByRole("button", { name: "处理该集验收", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`project=${projectId}&tab=decisions$`));
    await expect(page.locator('form[data-form="final"]')).toBeVisible();
    expect(offsets).toContain(0);
    expect(offsets.filter((offset) => offset === 100).length).toBeGreaterThanOrEqual(2);
  });

  test(`header appearance entry preserves palette across project workflow and model configuration at ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    await appearance(page);
    const palettes = page.getByRole("group", { name: "主题色", exact: true });
    await palettes.getByText("青瓷", { exact: true }).click();
    await page.getByRole("radio", { name: "深色", exact: true }).locator("..").click();
    await page.goto(`/#project=${projectId}&tab=overview`);
    await openWorkflow(page);
    await expect(page.locator("[data-stage]")).toHaveCount(5);
    await expect(page.locator("html")).toHaveAttribute("data-accent", "teal");
    await appearance(page);
    await expect(palettes.getByRole("radio", { name: "青瓷", exact: true })).toBeChecked();
    await page.goto("/#view=models");
    await expect(page.locator(".shared-page-header h1")).toHaveText("模型配置");
    await expect(page.locator(".service-card-grid")).toBeVisible();
    await appearance(page);
    await expect(page.getByRole("radio", { name: "深色", exact: true })).toBeChecked();
    await page.reload();
    await expect(palettes.getByRole("radio", { name: "青瓷", exact: true })).toBeChecked();
    await fit(page);
    const writes = (await (await request.get("/__test/calls")).json()).filter((call) => !["GET", "HEAD"].includes(call.method));
    expect(writes).toEqual([]);
  });
}
