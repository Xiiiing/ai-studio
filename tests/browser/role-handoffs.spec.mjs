import { test, expect } from "@playwright/test";
import {
  expandRole,
  openDetails,
  closeDetails,
  canvasState,
  pointerEdge,
} from "./fixed-canvas.mjs";

const ident = "a".repeat(32);
const record = {
  id: ident,
  source: "screenwriter",
  target: "storyboard_director",
  source_stage: "screenplay",
  target_stage: "script_review",
  version: { script_generation: 1 },
  state: "returned",
  current_version: false,
  scope: ["S03"],
  content_sha256: "b".repeat(64),
  source_sha256: "c".repeat(64),
  created_at: 1791853200,
  request_id: "request",
  events: [
    {
      action: "call_recorded",
      call_id: "synthetic-call",
      status: "succeeded",
      delivery: "response_received",
    },
    { action: "cached_result_reused", origin_call_id: "synthetic-call" },
  ],
};
const snapshot = {
  schema: "ai-studio.role-handoffs.v1",
  history_state: "recorded",
  total: 1,
  handoffs: [record],
  requests: [
    {
      id: "request",
      source: "storyboard_director",
      target: "screenwriter",
      scope: ["S03"],
      state: "supplemented",
      issues: [{ description: "模拟补料要求：补齐可见动作结果" }],
    },
  ],
};
for (const width of [1440, 390, 320])
  for (const theme of ["light", "dark"]) {
    test(`controlled handoff full card and fixed canvas ${width} ${theme}`, async ({
      page,
      request,
    }) => {
      await request.post("/__test/reset");
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(
        (t) => localStorage.setItem("studio.theme", t),
        theme,
      );
      const errors = [],
        writes = [],
        details = [];
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("request", (r) => {
        if (r.url().includes("/api/") && r.method() !== "GET")
          writes.push(r.url());
        if (r.url().endsWith(`/handoffs/${ident}`)) details.push(r.url());
      });
      await page.route("**/api/factory/projects/*/workspace", async (route) => {
        const response = await route.fetch();
        const data = await response.json();
        data.flow.controlled_handoffs = snapshot;
        data.workflow.nodes = [
          {
            id: "screenplay",
            label: "剧本创作",
            owner: "screenwriter",
            members: ["screenwriter"],
            status: "completed",
          },
          {
            id: "storyboard",
            label: "分镜编排",
            owner: "storyboard_director",
            members: ["storyboard_director"],
            status: "blocked",
          },
          {
            id: "script_review",
            label: "独立复审",
            owner: "independent_critic",
            members: ["independent_critic"],
            status: "pending",
          },
        ];
        data.workflow.active_node = "storyboard";
        data.workflow.edges = [
          { source: "screenplay", target: "storyboard" },
          { source: "storyboard", target: "script_review" },
        ];
        data.workflow.presentation = {
          workflow_version: 2,
          stages: [
            {
              id: "script",
              label: "剧本与分镜",
              node_ids: ["screenplay", "storyboard", "script_review"],
            },
          ],
          branches: [],
        };
        data.flow.collaboration_tasks = [];
        data.flow.task_board = { tasks: [] };
        data.flow.edges = [
          {
            source: "screenwriter",
            target: "storyboard_director",
            label: "剧本交付",
          },
          {
            source: "storyboard_director",
            target: "independent_critic",
            label: "分镜复审",
          },
          {
            source: "storyboard_director",
            target: "screenwriter",
            label: "指定镜头动作补料",
            loop: true,
          },
          {
            source: "independent_critic",
            target: "screenwriter",
            label: "独立审核返修要求",
            loop: true,
          },
        ];
        await route.fulfill({ response, json: data });
      });
      await page.route("**/api/factory/projects/*/flow", async (route) => {
        const response = await route.fetch();
        await route.fulfill({
          response,
          json: { ...(await response.json()), controlled_handoffs: snapshot },
        });
      });
      await page.route(`**/api/factory/projects/*/handoffs/${ident}`, (route) =>
        route.fulfill({
          json: {
            ...record,
            value: {
              action: "模拟交付原文：成年角色把钥匙放回桌上",
              note: "fixture-only",
              prompt:
                "Frozen production prompt " + "unaltered-material-".repeat(20),
            },
          },
        }),
      );
      await page.goto("/#project=012345abcdef&tab=workflow");
      await expect(page.locator('[data-stage="script"]')).toBeVisible();
      const target = await expandRole(page, "screenwriter", "script");
      const before = await canvasState(page);
      await target.click();
      expect(await canvasState(page)).toEqual(before);
      await openDetails(page);
      const card = page.locator("#workflow-detail-card");
      await card.getByText("查看交接记录 · 1 条", { exact: true }).click();
      await expect(
        card.getByText("补料已交回，仍需分镜及复审", { exact: true }),
      ).toBeVisible();
      await expect(card.getByText(/历史业务版本/)).toBeVisible();
      await expect(card.getByText(/实际调用：synthetic-call/)).toBeVisible();
      await expect(
        card.getByText(/复用已有结果，未发起新的模型调用/),
      ).toBeVisible();
      expect(details).toEqual([]);
      await card.getByText("查看交付原文", { exact: true }).click();
      await expect(
        card.getByText("模拟交付原文：成年角色把钥匙放回桌上", { exact: true }),
      ).toBeVisible();
      await card.getByText("完整 JSON 原文", { exact: true }).click();
      await expect(card.locator(".handoff-frozen-material pre")).toContainText(
        "Frozen production prompt",
      );
      await card.getByText("版本与校验依据", { exact: true }).click();
      expect(
        await card.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
      ).toBe(true);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `runtime/browser-report/handoffs-${theme}-${width}.png`,
        fullPage: true,
      });
      await closeDetails(page);
      expect(await canvasState(page)).toEqual(before);
      const edge = page
        .locator(
          '[data-flow-edge*="/screenwriter>"][data-flow-edge$="/storyboard_director:forward"]',
        )
        .first();
      await expect(edge).toBeAttached();
      await pointerEdge(page, edge);
      await openDetails(page);
      await card.getByText("查看交接记录 · 1 条", { exact: true }).click();
      await expect(card.getByText("已退回补料", { exact: true })).toBeVisible();
      await closeDetails(page);
      for (const sender of ["storyboard_director", "independent_critic"]) {
        const returned = page
          .locator(
            `[data-flow-edge*="/${sender}>"][data-flow-edge$="/screenwriter:repair"]`,
          )
          .first();
        await expect(returned).toBeAttached();
        await pointerEdge(page, returned);
        await openDetails(page);
        await card.getByText("查看交接记录 · 1 条", { exact: true }).click();
        await expect(
          card.getByText("本页暂无与所选岗位或连线对应的受控交接。", {
            exact: true,
          }),
        ).toBeVisible();
        await closeDetails(page);
      }
      expect(details).toHaveLength(1);
      expect(errors).toEqual([]);
      expect(writes).toEqual([]);
    });
  }
