import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
const pid = "012345abcdef",
  base = `/api/factory/projects/${pid}/prompts`;
const content = {
  role: "你是原创编剧。用{{shot_count}}镜表现可见行动的原因、选择和结果。",
  project: "",
  production: "",
  model: "",
  references: "",
  repair: "",
};
const edition = {
  id: "a".repeat(64),
  key: "creator@1.0.0",
  label: "动作因果候选",
  sections: content,
  created_at: "2026-10-19T03:00:00Z",
};
for (const width of [1440, 390, 320])
  for (const theme of ["light", "dark"]) {
    test(`prompt edition preview, apply and confirmation at ${width}px ${theme}`, async ({
      page,
      request,
    }) => {
      await request.post("/__test/reset");
      await page.route("**/*", (route) =>
        ["localhost", "127.0.0.1"].includes(
          new URL(route.request().url()).hostname,
        )
          ? route.continue()
          : route.abort(),
      );
      let editions = [],
        snapshot = { versions: {}, overrides: { ...content, role: undefined } },
        revision = 7;
      const writes = [],
        errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.route(`**${base}**`, async (route) => {
        const req = route.request(),
          op = new URL(req.url()).pathname.split("/").pop();
        let result;
        if (req.method() === "GET")
          result =
            op === "calls"
              ? { calls: [] }
              : {
                  catalog: [
                    {
                      key: edition.key,
                      role: "creator",
                      base_version: "1.0.0",
                      sections: content,
                      responsibility: "创作有因果的故事，不能自行覆盖独立审核",
                    },
                  ],
                  editions,
                  verified_editions: editions.map((e) => e.id),
                  defaults: {},
                  snapshot,
                  revision,
                  trials: [],
                  parameters: ["shot_count", "format"],
                };
        else {
          const body = req.postDataJSON();
          writes.push({ op, body });
          if (op === "save") {
            result = { ...edition, sections: body.sections };
            editions = [result];
          } else if (op === "preview")
            result = {
              system: "你是原创编剧。用15镜表现可见行动的原因、选择和结果。",
              diff: "-旧版\n+可见行动的原因、选择和结果",
              prompt: {
                assembly: {
                  segments: [
                    { section: "role", source: `edition:${edition.id}` },
                  ],
                },
              },
              transforms: ["prompt_workbench"],
              model: { model: "synthetic-model", trial_available: true },
            };
          else if (op === "validate")
            result = { id: "evidence-1", passed: true };
          else if (op === "apply") {
            snapshot = {
              versions: { [edition.key]: edition },
              overrides: body.overrides,
            };
            result = { snapshot, revision: ++revision };
          } else result = {};
        }
        await route.fulfill({ json: result });
      });
      await page.addInitScript(
        (theme) => localStorage.setItem("studio.theme", theme),
        theme,
      );
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`/#project=${pid}&tab=prompts`);
      await expect(
        page.getByRole("heading", { name: "提示词工作台", exact: true }),
      ).toBeVisible();
      await page.getByLabel("新版本名称").fill("动作因果候选");
      await page.getByRole("button", { name: "保存为新版本" }).click();
      await expect(
        page.getByRole("button", { name: "验证已保存版本" }),
      ).toBeEnabled();
      await page.getByRole("button", { name: "预览装配与差异" }).click();
      await expect(
        page.getByText("你是原创编剧。用15镜表现可见行动的原因、选择和结果。", {
          exact: true,
        }),
      ).toBeVisible();
      await page.getByRole("button", { name: "验证已保存版本" }).click();
      await expect(page.getByText(/已通过结构与装配验证/)).toBeVisible();
      await page.getByRole("button", { name: "将此版本应用到项目" }).click();
      await expect(page.getByText(/此项目：动作因果候选/)).toBeVisible();
      await page.getByText("公共默认版本（高级）", { exact: true }).click();
      await expect(
        page.getByRole("button", { name: "启用为公共默认" }),
      ).toBeDisabled();
      await page.getByRole("checkbox", { name: /确认更改新项目/ }).check();
      await page.getByRole("button", { name: "启用为公共默认" }).click();
      await expect
        .poll(() => writes.some((r) => r.op === "activate"))
        .toBe(true);
      expect(writes.map((r) => r.op)).toEqual([
        "save",
        "preview",
        "validate",
        "apply",
        "activate",
      ]);
      expect(writes.some((r) => r.op === "trial")).toBe(false);
      await page
        .getByRole("heading", { name: "提示词工作台", exact: true })
        .scrollIntoViewIfNeeded();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await fs.mkdir("docs/images/prompt-workbench", { recursive: true });
      await page.screenshot({
        path: `docs/images/prompt-workbench/workbench-${width}-${theme}.png`,
      });
      expect(errors).toEqual([]);
    });
  }
