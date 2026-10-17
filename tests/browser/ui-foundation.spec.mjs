import { setTheme, themeButton } from "./theme.mjs";
import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("/__test/reset");
});

test("appearance follows the system, remembers overrides and initializes before React", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await setTheme(page, "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute(
    "data-theme-preference",
    "dark",
  );
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.route("**/assets/*.js", (route) => route.abort());
  await page.reload();
  expect(await page.locator("html").getAttribute("data-theme")).toBe("dark");
  expect(
    await page
      .locator("html")
      .evaluate((el) => getComputedStyle(el).colorScheme),
  ).toBe("dark");
});

test("single theme icon cycles by keyboard and announces current and next preferences", async ({
  page,
}) => {
  await page.goto("/");
  const button = themeButton(page);
  await expect(button).toHaveAccessibleName("外观主题：跟随系统；切换为浅色");
  await expect(button.locator("select")).toHaveCount(0);
  await expect(button.locator("svg")).toHaveCount(1);
  await button.focus();
  for (const [key, preference, label] of [
    ["Enter", "light", "外观主题：浅色；切换为深色"],
    ["Space", "dark", "外观主题：深色；切换为跟随系统"],
    ["Enter", "system", "外观主题：跟随系统；切换为浅色"],
  ]) {
    await page.keyboard.press(key);
    await expect(button).toBeFocused();
    await expect(button).toHaveAccessibleName(label);
    await expect(button).toHaveAttribute("title", label);
    await expect(page.locator("html")).toHaveAttribute(
      "data-theme-preference",
      preference,
    );
  }
});

test("theme changes synchronize across tabs and return to following the system", async ({
  page,
  context,
}) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  const other = await context.newPage();
  await other.emulateMedia({ colorScheme: "dark" });
  await other.goto(page.url());
  await setTheme(page, "light");
  await expect(other.locator("html")).toHaveAttribute(
    "data-theme-preference",
    "light",
  );
  await expect(other.locator("html")).toHaveAttribute("data-theme", "light");
  await setTheme(other, "dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await setTheme(page, "system");
  await expect(other.locator("html")).toHaveAttribute(
    "data-theme-preference",
    "system",
  );
  await expect(other.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await other.close();
});

test("blocked storage still allows session theme changes and system following", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    for (const name of ["getItem", "setItem", "removeItem"])
      Object.defineProperty(Storage.prototype, name, {
        configurable: true,
        value() {
          throw new DOMException("Storage blocked", "SecurityError");
        },
      });
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await setTheme(page, "light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await setTheme(page, "system");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute(
    "data-theme-preference",
    "system",
  );
  expect(errors).toEqual([]);
});

test("skip link retains the project route and focuses its workspace", async ({
  page,
}) => {
  await page.goto("/#project=012345abcdef&tab=shots");
  await expect(page.locator(".project-heading h1")).toBeVisible();
  const url = page.url();
  await page.locator(".skip-link").focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(url);
  await expect(page.locator("#workspace")).toBeFocused();
});

test("mobile drawer traps focus, dismisses with Escape or overlay and restores its trigger", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const sidebar = page.locator("#studio-navigation");
  const trigger = page.getByRole("button", { name: "展开导航", exact: true });
  await expect(sidebar).toHaveAttribute("inert", "");
  await trigger.click();
  await expect(
    sidebar.getByRole("button", { name: "关闭导航", exact: true }),
  ).toBeFocused();
  await expect(page.locator(".main-shell")).toHaveAttribute("inert", "");
  await page.keyboard.press("Shift+Tab");
  expect(
    await sidebar.evaluate((el) => el.contains(document.activeElement)),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await expect(sidebar).toHaveAttribute("inert", "");
  await trigger.click();
  await page
    .locator(".navigation-backdrop")
    .click({ position: { x: 380, y: 500 } });
  await expect(trigger).toBeFocused();
  await expect(sidebar).toHaveAttribute("inert", "");
  await trigger.click();
  await sidebar.getByRole("button", { name: "新建制作", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "发起一部新制作" }),
  ).toBeVisible();
  await expect(sidebar).toHaveAttribute("inert", "");
});

test("named modal restores focus and reduced motion disables transitions", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const trigger = page
    .getByRole("button", { name: "新建制作", exact: true })
    .first();
  await trigger.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveAccessibleName("发起一部新制作");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(
    await trigger.evaluate((el) =>
      parseFloat(getComputedStyle(el).transitionDuration),
    ),
  ).toBeLessThanOrEqual(0.001);
});

for (const width of [320, 1440])
  test(`sync failure preserves the page and indicator at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/");
    await expect(page.locator(".sync-state")).toHaveAttribute(
      "title",
      "已同步",
    );
    await page.route("**/api/factory/console**", (route) =>
      route.fulfill({ status: 503, json: { error: "测试同步中断" } }),
    );
    await page.getByRole("button", { name: "刷新项目", exact: true }).click();
    const status = page.locator(".sync-state.error-text");
    await expect(status).toHaveText("同步中断");
    expect(
      await status
        .locator("i")
        .evaluate((el) => getComputedStyle(el).backgroundColor),
    ).toBe(await status.evaluate((el) => getComputedStyle(el).color));
    await expect(page.getByRole("heading", { name: "制片总览" })).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
