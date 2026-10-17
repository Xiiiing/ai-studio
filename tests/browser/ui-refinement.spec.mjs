import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { setTheme } from "./theme.mjs";

const projectId = "012345abcdef";
const anchors = [
  ".project-heading h1",
  ".project-navigation-bar",
  ".project-pane > :first-child",
];

async function positions(page) {
  return page.evaluate(
    (selectors) =>
      Object.fromEntries(
        selectors.map((selector) => {
          const element = document.querySelector(selector);
          if (!element) throw new Error(`Missing layout anchor: ${selector}`);
          const box = element.getBoundingClientRect();
          return [selector, { x: box.x, y: box.y + scrollY, width: box.width }];
        }),
      ),
    anchors,
  );
}

async function expectStable(page, baseline) {
  const current = await positions(page);
  for (const selector of anchors)
    for (const dimension of ["x", "y", "width"])
      expect(
        Math.abs(current[selector][dimension] - baseline[selector][dimension]),
        `${selector} ${dimension}`,
      ).toBeLessThanOrEqual(1);
}

async function refreshWorkspace(page, status = 200) {
  const response = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/projects/${projectId}/workspace`) &&
      response.status() === status,
  );
  await page.locator('.topbar [data-action="refresh"]').click();
  await response;
}

test.beforeAll(async () => {
  await mkdir("runtime/ui-refinement/after", { recursive: true });
});
test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort(),
  );
});

for (const width of [320, 1440]) {
  test(`project status drawer leaves workspace anchors stable at ${width}px`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await request.post("/__test/scenario", { data: { kind: "creative" } });
    await page.goto(`/#project=${projectId}&tab=shots`);
    await expect(page.locator(".shot-workbench")).toBeVisible();
    await expect(page.locator(".project-attention-banner")).toHaveCount(0);
    const trigger = page.getByRole("button", {
      name: "查看项目状态",
      exact: true,
    });
    await expect(trigger).toBeVisible();
    const baseline = await positions(page);
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "项目状态", exact: true });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveClass(/project-status-drawer/);
    await expect(dialog).toContainText("分镜 S03 缺少关键人物动机。");
    const bounds = await dialog.boundingBox();
    expect(Math.abs(bounds.width - Math.min(width, 400))).toBeLessThanOrEqual(
      1,
    );
    expect(Math.abs(bounds.x + bounds.width - width)).toBeLessThanOrEqual(1);
    await expectStable(page, baseline);
    await expect
      .poll(() =>
        dialog.evaluate((element) => element.contains(document.activeElement)),
      )
      .toBe(true);
    await page.keyboard.press("Shift+Tab");
    // Native dialogs may cycle through browser chrome (activeElement=body),
    // but must never transfer keyboard focus to a background control.
    expect(
      await dialog.evaluate(
        (element) =>
          document.activeElement === document.body ||
          element.contains(document.activeElement),
      ),
    ).toBe(true);
    await page.keyboard.press("Tab");
    await expect
      .poll(() =>
        dialog.evaluate((element) => element.contains(document.activeElement)),
      )
      .toBe(true);
    await page.screenshot({
      path: `runtime/ui-refinement/after/project-status-${width}.png`,
      fullPage: false,
    });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await expectStable(page, baseline);
    await expect(page.locator("body")).not.toHaveCSS("overflow", "hidden");
    await page.screenshot({
      path: `runtime/ui-refinement/after/project-shots-${width}.png`,
      fullPage: true,
    });
    // The same affordance remains available when the project has no blocker.
    await page.route(`**/projects/${projectId}/workspace`, async (route) => {
      const response = await route.fetch(),
        body = await response.json();
      body.operator.attention = null;
      await route.fulfill({ response, json: body });
    });
    await refreshWorkspace(page);
    await expect(trigger).toBeVisible();
    await trigger.click();
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    const calls = await (await request.get("/__test/calls")).json();
    expect(
      calls.filter(
        (call) => call.method === "POST" && call.path.startsWith("/api/"),
      ),
    ).toEqual([]);
  });

  test(`refresh errors float, deduplicate and recover without layout jumps at ${width}px`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`/#project=${projectId}&tab=shots`);
    await expect(page.locator(".shot-workbench")).toBeVisible();
    const baseline = await positions(page);
    await request.post("/__test/failure", { data: { route: "/workspace" } });
    await refreshWorkspace(page, 503);
    const error = page
      .getByRole("alert")
      .filter({ hasText: "模拟服务暂时不可用" });
    await expect(error).toHaveCount(1);
    await expect(error).toBeVisible();
    expect(
      await error.evaluate(
        (element) =>
          getComputedStyle(element.closest(".feedback-region")).position,
      ),
    ).toBe("fixed");
    await expectStable(page, baseline);
    if (width === 320) {
      const collisions = await page.evaluate(() => {
        const feedback = document
          .querySelector(".feedback-region")
          .getBoundingClientRect();
        return [
          ...document.querySelectorAll(
            ".project-heading h1, .project-navigation-bar, .role-savebar, .config-savebar.is-dirty",
          ),
        ]
          .filter((element) => element.getClientRects().length)
          .filter((element) => {
            const box = element.getBoundingClientRect();
            return (
              box.left < feedback.right &&
              box.right > feedback.left &&
              box.top < feedback.bottom &&
              box.bottom > feedback.top
            );
          })
          .map((element) => element.className || element.tagName);
      });
      expect(
        collisions,
        "floating feedback must leave the title, project navigation and visible save controls accessible",
      ).toEqual([]);
    }
    const failures = (await (await request.get("/__test/calls")).json()).filter(
      (call) => call.path.endsWith("/workspace"),
    ).length;
    // Wait for a real scheduled poll rather than manufacturing a second error node.
    await expect
      .poll(
        async () =>
          (await (await request.get("/__test/calls")).json()).filter((call) =>
            call.path.endsWith("/workspace"),
          ).length,
        { timeout: 8000 },
      )
      .toBeGreaterThan(failures);
    await expect(error).toHaveCount(1);
    await expectStable(page, baseline);
    await page.screenshot({
      path: `runtime/ui-refinement/after/project-error-${width}.png`,
      fullPage: false,
    });
    await error
      .getByRole("button", { name: "关闭错误提示", exact: true })
      .click();
    await expect(error).toHaveCount(0);
    await refreshWorkspace(page, 503);
    await expect(error).toHaveCount(0);
    await expectStable(page, baseline);
    await request.post("/__test/failure", { data: { route: "" } });
    await refreshWorkspace(page);
    await expect(error).toHaveCount(0);
    await request.post("/__test/failure", { data: { route: "/workspace" } });
    await refreshWorkspace(page, 503);
    await expect(error).toHaveCount(1);
    await expectStable(page, baseline);
  });

  test(`typographic roles and model logo sizes follow the shared scale at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`/#project=${projectId}&tab=overview`);
    await expect(page.locator("body")).toHaveCSS(
      "font-family",
      /system-ui.*sans-serif/,
    );
    const typography = async (locator, size, leading) => {
      await expect(locator).toBeVisible();
      await expect(locator).toHaveCSS("font-size", `${size}px`);
      await expect(locator).toHaveCSS("line-height", `${leading}px`);
    };
    await typography(
      page.locator(".project-heading h1"),
      width <= 700 ? 28 : 32,
      width <= 700 ? 36 : 40,
    );
    await typography(
      page.locator(".project-focus .panel-head h2").first(),
      width <= 700 ? 18 : 20,
      width <= 700 ? 26 : 28,
    );
    await typography(
      page.locator('.project-primary-navigation [data-tab="script"]'),
      14,
      22,
    );
    await typography(
      page.locator(".project-context article > small").first(),
      12,
      18,
    );
    await page.screenshot({
      path: `runtime/ui-refinement/after/project-overview-${width}.png`,
      fullPage: true,
    });
    await page
      .locator("summary")
      .filter({ hasText: /^创作背景与最近事件$/ })
      .click();
    await typography(page.locator(".brief-text"), 16, 28);
    await page.goto("/#view=models");
    await expect(page.locator(".service-list-item").first()).toBeVisible();
    const logoSizes = async (selector, size, imageSize) => {
      const containers = page.locator(selector);
      expect(await containers.count(), selector).toBeGreaterThan(0);
      for (const container of await containers.all()) {
        await expect(container).toHaveCSS("width", `${size}px`);
        await expect(container).toHaveCSS("height", `${size}px`);
        for (const image of await container.locator("img").all()) {
          await expect(image).toHaveCSS("width", `${imageSize}px`);
          await expect(image).toHaveCSS("height", `${imageSize}px`);
          await expect(image).toHaveCSS("object-fit", "contain");
          await expect
            .poll(() =>
              image.evaluate(
                (element) => element.complete && element.naturalWidth > 0,
              ),
            )
            .toBe(true);
        }
      }
    };
    await logoSizes(".service-list-item .model-symbol", 32, 24);
    if (width <= 720) await page.locator('[data-service="llm:writer"]').click();
    await logoSizes(
      ".service-detail .service-editor-brand .model-symbol",
      40,
      28,
    );
    if (width <= 720)
      await page.getByRole("button", { name: "服务列表", exact: true }).click();
    await page
      .getByRole("button", { name: "添加服务", exact: true })
      .first()
      .click();
    await logoSizes(".provider-picker-grid .model-symbol", 40, 28);
    await page.keyboard.press("Escape");
    await page.screenshot({
      path: `runtime/ui-refinement/after/models-${width}.png`,
      fullPage: true,
    });
  });
}

