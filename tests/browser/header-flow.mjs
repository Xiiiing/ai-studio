import { expect } from "@playwright/test";

export async function exposePageControl(page, control) {
  await expect(page.locator(".shared-page-header")).toBeVisible();
  if (!(await control.isVisible())) {
    const tools = page.getByRole("button", { name: "页面工具", exact: true });
    // A control may be waiting for query state rather than hidden by mobile CSS.
    // Desktop has no exposed tools trigger, so wait for that control directly.
    if (
      (await tools.isVisible()) &&
      (await tools.getAttribute("aria-expanded")) !== "true"
    )
      await tools.click();
    await expect(control).toBeVisible();
  }
  return control;
}

export async function closePageTools(page) {
  const tools = page.getByRole("button", { name: "页面工具", exact: true });
  if (
    (await tools.isVisible()) &&
    (await tools.getAttribute("aria-expanded")) === "true"
  ) {
    await page.keyboard.press("Escape");
    await expect(tools).toHaveAttribute("aria-expanded", "false");
  }
}

export async function refreshPage(page) {
  const refresh = page.locator('.shared-page-header [data-action="refresh"]');
  await (await exposePageControl(page, refresh)).click();
  await closePageTools(page);
}

export async function openSystemSettings(page) {
  const nav = page.getByRole("navigation", { name: "主导航", exact: true });
  if (!(await nav.isVisible())) await page.getByRole("button", { name: "展开导航", exact: true }).click();
  await nav.getByRole("button", { name: "系统设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "系统设置", exact: true })).toBeVisible();
}
