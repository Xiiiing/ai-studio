import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { selectOption } from "./select-control.mjs";

test.use({ video: { mode: "on", size: { width: 1440, height: 950 } } });
const id = "dd1122334455";
const drama = {
  id,
  record_kind: "drama",
  title: "交互验收项目",
  revision: 1,
  episode_count: 2,
  duration_seconds: 120,
  format: "animation",
  status: "ready",
  stage: "episodes",
  plan_version: 1,
  plans: [],
  episodes: [],
  batches: [],
  draft_plan: null,
  workflow: { tasks: [] },
};
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
  await page.route(`**/api/factory/projects/${id}/workspace`, (r) =>
    r.fulfill({
      json: { project: drama, revision: 1, workflow: {}, operator: {} },
    }),
  );
  await page.route(`**/api/factory/dramas/${id}`, (r) =>
    r.fulfill({ json: drama }),
  );
});
for (const width of [1982, 1440, 390])
  for (const theme of ["light", "dark"]) {
    test(`menu and dialog controls keep surrounding layout at ${width} ${theme}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 950 });
      await page.emulateMedia({
        colorScheme: theme,
        reducedMotion: "no-preference",
      });
      await page.goto("/#project=" + id);
      const nav = page.locator(".drama-navigation");
      await expect(nav).toBeVisible();
      const measure = () =>
        page.evaluate(() =>
          Object.fromEntries(
            [".drama-navigation", ".work-focus", ".main-shell"].map((s) => {
              const r = document.querySelector(s)?.getBoundingClientRect();
              return [
                s,
                r ? { x: r.x, y: r.y, width: r.width, height: r.height } : null,
              ];
            }),
          ),
        );
      const initial = await measure();
      const trigger = page.getByRole("button", {
        name: "项目资料",
        exact: true,
      });
      for (let i = 0; i < 3; i++) {
        await trigger.click();
        await expect(page.getByRole("menu")).toBeVisible();
        const opened = await measure();
        for (const s of Object.keys(initial))
          if (initial[s])
            for (const k of Object.keys(initial[s]))
              expect(
                Math.abs(initial[s][k] - opened[s][k]),
              ).toBeLessThanOrEqual(1);
        await page.keyboard.press("Escape");
        await expect(trigger).toBeFocused();
        expect(await measure()).toEqual(initial);
      }
      // A route selection followed by a fast reopen must keep the new menu.
      await trigger.click();
      await page
        .getByRole("menuitem", { name: "全剧规划", exact: true })
        .click();
      await trigger.click();
      await expect(page.getByRole("menu")).toBeVisible();
      await page.waitForTimeout(140);
      await expect(page.getByRole("menu")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(trigger).toBeFocused();
      if (width < 1024)
        await page
          .getByRole("button", { name: "展开导航", exact: true })
          .click();
      await page
        .getByRole("button", { name: "新建制作", exact: true })
        .first()
        .click();
      const dialog = page.getByRole("dialog", {
        name: "新建一部剧",
        exact: true,
      });
      await expect(dialog).toBeVisible();
      const combo = dialog.getByRole("combobox", {
        name: "创作起点",
        exact: true,
      });
      const before = await measure();
      await combo.click();
      const list = page.getByRole("listbox");
      await expect(list).toBeVisible();
      expect(await list.evaluate((e) => !!e.closest("dialog"))).toBe(true);
      const r = await list.boundingBox();
      expect(r.x).toBeGreaterThanOrEqual(0);
      expect(r.x + r.width).toBeLessThanOrEqual(width);
      const during = await measure();
      expect(during).toEqual(before);
      await page.keyboard.press("Escape");
      await expect(combo).toBeFocused();
      await selectOption(combo, "script");
      await expect(
        dialog.getByLabel("剧本正文", { exact: true }),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await mkdir("runtime/browser-report/interaction-030", {
        recursive: true,
      });
      await page.screenshot({
        path: `runtime/browser-report/interaction-030/controls-${width}-${theme}.png`,
      });
    });
  }
for (const reducedMotion of ["no-preference", "reduce"]) {
  test(`selection moves continuously with stable labels: ${reducedMotion}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 950 });
    await page.emulateMedia({ reducedMotion });
    await page.goto("/#view=projects");
    const group = page.locator(".segmented.selection-group").first();
    await expect(group).toBeVisible();
    const target = group.getByRole("button", { name: "已交付", exact: true });
    await expect(group.locator("[data-selection-marker]")).toBeVisible();
    const initial = await group
        .locator("[data-selection-marker]")
        .boundingBox(),
      label = await target.boundingBox();
    await target.click();
    const samples = await group.evaluate(async (group) => {
      const out = [];
      for (let i = 0; i < 30; i++) {
        await new Promise(requestAnimationFrame);
        const r = group
          .querySelector("[data-selection-marker]")
          .getBoundingClientRect();
        const marker = group.querySelector("[data-selection-marker]");
        const scale = new DOMMatrixReadOnly(getComputedStyle(marker).transform)
          .a;
        out.push({ x: r.x, width: r.width, scale });
      }
      return out;
    });
    await expect
      .poll(async () =>
        Math.abs(
          (await group.locator("[data-selection-marker]").boundingBox()).x -
            label.x,
        ),
      )
      .toBeLessThan(1);
    expect(await target.boundingBox()).toEqual(label);
    if (reducedMotion === "no-preference")
      expect(
        samples.some((r) => r.x > initial.x + 1 && r.x < label.x - 1),
      ).toBe(true);
    if (reducedMotion === "no-preference")
      expect(
        Math.max(...samples.map((sample) => sample.scale)),
      ).toBeGreaterThan(1.005);
    else
      expect(
        samples.every((sample) => Math.abs(sample.scale - 1) < 0.001),
      ).toBe(true);
    await group.getByRole("button", { name: "全部项目", exact: true }).click();
    await target.click();
    await group.getByRole("button", { name: "制作中", exact: true }).click();
    await expect(
      group.getByRole("button", { name: "制作中", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
  });
}
