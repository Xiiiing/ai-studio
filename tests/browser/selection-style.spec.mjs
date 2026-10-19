import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const palettes = ["slate", "graphite", "teal", "jade", "iris", "bronze"];
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

async function openDrawer(page) {
  await page.getByRole("button", { name: "展开导航", exact: true }).click();
  await expect(
    page.locator("#studio-navigation .navigation-close"),
  ).toBeFocused();
}

async function filledSelection(locator) {
  const style = await locator.evaluate((element) => {
    const s = getComputedStyle(element);
    const rgb = (value) =>
      (value.match(/[\d.]+/g) || [])
        .slice(0, 3)
        .map((v) => Number(v) * (value.startsWith("color(srgb ") ? 255 : 1));
    const luminance = (color) =>
      color
        .map((v) => v / 255)
        .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
        .reduce((n, v, i) => n + v * [0.2126, 0.7152, 0.0722][i], 0);
    const a = luminance(rgb(s.color)),
      b = luminance(rgb(s.backgroundColor));
    return {
      background: s.backgroundColor,
      shadow: s.boxShadow,
      contrast: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
      borders: [
        s.borderTopWidth,
        s.borderRightWidth,
        s.borderBottomWidth,
        s.borderLeftWidth,
      ],
    };
  });
  expect(style.background).not.toBe("rgba(0, 0, 0, 0)");
  expect(style.contrast).toBeGreaterThanOrEqual(4.5);
  // An optional outline surrounds the entire control; no offset inset stripe.
  expect(style.shadow).not.toMatch(
    /inset\s+[1-9]\d*px\s+0px|[1-9]\d*px\s+0px[^,]*inset/,
  );
  return style;
}

for (const width of [1440, 390])
  for (const theme of ["light", "dark"]) {
    test(`monochrome main navigation and secondary setting selection stay distinct in all six palettes at ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ colorScheme: theme });
      await page.goto("/#view=settings");
      for (const palette of palettes) {
        await page
          .locator(`input[name="appearance-accent"][value="${palette}"]`)
          .locator("..")
          .click();
        const selected = page.locator(
          '.appearance-palette[data-selected="true"]',
        );
        await expect(selected.locator("input")).toBeChecked();
        await expect(selected.locator(".appearance-check")).toBeVisible();
        const category = page.locator(
          '.settings-category[aria-current="page"]',
        );
        const card = await filledSelection(category);
        expect(new Set(card.borders).size).toBe(1);
        await expect(
          category.locator(".settings-category-check"),
        ).toBeVisible();
        const colors = await category.evaluate((el) => {
          const probe = document.createElement("span");
          el.append(probe);
          probe.style.color = "var(--panel)";
          const panel = getComputedStyle(probe).color;
          probe.style.color = "var(--ink)";
          const ink = getComputedStyle(probe).color;
          probe.remove();
          return {
            panel,
            ink,
            background: getComputedStyle(el).backgroundColor,
            color: getComputedStyle(el).color,
          };
        });
        expect(colors.background).toBe(colors.panel);
        expect(colors.color).toBe(colors.ink);
        const selectedBox = await category.boundingBox();
        await category.hover();
        expect(await category.boundingBox()).toEqual(selectedBox);
        if (width < 1024) await openDrawer(page);
        const nav = page.locator('.main-nav [aria-current="page"]');
        const main = await nav.evaluate((el) => {
          const probe = document.createElement("span");
          el.append(probe);
          probe.style.color = "var(--ink)";
          const background = getComputedStyle(probe).color;
          probe.style.color = "var(--panel)";
          const color = getComputedStyle(probe).color;
          probe.remove();
          return {
            background,
            color,
            actualBackground: getComputedStyle(el).backgroundColor,
            actualColor: getComputedStyle(el).color,
          };
        });
        expect(main.actualBackground).toBe(main.background);
        expect(main.actualColor).toBe(main.color);
        await expect(nav).toHaveCSS("font-weight", "600");
        await expect(page.locator(".main-nav [data-view]")).toHaveCount(5);
        await expect(
          page.locator('.main-nav [data-view="models"]'),
        ).toHaveCount(0);
        if (width < 1024) await page.keyboard.press("Escape");
        await category.focus();
        await page.keyboard.press("Tab");
        const outline = await page.evaluate(
          () => getComputedStyle(document.activeElement).outlineWidth,
        );
        expect(parseFloat(outline)).toBeGreaterThanOrEqual(2);
      }
      // The selected marker must not change category size or position.
      await page.evaluate(() => {
        document.querySelector(".main-shell").scrollTop = 0;
        scrollTo(0, 0);
      });
      const bounds = await page
        .locator(".settings-category")
        .evaluateAll((nodes) =>
          nodes.map((el) => ({
            y: el.getBoundingClientRect().y,
            height: el.getBoundingClientRect().height,
          })),
        );
      await page
        .getByRole("button", { name: "模型与服务", exact: true })
        .click();
      await expect(page.locator(".model-service-card").first()).toBeVisible();
      expect(
        await page
          .locator(".settings-category")
          .evaluateAll((nodes) =>
            nodes.map((el) => ({
              y: el.getBoundingClientRect().y,
              height: el.getBoundingClientRect().height,
            })),
          ),
      ).toEqual(bounds);
      await page.getByRole("button", { name: "外观显示", exact: true }).click();
      await page
        .getByRole("heading", { name: "系统设置", exact: true })
        .click();
      await page.evaluate(() => scrollTo(0, 0));
      await page.mouse.move(0, 0);
      await mkdir("docs/images/navigation-selection", { recursive: true });
      await page.screenshot({
        path: `docs/images/navigation-selection/settings-${theme}-${width}.png`,
        fullPage: true,
      });
      if (width < 1024) {
        await openDrawer(page);
        await page.screenshot({
          path: `docs/images/navigation-selection/navigation-${theme}-${width}.png`,
        });
        await page.keyboard.press("Escape");
      }
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      const calls = await (await request.get("/__test/calls")).json();
      expect(calls.filter((c) => !["GET", "HEAD"].includes(c.method))).toEqual(
        [],
      );
    });
  }
