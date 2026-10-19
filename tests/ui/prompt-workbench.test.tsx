import { selectControl } from "./select-control";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { PromptWorkbench } from "../../ui/factory/src/PromptWorkbench";
const sections = {
  role: "创作{{shot_count}}镜的完整故事",
  project: "",
  production: "",
  model: "",
  references: "",
  repair: "",
};
const e = {
  id: "a".repeat(64),
  key: "creator@1.0.0",
  label: "角色候选",
  sections,
  created_at: "2026-10-19T03:00:00Z",
};
let saved: (typeof e)[],
  writes: { op: string; body: any }[],
  failApply: boolean;
beforeEach(() => {
  saved = [];
  writes = [];
  failApply = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      if (init.method === "GET")
        return new Response(
          JSON.stringify(
            url.endsWith("/calls")
              ? { calls: [] }
              : {
                  catalog: [
                    {
                      key: e.key,
                      role: "creator",
                      base_version: "1.0.0",
                      sections,
                    },
                  ],
                  editions: saved,
                  verified_editions: saved.map((e) => e.id),
                  defaults: {},
                  snapshot: {
                    versions: {},
                    overrides: { ...sections, role: undefined },
                  },
                  revision: 7,
                  trials: [],
                  parameters: ["shot_count"],
                },
          ),
        );
      const op = url.split("/").pop()!,
        body = JSON.parse(init.body as string);
      writes.push({ op, body });
      if (op === "save") {
        saved = [e];
        return new Response(JSON.stringify(e));
      }
      if (op === "preview")
        return new Response(
          JSON.stringify({
            system: "创作15镜的完整故事",
            diff: "-旧版\n+候选",
            prompt: {},
            transforms: ["prompt_workbench"],
            model: { model: "existing-model", trial_available: true },
          }),
        );
      if (op === "validate")
        return new Response(JSON.stringify({ id: "validation", passed: true }));
      if (op === "apply" && failApply)
        return new Response(JSON.stringify({ error: "请先暂停项目" }), {
          status: 409,
        });
      return new Response(
        JSON.stringify({ snapshot: { overrides: sections }, revision: 8 }),
      );
    }),
  );
});
function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <PromptWorkbench project={{ id: "012345abcdef", revision: 7 }} />
    </QueryClientProvider>,
  );
}
it("previews unsaved layers without saving or calling a model", async () => {
  mount();
  const user = userEvent.setup();
  await screen.findByLabelText("岗位职责");
  await user.click(screen.getByRole("button", { name: "预览装配与差异" }));
  await screen.findByText("创作15镜的完整故事");
  expect(writes.map((r) => r.op)).toEqual(["preview"]);
  expect(writes[0].body.sections.role).toContain("{{shot_count}}");
  expect(screen.getByRole("button", { name: "启动一次试运行" })).toBeDisabled();
});
it("requires an immutable edition, validation and human confirmation for activation", async () => {
  mount();
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("新版本名称"), "角色候选");
  await user.click(screen.getByRole("button", { name: "保存为新版本" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "验证已保存版本" }),
    ).toBeEnabled(),
  );
  expect(screen.getByRole("button", { name: "启用为公共默认" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "验证已保存版本" }));
  await screen.findByText(/已通过结构与装配验证/);
  await user.click(screen.getByText("公共默认版本（高级）"));
  await user.click(screen.getByRole("checkbox", { name: /确认更改新项目/ }));
  await user.click(screen.getByRole("button", { name: "启用为公共默认" }));
  await waitFor(() =>
    expect(writes.some((r) => r.op === "activate")).toBe(true),
  );
  expect(writes.find((r) => r.op === "activate")?.body).toMatchObject({
    edition_id: e.id,
    validation_id: "validation",
    confirmed: true,
  });
});
it("keeps the operation identity on a failed application and offers a fresh sync", async () => {
  saved = [e];
  failApply = true;
  mount();
  const user = userEvent.setup();
  await selectControl(await screen.findByLabelText("编辑版本"), e.id, user);
  await user.click(screen.getByRole("button", { name: "将此版本应用到项目" }));
  await screen.findByText("请先暂停项目");
  await user.click(screen.getByRole("button", { name: "重试上次操作" }));
  await waitFor(() =>
    expect(writes.filter((r) => r.op === "apply")).toHaveLength(2),
  );
  expect(writes[0].body.request_id).toBe(writes[1].body.request_id);
  await user.click(screen.getByRole("button", { name: "刷新后重新准备" }));
  expect(
    screen.getByRole("button", { name: "将此版本应用到项目" }),
  ).toBeEnabled();
});
it("changing editor content invalidates preview and validation", async () => {
  saved = [e];
  mount();
  const user = userEvent.setup();
  await selectControl(await screen.findByLabelText("编辑版本"), e.id, user);
  await user.click(screen.getByRole("button", { name: "验证已保存版本" }));
  await screen.findByText(/已通过结构与装配验证/);
  await user.type(screen.getByLabelText("岗位职责"), "，保留因果");
  expect(screen.queryByText(/已通过结构与装配验证/)).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "将此版本应用到项目" }),
  ).toBeDisabled();
});
