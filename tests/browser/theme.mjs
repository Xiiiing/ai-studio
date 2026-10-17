import { expect } from "@playwright/test";

export function themeButton(page) {
  return page.getByRole("button", { name: /^外观主题：/ });
}

export async function setTheme(page, preference) {
  await expect(themeButton(page)).toBeVisible();
  for (let i = 0; i < 3; i++) {
    if (
      (await page.locator("html").getAttribute("data-theme-preference")) ===
      preference
    )
      return;
    await themeButton(page).click();
  }
  await expect(page.locator("html")).toHaveAttribute(
    "data-theme-preference",
    preference,
  );
}
