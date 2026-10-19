import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { AgentLoop } from "../../ui/factory/src/AgentLoop";
it("uses explicit snapshots and retains operation identity after an uncertain response", async () => {
  const writes: any[] = [];
  let enabled = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method === "GET")
        return new Response(JSON.stringify({ enabled, revision: 7, runs: [] }));
      writes.push(JSON.parse(init.body as string));
      if (writes.length === 1)
        return new Response(JSON.stringify({ error: "响应中断，请核实" }), {
          status: 409,
        });
      enabled = true;
      return new Response(JSON.stringify({ revision: 8 }));
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AgentLoop project={{ id: "012345abcdef" }} />
    </QueryClientProvider>,
  );
  const enable = await screen.findByRole("button", { name: "启用 Agent 循环" });
  expect(enable).toBeDisabled();
  expect(screen.getByText("尚无 Agent 执行证据")).toBeVisible();
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(enable);
  await screen.findByText("响应中断，请核实");
  expect(enable).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "重试此操作" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[0]).toEqual(writes[1]);
  expect(writes[0]).toMatchObject({
    expected_revision: 7,
    confirmed: true,
    enabled: true,
    settings: { protocol: "auto", max_turns: 12 },
  });
  await screen.findByRole("button", { name: "回退原执行流程" });
  client.clear();
});
