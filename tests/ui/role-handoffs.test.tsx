import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { RoleHandoffs } from "../../ui/factory/src/RoleHandoffs";

const record = {
  id: "a".repeat(32),
  source: "screenwriter",
  target: "storyboard_director",
  version: { script_generation: 1 },
  state: "returned",
  current_version: false,
  scope: ["S03"],
  created_at: 1791853200,
  content_sha256: "b".repeat(64),
  source_sha256: "c".repeat(64),
  request_id: "request",
  events: [{ action: "cached_result_reused", origin_call_id: "original-call" }],
};
const snapshot = {
  history_state: "recorded",
  total: 1,
  handoffs: [record],
  requests: [
    {
      id: "request",
      state: "supplemented",
      source: "storyboard_director",
      target: "screenwriter",
      scope: ["S03"],
      issues: [{ description: "补齐动作的完成状态" }],
    },
  ],
};
function view(props: Record<string, any> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RoleHandoffs projectId="012345abcdef" value={snapshot} {...props} />
    </QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());

it("leaves legacy history unknown without fetching or creating records", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  view({ value: { history_state: "not_recorded", total: 0, handoffs: [] } });
  expect(screen.getByText(/历史资料不补记为实际交接/)).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});
it("only loads frozen full material on request and distinguishes cache, version and repair from approval", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      value: { action: "冻结的原动作", prompt: "保留在完整原文中的制作提示词" },
      approval: { passed: false },
    }),
  });
  vi.stubGlobal("fetch", fetch);
  view();
  expect(fetch).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("查看交接记录 · 1 条"));
  expect(screen.getByText("已退回补料")).toBeVisible();
  expect(screen.getByText(/历史业务版本/)).toBeVisible();
  expect(screen.getByText(/补料已交回，仍需分镜及复审/)).toBeVisible();
  expect(screen.getByText(/未发起新的模型调用/)).toHaveTextContent(
    "original-call",
  );
  expect(fetch).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("查看交付原文"));
  expect(await screen.findByText("冻结的原动作")).toBeVisible();
  await userEvent.click(screen.getByText("完整 JSON 原文"));
  expect(
    screen.getByText(/"prompt": "保留在完整原文中的制作提示词"/),
  ).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toBe(
    `/api/factory/projects/012345abcdef/handoffs/${record.id}`,
  );
  expect(fetch.mock.calls[0][1].method).toBe("GET");
});
it("filters exact direction and separates actual failed calls from successful receipts", async () => {
  view({
    pairs: [{ source: "storyboard_director", target: "screenwriter" }],
    value: {
      ...snapshot,
      handoffs: [
        record,
        {
          ...record,
          id: "d".repeat(32),
          source: "storyboard_director",
          target: "screenwriter",
          state: "accepted",
          events: [
            {
              action: "call_recorded",
              call_id: "failed-call",
              status: "failed",
              delivery: "attempted",
            },
          ],
        },
      ],
      total: 2,
    },
  });
  await userEvent.click(screen.getByText("查看交接记录 · 2 条"));
  expect(screen.getByText("控制层已接收")).toBeVisible();
  expect(screen.queryByText("已退回补料")).toBeNull();
  expect(screen.getByText(/实际调用：failed-call/)).toHaveTextContent(
    "失败 · 已尝试提交",
  );
});
it("keeps stale details from fetching latest materials against an old summary", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  view({ stale: true });
  await userEvent.click(screen.getByText("查看交接记录 · 1 条"));
  expect(screen.getByText("查看交付原文")).toBeDisabled();
  expect(screen.getByText(/项目状态已变化/)).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});
it("paginates without downloading full source values", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      ...snapshot,
      total: 31,
      handoffs: [{ ...record, id: "e".repeat(32) }],
    }),
  });
  vi.stubGlobal("fetch", fetch);
  view({ value: { ...snapshot, total: 31 } });
  await userEvent.click(screen.getByText("查看交接记录 · 31 条"));
  await userEvent.click(screen.getByText("下一页"));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  expect(fetch.mock.calls[0][0]).toContain("?offset=30&limit=30");
  expect(screen.getByText("第 2 页")).toBeVisible();
});
