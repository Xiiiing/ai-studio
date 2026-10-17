import { expect } from "@playwright/test";

export async function openProjectPage(page, tab) {
  const navigation = page.locator(".project-navigation");
  await expect(navigation).toBeVisible();
  if (["team", "costs"].includes(tab)) {
    const more = page.locator(".project-more");
    if ((await more.getAttribute("open")) === null)
      await more.locator(":scope > summary").click();
    await more.locator(`[data-project-page="${tab}"]`).click();
    return;
  }
  const group =
    tab === "series" ? "materials" : tab === "quality" ? "decisions" : tab;
  await navigation.locator(`[data-tab="${group}"]`).click();
  if (tab !== group)
    await page
      .locator(`.project-subnavigation [data-project-page="${tab}"]`)
      .click();
}

export async function openWorkflow(page) {
  const process = page.locator(".project-process");
  await process.waitFor();
  if ((await process.getAttribute("open")) === null)
    await process.locator(":scope > summary").click();
  await page.locator(".workflow-workspace").waitFor({ state: "visible" });
}
