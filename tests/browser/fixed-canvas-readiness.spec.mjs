import { test, expect } from "@playwright/test";
import { expandRole, openDetails } from "./fixed-canvas.mjs";

const id = "012345abcdef";

test("role expansion waits for the workspace response before enumerating disclosures", async ({
  page,
  request,
}) => {
  await request.post("/__test/reset");
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/factory/projects/${id}/workspace`, async (route) => {
    await gate;
    await route.continue();
  });
  await page.goto(`/#project=${id}&tab=workflow`);
  const expansion = expandRole(page, "screenwriter");
  // Keep the workspace unavailable while the helper starts. A one-time empty
  // locator snapshot must not be mistaken for a graph with no disclosures.
  try {
    await expect(page.locator(".workflow-fixed-canvas")).toHaveCount(0);
    release();
    const role = await expansion;
    await role.click();
    await openDetails(page);
    const panel = page.getByRole("complementary", { name: "节点详情" });
    await expect(
      panel.locator('[data-execution="writer-current"]'),
    ).toContainText("mock-writer-current");
    await expect(page.getByRole("alert")).toHaveCount(0);
  } finally {
    release();
    await expansion.catch(() => {});
  }
});
