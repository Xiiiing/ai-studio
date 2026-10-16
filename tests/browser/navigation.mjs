export async function openProjectPage(page, tab) {
  const trigger = page.getByRole("button", { name: /^项目页面：/ });
  if (await trigger.isVisible()) {
    await trigger.click();
    await page.locator(`[data-project-page="${tab}"]`).click();
  } else {
    await page.locator(`.project-navigation [data-tab="${tab}"]`).click();
  }
}
