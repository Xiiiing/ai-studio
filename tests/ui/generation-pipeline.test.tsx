import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { GenerationPipeline } from "../../ui/factory/src/GenerationPipeline";

it("requires explicit selection, preserves the failed operation identity and distinguishes spec from evidence", async () => {
  const writes: any[] = [];
  let enabled = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method === "GET")
        return new Response(
          JSON.stringify({
            enabled,
            revision: 7,
            shots: [
              {
                shot_id: "S01",
                source: {
                  story_goal: { brief: "保留动作因果" },
                  approved_shot: { action: "转头，杯子留在原持物手" },
                },
              },
            ],
            records: [],
          }),
        );
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
      <GenerationPipeline project={{ id: "012345abcdef" }} />
    </QueryClientProvider>,
  );
  const enable = await screen.findByRole("button", {
    name: "启用生成提示词流水线",
  });
  expect(enable).toBeDisabled();
  expect(screen.getByText("尚未执行新流水线")).toBeVisible();
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(enable);
  await screen.findByText("响应中断，请核实");
  expect(enable).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "重试此操作" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[0]).toEqual(writes[1]);
  expect(writes[0]).toMatchObject({
    confirmed: true,
    enabled: true,
    expected_revision: 7,
  });
  await screen.findByRole("button", { name: "回退原生成流程" });
  expect(screen.getByText("尚未执行新流水线")).toBeVisible();
  client.clear();
});
