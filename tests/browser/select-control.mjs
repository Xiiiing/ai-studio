import { expect } from "@playwright/test";

// Exercise the visible Radix control, using its backing form option solely to
// translate stored API values into the human-readable option the user selects.
export async function selectOption(control, value) {
  const labels = await control.evaluate((trigger, requested) => {
    const native =
      trigger.tagName === "SELECT"
        ? trigger
        : trigger.id
          ? document.getElementById(trigger.id + "-native")
          : trigger.previousElementSibling;
    if (!(native instanceof HTMLSelectElement))
      throw new Error("Missing form select for control");
    return (Array.isArray(requested) ? requested : [requested]).map((v) => {
      const option = Array.from(native.options).find(
        (o) => o.value === String(v),
      );
      if (!option) throw new Error("Unknown option value: " + v);
      return option.label;
    });
  }, value);
  await control.click();
  const page = control.page();
  for (const label of labels) {
    const option = page.getByRole("option", { name: label, exact: true });
    if (await option.count()) await option.click();
    else
      await page
        .getByRole("menuitemcheckbox", { name: label, exact: true })
        .click();
  }
  if (Array.isArray(value)) await page.keyboard.press("Escape");
  await expect(control).toBeVisible();
}

export function nativeControl(control) {
  return control.locator(
    "xpath=preceding-sibling::select[contains(@class, 'studio-select-native')][1]",
  );
}