for (const theme of ["light", "dark"])
  test(`representative surfaces, states and controls use neutral or blue colors in ${theme}`, async ({
    page,
    request,
  }) => {
    await page.goto("/#view=models");
    await setTheme(page, theme);
    await request.post("/__test/scenario", { data: { kind: "creative" } });
    for (const hash of [
      "view=models",
      `project=${projectId}&tab=overview`,
      `project=${projectId}&tab=shots`,
      `project=${projectId}&tab=quality`,
    ]) {
      await page.goto("/#" + hash);
      await expect(page.locator("main h1").first()).toBeVisible();
      await expect(page.locator("main .loading")).toHaveCount(0);
      const violations = await page.evaluate(() => {
        const allowed = (value) => {
          const components = value.match(/[\d.]+/g)?.map(Number);
          if (!components || components.length < 3) return true;
          const [r, g, b, alpha = 1] = components;
          if (alpha === 0 || Math.max(r, g, b) - Math.min(r, g, b) <= 14)
            return true;
          // Blue accents are allowed; logos and media are checked separately.
          return b > r + 10 && b >= g && g >= r && (g - r) / (b - r) <= 0.85;
        };
        const properties = [
          "color",
          "backgroundColor",
          "borderTopColor",
          "borderRightColor",
          "borderBottomColor",
          "borderLeftColor",
        ];
        return [...document.querySelectorAll("body *")]
          .filter(
            (element) =>
              element.getClientRects().length &&
              !element.closest("img,video,canvas,.model-symbol img"),
          )
          .flatMap((element) => {
            const style = getComputedStyle(element);
            return properties.flatMap((property) =>
              allowed(style[property])
                ? []
                : [
                    {
                      element: `${element.tagName}.${element.className?.baseVal ?? element.className}`,
                      property,
                      value: style[property],
                    },
                  ],
            );
          })
          .slice(0, 20);
      });
      expect(violations, hash).toEqual([]);
    }
  });

