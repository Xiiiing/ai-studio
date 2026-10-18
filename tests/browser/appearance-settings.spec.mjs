import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { exposePageControl } from "./header-flow.mjs";
import { openWorkflow } from "./navigation.mjs";
import { paletteViolations } from "./palette.mjs";

const palettes = [
  ["slate", "深灰蓝"], ["graphite", "石墨"], ["teal", "青瓷"],
  ["jade", "松绿"], ["iris", "暮紫"], ["bronze", "暖铜"],
];
const group = (page) => page.getByRole("group", { name: "主题色", exact: true });
const choose = (page, name) => group(page).getByText(name, { exact: true }).click();
const overflow = async (page) => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
const tokens = (page) => page.evaluate(() => {
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(["canvas", "panel", "success", "warning", "danger", "primary", "accent"].map((token) => [token, style.getPropertyValue(`--${token}`).trim()]));
});

test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (r) => ["127.0.0.1", "localhost"].includes(new URL(r.request().url()).hostname) ? r.continue() : r.abort());
});

for (const width of [1440, 390]) for (const theme of ["light", "dark"]) {
  test(`settings applies six palettes throughout the workspace at ${width} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ colorScheme: theme });
    const errors = [], writes = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => { if(r.url().includes("/api/") && !["GET", "HEAD"].includes(r.method())) writes.push(r.url()); });
    await page.goto("/");
    await (await exposePageControl(page, page.getByRole("button", { name: "外观设置", exact: true }))).click();
    await expect(page).toHaveURL(/view=settings$/);
    await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
    await expect(group(page).getByRole("radio")).toHaveCount(6);
    const initial = await tokens(page), primaryColors = new Set();
    for (const [id, label] of palettes) {
      await choose(page, label);
      await expect(group(page).getByRole("radio", { name: label, exact: true })).toBeChecked();
      await expect(page.locator("html")).toHaveAttribute("data-accent", id);
      const values = await tokens(page);
      for (const key of ["canvas", "panel", "success", "warning", "danger"]) expect(values[key]).toBe(initial[key]);
      primaryColors.add(values.primary);
      expect(values.primary).toBe(values.accent);
      expect(await paletteViolations(page)).toEqual([]);
      await overflow(page);
    }
    expect(primaryColors.size).toBe(6);
    await page.reload();
    await expect(group(page).getByRole("radio", { name: "暖铜", exact: true })).toBeChecked();
    await page.getByRole("radio", { name: theme === "dark" ? "浅色" : "深色", exact: true }).locator("..").click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme === "dark" ? "light" : "dark");
    await expect(page.locator("html")).toHaveAttribute("data-accent", "bronze");
    await page.getByRole("radio", { name: theme === "dark" ? "深色" : "浅色", exact: true }).locator("..").click();
    await mkdir("docs/images/appearance", { recursive: true });
    await page.screenshot({ path: `docs/images/appearance/settings-${theme}-${width}.png`, fullPage: true });
    // Navigate away and back; the same preference colors the existing workflow.
    await page.goto("/#project=012345abcdef&tab=overview");
    await expect(page.locator("html")).toHaveAttribute("data-accent", "bronze");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await openWorkflow(page);
    await expect(page.locator("[data-stage]").first()).toBeVisible();
    expect(await paletteViolations(page)).toEqual([]);
    expect(errors).toEqual([]);
    expect(writes).toEqual([]);
  });
}

test("theme color works with the keyboard at 320px and follows system mode independently", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto("/#view=settings");
  await group(page).getByRole("radio", { name: "深灰蓝", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(group(page).getByRole("radio", { name: "石墨", exact: true })).toBeChecked();
  const outline = await group(page).locator("label").filter({ has: page.getByRole("radio", { name: "石墨", exact: true }) }).evaluate((e) => getComputedStyle(e).outlineStyle);
  expect(outline).toBe("solid");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute("data-accent", "graphite");
  await overflow(page);
});

test("appearance synchronizes between tabs without any business save", async ({ page, context }) => {
  await page.goto("/#view=settings");
  const other = await context.newPage();
  await other.goto("/#view=settings");
  await choose(page, "松绿");
  await expect(group(other).getByRole("radio", { name: "松绿", exact: true })).toBeChecked();
  await page.getByRole("radio", { name: "深色", exact: true }).locator("..").click();
  await expect(other.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.evaluate(() => localStorage.clear());
  await expect(group(other).getByRole("radio", { name: "深灰蓝", exact: true })).toBeChecked();
  await expect(other.getByRole("radio", { name: "跟随系统", exact: true })).toBeChecked();
  await other.close();
});

test("settings stays usable when the business API is unavailable", async ({ page }) => {
  await page.route("**/api/factory/**", (r) => r.fulfill({ status: 503, json: { error: "fixture unavailable" } }));
  await page.goto("/#view=settings");
  await choose(page, "青瓷");
  await expect(group(page).getByRole("radio", { name: "青瓷", exact: true })).toBeChecked();
  await expect(page.locator("html")).toHaveAttribute("data-accent", "teal");
});

for (const [saved, expected] of [["iris", "iris"], ["invalid-color", "slate"]]) {
  test(`initial page applies ${expected} before the application bundle`, async ({ page }) => {
    await page.addInitScript((value) => { localStorage.setItem("studio.accent", value); localStorage.setItem("studio.theme", "dark"); }, saved);
    await page.route("**/assets/index-*.js", (r) => r.abort());
    await page.goto("/#view=settings");
    await expect(page.locator("html")).toHaveAttribute("data-accent", expected);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.locator("#root")).toBeEmpty();
  });
}
