import { exposePageControl, closePageTools } from "./header-flow.mjs";
import { expect } from "@playwright/test";

export function themeButton(page) {
  return page.getByRole("button", { name: /^外观主题：/ });
}

export async function exposeTheme(page) {
  return exposePageControl(page, themeButton(page));
}

export { closePageTools } from "./header-flow.mjs";

export async function setTheme(page, preference) {
  await expect(page.locator(".shared-page-header")).toBeVisible();
  for (let i = 0; i < 3; i++) {
    if (
      (await page.locator("html").getAttribute("data-theme-preference")) ===
      preference
    )
      break;
    await (await exposeTheme(page)).click();
  }
  await expect(page.locator("html")).toHaveAttribute(
    "data-theme-preference",
    preference,
  );
  await closePageTools(page);
}
