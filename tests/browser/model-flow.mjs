import { expect } from "@playwright/test";

export function serviceCard(page, key = "llm:writer") {
  return page.locator(`.model-service-card[data-service="${key}"]`);
}

export async function openServiceDetails(page, key = "llm:writer") {
  await serviceCard(page, key).locator('[data-model-action="details"]').click();
  const dialog = page.locator("#model-service-detail-dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

export async function manageService(page, key = "llm:writer") {
  await serviceCard(page, key).locator('[data-model-action="manage"]').click();
  await expect(page.locator("#service-dialog, #media-dialog")).toBeVisible();
}

export async function addService(page, purpose) {
  await page.locator('[data-model-action="add"]').click();
  if (purpose)
    await page
      .getByRole("group", { name: "服务用途" })
      .getByRole("button", { name: purpose, exact: true })
      .click();
}
