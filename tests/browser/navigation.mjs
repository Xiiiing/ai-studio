export async function openProjectPage(page, tab) {
  // React can mount after navigation; do not mistake a not-yet-rendered mobile
  // picker for the hidden desktop tabbar. Wait for either visible control.
  await page
    .locator(
      ".project-navigation-trigger:visible, .project-navigation [data-tab]:visible",
    )
    .first()
    .waitFor({ state: "visible" });
  const trigger = page.getByRole("button", { name: /^项目页面：/ });
  if (await trigger.isVisible()) {
    await trigger.click();
    await page.locator(`[data-project-page="${tab}"]`).click();
  } else {
    await page.locator(`.project-navigation [data-tab="${tab}"]`).click();
  }
}

export async function openWorkflow(page) {
  const process = page.locator(".project-process");
  await process.waitFor();
  if ((await process.getAttribute("open")) === null)
    await process.locator(":scope > summary").click();
  await page.locator(".workflow-workspace").waitFor({ state: "visible" });
}
