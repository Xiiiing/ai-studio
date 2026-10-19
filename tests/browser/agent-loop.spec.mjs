import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
const pid = "012345abcdef",
  base = `/api/factory/projects/${pid}/agent-loop`,
  run = "a".repeat(64);
for (const width of [1440, 390, 320])
  for (const theme of ["light", "dark"]) {
    test(`agent task snapshots, evidence, unknown recovery ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await request.post("/__test/reset");
      let enabled = false,
        closed = false,
        revision = 7;
      const writes = [],
        errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.route(`**${base}**`, async (route) => {
        if (route.request().method() === "POST") {
          const body = route.request().postDataJSON();
          writes.push(body);
          if (route.request().url().endsWith("/settle")) closed = true;
          else enabled = body.enabled;
          revision++;
          return route.fulfill({ json: { revision } });
        }
        if (
          route
            .request()
            .url()
            .endsWith("/" + run)
        )
          return route.fulfill({
            json: {
              candidate: { summary: "合成候选，不是已审核作品" },
              materials: { "project:brief": { sha256: "b".repeat(64) } },
              handoff: { target: "screenwriter", scope: ["S03"] },
            },
          });
        await route.fulfill({
          json: {
            enabled,
            revision,
            runs: [
              {
                id: run,
                role: "storyboard_director",
                stage: "storyboard",
                state: closed ? "closed" : "unknown",
                evidence_type: "synthetic_fixture",
                turns: 2,
                spent_output_tokens: 40,
                plan: ["保持白杯持物手，检查镜头依据"],
                snapshot: { settings: { max_turns: 12 } },
                events: [
                  {
                    seq: 1,
                    event: "tool_end",
                    tool: "read_material",
                    summary: "已读取当前材料",
                  },
                ],
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
      await page.goto(`/#project=${pid}&tab=agent`);
      await expect(
        page.getByRole("heading", { name: "Agent 执行循环", exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText("请求结果未知，停止新增请求", { exact: false }),
      ).toBeVisible();
      const enable = page.getByRole("button", { name: "启用 Agent 循环" });
      await expect(enable).toBeDisabled();
      await page.getByRole("checkbox").check();
      await enable.click();
      await expect(
        page.getByRole("button", { name: "应用新执行配置" }),
      ).toBeDisabled();
      expect(writes[0]).toMatchObject({
        enabled: true,
        expected_revision: 7,
        confirmed: true,
        settings: { protocol: "auto" },
      });
      await page.getByRole("button", { name: "查看任务产物与来源" }).click();
      await expect(page.getByText(/合成候选，不是已审核作品/)).toBeVisible();
      await page.getByRole("checkbox").check();
      await page.getByRole("button", { name: "核实后结束此未知请求" }).click();
      await expect(
        page.getByText("未知请求已结束", { exact: false }),
      ).toBeVisible();
      expect(writes[1].run_id).toBe(run);
      await page.getByText("配置与动作时间线").click();
      await expect(page.getByText(/read_material/)).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page
        .getByRole("heading", { name: "Agent 执行循环", exact: true })
        .scrollIntoViewIfNeeded();
      await fs.mkdir("docs/images/agent-loop", { recursive: true });
      await page.screenshot({
        path: `docs/images/agent-loop/agent-${width}-${theme}.png`,
      });
      expect(errors).toEqual([]);
    });
  }
