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
  await page.getByLabel("外观主题").selectOption("dark");
  await page.reload();
  await expect(page.getByLabel("外观主题")).toHaveValue("dark");
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
