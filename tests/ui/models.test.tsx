import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { Models } from "../../ui/factory/src/Models";
import type { RecordData } from "../../ui/factory/src/api";
function inventory() {
  return {
    revision: "config-1",
    editable_profiles: {
      writer: {
        provider: "llama_cpp",
        network_mode: "loopback",
        enabled: true,
        base_url: "http://127.0.0.1:8090/v1",
        model: "writer-a",
        context_tokens: 32768,
        vision: true,
        response_format: "llama_json_schema",
        context_validation: "llama_tokenize",
        chat_template_kwargs: { enable_thinking: false },
        temperature_override: 0.25,
      },
      critic: {
        provider: "openai_compatible",
        network_mode: "loopback",
        enabled: true,
        base_url: "http://127.0.0.1:8000/v1",
        model: "critic-a",
        context_tokens: 32768,
        vision: false,
        response_format: "json_object",
        context_validation: "conservative_estimate",
      },
    },
    role_assignments: { screenwriter: "writer", independent_critic: "critic" },
    roles: [
      { role: "screenwriter", label: "编剧", profile: "writer" },
      { role: "independent_critic", label: "独立审稿", profile: "critic" },
      {
        role: "visual_critic",
        label: "视觉质检",
        profile: "writer",
        requires_vision: true,
      },
      { role: "executive_producer", label: "总制片代理", optional: true },
    ],
    video_profiles: {
      local: {
        provider: "comfyui_wan",
        model: "Wan2.2-TI2V-5B",
        enabled: true,
      },
    },
    video_roles: { video_generator: "local" },
  };
}
function setup() {
  let state = inventory();
  const calls: { path: string; body: any }[] = [];
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const fetch = vi.fn(async (path: any, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path: String(path), body });
    if (String(path).endsWith("/test"))
      return new Response(
        JSON.stringify({
          status: "unverified",
          message: "目录跳过，连接尚未验证",
        }),
      );
    if (body) {
      if (body.expected_revision !== state.revision)
        return new Response(
          JSON.stringify({ error: "配置已更新，草稿已保留" }),
          { status: 409 },
        );
      state = {
        ...state,
        revision: "config-2",
        editable_profiles: body.profiles,
        role_assignments: body.roles,
        video_profiles: body.video_profiles,
        video_roles: body.video_roles,
      };
    }
    return new Response(JSON.stringify(state));
  });
  vi.stubGlobal("fetch", fetch);
  const view = render(
    <QueryClientProvider client={client}>
      <Models notify={vi.fn()} />
    </QueryClientProvider>,
  );
  return {
    view,
    client,
    calls,
    fetch,
    update: (v: RecordData) => {
      state = { ...state, ...v };
      client.setQueryData(["models"], state);
    },
  };
}
describe("实际模型设置页面", () => {
  it("保存连接编辑保留高级参数，保存之前禁用目录检测", async () => {
    const { calls } = setup();
    await screen.findByText("writer-a");
    const card = screen.getByText("writer-a").closest("article")!;
    await userEvent.click(
      within(card).getByRole("button", { name: "编辑连接" }),
    );
    const dialog = screen.getByRole("dialog");
    await userEvent.clear(
      within(dialog).getByRole("textbox", { name: "模型标识" }),
    );
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "模型标识" }),
      "writer-b",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "加入待保存修改" }),
    );
    expect(
      within(card).getByRole("button", { name: "测试连接" }),
    ).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "保存配置" })).toBeDisabled(),
    );
    const save = calls.find((c) => c.body?.profiles)!;
    expect(save.body.expected_revision).toBe("config-1");
    expect(save.body.profiles.writer.model).toBe("writer-b");
    expect(save.body.profiles.writer.chat_template_kwargs).toEqual({
      enable_thinking: false,
    });
    expect(save.body.profiles.writer.temperature_override).toBe(0.25);
  });
  it("冲突时保留角色草稿及原revision，不改写他人的配置", async () => {
    const { calls, update } = setup();
    await screen.findByText("writer-a");
    await userEvent.selectOptions(screen.getByLabelText("编剧"), "critic");
    update({ revision: "remote-2" });
    expect(screen.getByLabelText("编剧")).toHaveValue("critic");
    await userEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await screen.findByText("配置已更新，草稿已保留");
    expect(screen.getByLabelText("编剧")).toHaveValue("critic");
    expect(calls.find((c) => c.body?.profiles)?.body.expected_revision).toBe(
      "config-1",
    );
  });
  it("切换原生Ollama后输出有效枚举并移除旧模板参数", async () => {
    const { calls } = setup();
    await screen.findByText("writer-a");
    const card = screen.getByText("writer-a").closest("article")!;
    await userEvent.click(
      within(card).getByRole("button", { name: "编辑连接" }),
    );
    const dialog = screen.getByRole("dialog");
    await userEvent.selectOptions(
      within(dialog).getByLabelText("服务类型"),
      "ollama_native",
    );
    expect(within(dialog).getByLabelText("接口地址")).toHaveValue(
      "http://127.0.0.1:11434",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "加入待保存修改" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(calls.some((c) => c.body?.profiles)).toBe(true));
    const p = calls.find((c) => c.body?.profiles)?.body.profiles.writer;
    expect(p.context_validation).toBe("conservative_estimate");
    expect(p.chat_template_kwargs).toBeUndefined();
    expect(p.response_format).toBe("openai_json_schema");
  });
  it("目录跳过保持未验证，文本模型不能选到视觉审核", async () => {
    setup();
    await screen.findByText("writer-a");
    const visual = screen.getByLabelText("视觉质检") as HTMLSelectElement;
    expect(
      within(visual).getByRole("option", { name: /critic.*不支持图像/ }),
    ).toBeDisabled();
    const card = screen.getByText("writer-a").closest("article")!;
    await userEvent.click(
      within(card).getByRole("button", { name: "测试连接" }),
    );
    await screen.findByText("目录跳过，连接尚未验证");
    expect(within(card).getByText("尚未验证连接")).toBeVisible();
  });

  it("preserves systemd credential authentication and shows mounted state", async () => {
    const { update, calls } = setup();
    await screen.findByText("writer-a");
    const original = inventory();
    const configured = {
      editable_profiles: {
        ...original.editable_profiles,
        writer: {
          ...original.editable_profiles.writer,
          api_key_credential: "qwen38_primary",
        },
      },
      profiles: [
        { id: "writer", api_key_credential: "qwen38_primary", auth_configured: false },
      ],
    };
    update(configured);
    await screen.findByText(/系统凭据：qwen38_primary.*尚未挂载/);
    update({
      profiles: [
        { id: "writer", api_key_credential: "qwen38_primary", auth_configured: true },
      ],
    });
    const card = screen.getByText("writer-a").closest("article")!;
    await waitFor(() =>
      expect(within(card).getByText(/系统凭据：qwen38_primary.*已挂载/)).toBeVisible(),
    );
    await userEvent.click(within(card).getByRole("button", { name: "编辑连接" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("保留系统凭据关联：qwen38_primary")).toBeVisible();
    await userEvent.click(within(dialog).getByRole("button", { name: "加入待保存修改" }));
    await userEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(calls.some((call) => call.body?.profiles)).toBe(true));
    const saved = calls.find((call) => call.body?.profiles)!.body.profiles.writer;
    expect(saved.api_key_credential).toBe("qwen38_primary");
    expect(saved.api_key_env).toBe("");
  });

});
