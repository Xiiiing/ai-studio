import { expect } from "@playwright/test";

export async function openProjectPage(page, tab) {
  const navigation = page.locator(".project-navigation");
  await expect(navigation).toBeVisible();
  if (["team", "prompts", "generation", "agent", "costs"].includes(tab)) {
    const more = page.locator(".project-more").getByRole("button");
    if ((await more.getAttribute("aria-expanded")) !== "true")
      await more.click();
    const labels = {
      team: "团队与回执",
      prompts: "提示词工作台",
      generation: "生成提示词流水线",
      agent: "Agent 执行循环",
      costs: "云任务与费用",
    };
    await page
      .getByRole("menuitem", { name: labels[tab], exact: true })
      .click();
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