test("Escape closes nested error details while preserving the project form and its inputs", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.locator('[data-action="new-project"]').click();
  const form = page.getByRole("dialog", {
    name: "发起一部新制作",
    exact: true,
  });
  const brief = "测试错误详情关闭后继续保留创作草稿。";
  await form.getByLabel("创作方向", { exact: true }).fill(brief);
  await form
    .locator("summary")
    .filter({ hasText: /^预算、系列与研究资料$/ })
    .click();
  await form.getByLabel("稳定角色 JSON / 可选", { exact: true }).fill("{");
  await form.getByRole("button", { name: "开始自主策划", exact: true }).click();
  await expect(form.getByRole("alert")).toBeVisible();
  await expect(form.locator(".form-feedback-slot")).toHaveCSS("height", "64px");
  const details = form.getByRole("button", { name: "查看详情", exact: true });
  await details.click();
  await expect(
    page.getByRole("dialog", { name: "错误详情", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("dialog", { name: "错误详情", exact: true }),
  ).toHaveCount(0);
  await expect(form).toBeVisible();
  await expect(form.getByLabel("创作方向", { exact: true })).toHaveValue(brief);
  await expect(
    form.getByLabel("稳定角色 JSON / 可选", { exact: true }),
  ).toHaveValue("{");
  await expect(details).toBeFocused();
  const calls = await (await request.get("/__test/calls")).json();
  expect(
    calls.filter(
      (call) => call.method === "POST" && call.path.startsWith("/api/"),
    ),
  ).toEqual([]);
});
