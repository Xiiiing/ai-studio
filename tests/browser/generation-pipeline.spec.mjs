import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
const pid = "012345abcdef",
  base = `/api/factory/projects/${pid}/generation-pipeline`;
for (const width of [1440, 390, 320])
  for (const theme of ["light", "dark"]) {
    test(`generation pipeline explicit opt-in, evidence and rollback at ${width}px ${theme}`, async ({
      page,
      request,
    }) => {
      await request.post("/__test/reset");
      let enabled = false,
        revision = 7;
      const writes = [],
        errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.route(`**${base}**`, async (route) => {
        if (route.request().method() === "POST") {
          const body = route.request().postDataJSON();
          writes.push(body);
          enabled = body.enabled;
          revision++;
          return route.fulfill({
            json: { revision, snapshot: enabled ? { version: "v1" } : null },
          });
        }
        await route.fulfill({
          json: {
            enabled,
            revision,
            shots: [
              {
                shot_id: "S01",
                source: {
                  story_goal: { brief: "不要恢复删除的雨伞" },
                  approved_shot: {
                    action: "转头看向镜头，白杯始终留在原持物手中",
                    camera_motion: "hold",
                  },
                },
              },
            ],
            records: [
              {
                id: "synthetic-pixel-receipt",
                document: {
                  phase: "video",
                  shot_id: "S01",
                  quality_status: "pending_actual_media_review",
                  first_frame: {
                    sha256: "a".repeat(64),
                    model: "synthetic-vision",
                    scope: "first_frame_pixels_only",
                    quality_approval: false,
                    observation: {
                      summary:
                        "合成首帧观察样例：白杯在画面左侧，不能据此证明后续动作完成。",
                    },
                  },
                  plan: {
                    video: {
                      initial_state: "面向窗外",
                      action: "转头，保持原持物手",
                      end_state: "面向镜头",
                    },
                  },
                  parameters: {
                    prompt: "合成协议样例，不是真实模型效果",
                    reference_sha256: ["a".repeat(64)],
                  },
                },
              },
            ],
          },
        });
      });
      await page.addInitScript(
        (t) => localStorage.setItem("studio.theme", t),
        theme,
      );
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`/#project=${pid}&tab=generation`);
      await expect(
        page.getByRole("heading", { name: "生成提示词流水线", exact: true }),
      ).toBeVisible();
      expect(await page.locator("html").getAttribute("data-theme")).toBe(theme);
      const enable = page.getByRole("button", {
        name: "启用生成提示词流水线",
        exact: true,
      });
      await expect(enable).toBeDisabled();
      await expect(page.getByText("批准规格（不代表已执行）")).toBeVisible();
      await page.getByRole("checkbox", { name: /我确认只更改/ }).check();
      await enable.click();
      await expect(
        page.getByRole("button", { name: "回退原生成流程" }),
      ).toBeDisabled();
      expect(writes[0]).toMatchObject({
        expected_revision: 7,
        confirmed: true,
        enabled: true,
      });
      await page.getByRole("checkbox", { name: /我确认只更改/ }).check();
      await page.getByRole("button", { name: "回退原生成流程" }).click();
      await expect(
        page.getByRole("button", { name: "启用生成提示词流水线", exact: true }),
      ).toBeDisabled();
      expect(writes).toHaveLength(2);
      expect(writes[1].enabled).toBe(false);
      await expect(
        page.getByText(/仅限首帧可见内容，不代表动作完成/),
      ).toBeVisible();
      await page.getByText("实际适配器参数", { exact: true }).click();
      await expect(
        page.getByText(/合成协议样例，不是真实模型效果/),
      ).toBeVisible();
      await page
        .getByRole("heading", { name: "生成提示词流水线", exact: true })
        .scrollIntoViewIfNeeded();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await fs.mkdir("docs/images/generation-pipeline", { recursive: true });
      await page.screenshot({
        path: `docs/images/generation-pipeline/pipeline-${width}-${theme}.png`,
      });
      expect(errors).toEqual([]);
    });
  }
