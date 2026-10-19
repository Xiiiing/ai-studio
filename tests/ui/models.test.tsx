import { nativeControl, selectControl } from "./select-control";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
  act,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { Models, normalizeProfile } from "../../ui/factory/src/Models";
import type { RecordData } from "../../ui/factory/src/api";

it("keeps the client TLS identity on its configured authority only", () => {
  const tls = {
    tls_ca_file_env: "CA",
    tls_client_cert_env: "CERT",
    tls_client_key_env: "KEY",
  };
  const previous = {
    ...tls,
    provider: "llama_cpp",
    base_url: "https://172.20.114.10:8790/inference/models/primary/v1",
  };
  const values = {
    ...previous,
    model: "writer",
    enabled: "on",
    allowed_hosts: "172.20.114.10",
    context_tokens: "32768",
    request_timeout_seconds: "480",
  };
  expect(normalizeProfile(values, previous)).toMatchObject(tls);
  for (const base_url of [
    "https://other.example/v1",
    "https://172.20.114.10:9999/v1",
    "http://172.20.114.10/v1",
  ])
    for (const key of Object.keys(tls))
      expect(
        normalizeProfile({ ...values, base_url }, previous)[key],
      ).toBeUndefined();
});
it("serializes H3 quality parameters and preserves an unchanged legacy preview", () => {
  const legacy = {
    provider: "comfyui_h3",
    model: "MiniMax-H3",
    enabled: true,
    revision: "wkp-dualgpu-turbo4-v1",
  };
  const values = {
    ...legacy,
    enabled: "on",
    quality: "production",
    steps: "28",
    shift_video: "10.5",
    shift_audio: "2",
    sampler: "heun",
  };
  expect(normalizeProfile(values, legacy, true)).toEqual({
    ...legacy,
    revision: "wkp-dualgpu-base-v1",
    quality: "production",
    steps: 28,
    shift_video: 10.5,
    shift_audio: 2,
    sampler: "heun",
  });
  expect(
    normalizeProfile({ ...values, quality: "preview" }, legacy, true),
  ).toEqual(legacy);
  expect(
    normalizeProfile(
      { ...values, quality: "preview" },
      { ...legacy, quality: "production" },
      true,
    ),
  ).toMatchObject({
    quality: "preview",
    steps: 4,
    shift_video: 12,
    shift_audio: 3,
    sampler: "euler",
    revision: "wkp-dualgpu-turbo4-v1",
  });
});
function inventory() {
  return {
    revision: "config-1",
    providers: [
      { id: "llama_cpp", label: "llama.cpp" },
      { id: "openai_compatible", label: "兼容接口" },
      { id: "ollama_native", label: "Ollama 原生" },
    ],
    presets: {
      ollama_native: {
        base_url: "http://127.0.0.1:11434",
        response_format: "openai_json_schema",
        model_discovery: "required",
      },
    },
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
async function roleTab() {
  fireEvent.click(await screen.findByRole("tab", { name: "制作分工" }));
}
async function addCustom() {
  fireEvent.click(await screen.findByRole("button", { name: "添加服务" }));
  fireEvent.click(screen.getByRole("button", { name: "兼容接口" }));
  return within(screen.getByRole("dialog"));
}
describe("服务与制作分工", () => {
  it("shows compact service cards and keeps roles separate from connections", async () => {
    setup();
    expect(await screen.findByText("writer-a")).toBeVisible();
    expect(screen.queryByText("接入向导")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("编剧", { exact: true }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "保存配置" }),
    ).not.toBeInTheDocument();
    await roleTab();
    expect(nativeControl(screen.getByLabelText("文本创作模型"))).toHaveValue(
      "writer",
    );
    expect(nativeControl(screen.getByLabelText("视觉审核模型"))).toHaveValue(
      "writer",
    );
    await userEvent.click(screen.getByLabelText("视觉审核模型"));
    expect(screen.getByRole("option", { name: /critic-a/ })).toHaveAttribute(
      "data-disabled",
    );
    await userEvent.keyboard("{Escape}");
  });
  it("groups models sharing the same endpoint and credential, while separating distinct keys", async () => {
    const { update, view } = setup();
    await screen.findByText("writer-a");
    const first = inventory().editable_profiles.writer;
    update({
      editable_profiles: {
        one: { ...first, api_key_env: "KEY_ONE" },
        two: { ...first, model: "second-model", api_key_env: "KEY_ONE" },
        other: { ...first, model: "third-model", api_key_env: "KEY_TWO" },
      },
    });
    expect(await screen.findByTitle("writer-a、second-model")).toBeVisible();
    expect(
      view.container.querySelectorAll(
        '.model-service-card[data-service^="llm:"]',
      ),
    ).toHaveLength(2);
    expect(screen.getByText("third-model")).toBeVisible();
    const grouped = view.container.querySelector(
      '[data-service="llm:one"]',
    ) as HTMLElement;
    expect(
      within(grouped).getByRole("button", { name: /^查看模型/ }),
    ).toHaveTextContent("2 个模型");
    expect(
      screen.queryByRole("dialog", { name: "服务详情" }),
    ).not.toBeInTheDocument();
  });
  it("saves a service once, supports multiple manually selected models and writes keys separately", async () => {
    const { calls } = setup();
    const dialog = await addCustom();
    fireEvent.change(dialog.getByLabelText("服务 URL"), {
      target: { value: "http://127.0.0.1:9000/gateway/v1/chat/completions" },
    });
    fireEvent.change(dialog.getByLabelText("API Key"), {
      target: { value: "fixture-key" },
    });
    for (const model of ["text-model", "vision-named-but-unknown"]) {
      fireEvent.change(
        dialog.getByLabelText("手动添加模型 ID", { exact: true }),
        { target: { value: model } },
      );
      fireEvent.click(dialog.getByRole("button", { name: "添加" }));
    }
    fireEvent.click(dialog.getByRole("button", { name: "保存连接" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const saves = calls.filter((c) => c.path.endsWith("/models") && c.body);
    expect(saves).toHaveLength(1);
    const profiles = Object.entries(saves[0].body.profiles).filter(
      ([id]) => !["writer", "critic"].includes(id),
    ) as [string, RecordData][];
    expect(profiles).toHaveLength(2);
    for (const [id, p] of profiles) {
      expect(p).toMatchObject({
        base_url: "http://127.0.0.1:9000/gateway/v1",
        network_mode: "loopback",
        vision: false,
      });
      expect(saves[0].body.secrets["llm:" + id]).toBe("fixture-key");
    }
    expect(JSON.stringify(saves[0].body.profiles)).not.toContain("fixture-key");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it("retains per-model settings, mTLS, stable IDs and role bindings when editing one shared service", async () => {
    const { calls, update } = setup();
    await screen.findByText("writer-a");
    const p = {
      ...inventory().editable_profiles.writer,
      tls_ca_file_env: "CA",
      tls_client_cert_env: "CERT",
      tls_client_key_env: "TLS_KEY",
      api_key_credential: "system.key",
    };
    update({
      editable_profiles: {
        writer: p,
        second: {
          ...p,
          model: "second-model",
          request_timeout_seconds: 240,
          context_tokens: 65536,
          vision: false,
          temperature_override: 0.8,
        },
      },
    });
    await screen.findByTitle("writer-a、second-model");
    fireEvent.click(screen.getAllByRole("button", { name: /^配置连接 / })[0]);
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "保存连接" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const payload = calls.find((c) => c.body?.expected_revision)?.body;
    expect(payload.profiles.writer).toMatchObject(p);
    expect(payload.profiles.second).toMatchObject({
      context_tokens: 65536,
      request_timeout_seconds: 240,
      vision: false,
      temperature_override: 0.8,
      tls_client_key_env: "TLS_KEY",
    });
    expect(payload.roles.screenwriter).toBe("writer");
    expect(payload).not.toHaveProperty("secrets");
  });
  it("keeps the editor open with its captured revision and key after a CAS conflict", async () => {
    const { calls, update } = setup();
    await screen.findByText("writer-a");
    fireEvent.click(screen.getAllByRole("button", { name: /^配置连接 / })[0]);
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.change(dialog.getByLabelText("API Key"), {
      target: { value: "keep-key" },
    });
    update({ revision: "config-other" });
    fireEvent.click(dialog.getByRole("button", { name: "保存连接" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent("配置已更新");
    expect(dialog.getByLabelText("API Key")).toHaveValue("keep-key");
    expect(calls.find((c) => c.body?.secrets)?.body.expected_revision).toBe(
      "config-1",
    );
    expect(dialog.getByRole("button", { name: "保存连接" })).toBeEnabled();
  });
  it("does not save twice while a connection request is pending", async () => {
    const { fetch } = setup();
    await screen.findByText("writer-a");
    let finish!: (r: Response) => void;
    fetch.mockImplementationOnce(
      () =>
        new Promise<Response>((r) => {
          finish = r;
        }),
    );
    fireEvent.click(screen.getAllByRole("button", { name: /^配置连接 / })[0]);
    const dialog = within(screen.getByRole("dialog"));
    const save = dialog.getByRole("button", { name: "保存连接" });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(dialog.getByRole("button", { name: "保存中…" })).toBeDisabled();
    expect(fetch).toHaveBeenCalledTimes(2);
    await act(async () =>
      finish(
        new Response(JSON.stringify({ error: "fixture failure" }), {
          status: 500,
        }),
      ),
    );
    expect(await dialog.findByRole("alert")).toHaveTextContent(
      "fixture failure",
    );
  });
  it("changes grouped usage explicitly and preserves an unsaved draft across route mounts", async () => {
    const { view, client, calls } = setup();
    await roleTab();
    await selectControl(screen.getByLabelText("文本创作模型"), "critic");
    fireEvent.click(screen.getByRole("tab", { name: "服务连接" }));
    expect(screen.getByRole("button", { name: "添加服务" })).toBeDisabled();
    view.unmount();
    render(
      <QueryClientProvider client={client}>
        <Models notify={vi.fn()} />
      </QueryClientProvider>,
    );
    await roleTab();
    expect(nativeControl(screen.getByLabelText("文本创作模型"))).toHaveValue(
      "critic",
    );
    fireEvent.click(screen.getByRole("button", { name: "保存分工" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "保存分工" })).toBeDisabled(),
    );
    const payload = calls.find((c) => c.body?.expected_revision)?.body;
    expect(payload.roles).toMatchObject({
      screenwriter: "critic",
      independent_critic: "critic",
      visual_critic: "writer",
    });
    expect(payload.expected_revision).toBe("config-1");
  });
  it("preserves custom per-role routing until the user changes that group", async () => {
    const { update } = setup();
    await screen.findByText("writer-a");
    update({
      roles: [
        ...inventory().roles,
        { role: "researcher", label: "选题研究", profile: "critic" },
      ],
      role_assignments: {
        ...inventory().role_assignments,
        researcher: "critic",
      },
    });
    await roleTab();
    expect(nativeControl(screen.getByLabelText("文本创作模型"))).toHaveValue(
      "__mixed__",
    );
    expect(screen.getByRole("button", { name: "保存分工" })).toBeDisabled();
    fireEvent.click(screen.getByText("按角色单独配置 · 总制片代理"));
    await selectControl(screen.getByLabelText("总制片代理"), "critic");
    expect(nativeControl(screen.getByLabelText("文本创作模型"))).toHaveValue(
      "__mixed__",
    );
  });
  it("keeps failed role saves and original revisions instead of overwriting newer configuration", async () => {
    const { update, calls } = setup();
    await roleTab();
    await selectControl(screen.getByLabelText("文本创作模型"), "critic");
    update({ revision: "new-config" });
    fireEvent.click(screen.getByRole("button", { name: "保存分工" }));
    await waitFor(() =>
      expect(
        screen
          .getAllByRole("alert")
          .map((e) => e.textContent)
          .join(),
      ).toContain("配置已更新"),
    );
    expect(nativeControl(screen.getByLabelText("文本创作模型"))).toHaveValue(
      "critic",
    );
    expect(
      calls.find((c) => c.body?.expected_revision)?.body.expected_revision,
    ).toBe("config-1");
  });
  it("shows pending connection checks, prevents duplicates and discards stale results", async () => {
    const { fetch, update } = setup();
    await screen.findByText("writer-a");
    let finish!: (r: Response) => void;
    fetch.mockImplementationOnce(
      () =>
        new Promise<Response>((r) => {
          finish = r;
        }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /^查看模型 .*writer-a/ }),
    );
    const check = screen.getByRole("button", { name: "检查连接" });
    fireEvent.click(check);
    fireEvent.click(check);
    expect(screen.getByRole("button", { name: "检查中…" })).toBeDisabled();
    update({ revision: "new-config" });
    await act(async () =>
      finish(
        new Response(
          JSON.stringify({ status: "ready", message: "stale probe" }),
        ),
      ),
    );
    expect(screen.queryByText("stale probe")).not.toBeInTheDocument();
    expect(
      fetch.mock.calls.filter(([path]) => String(path).endsWith("/test")),
    ).toHaveLength(1);
  });
  it("does not relabel directory-only checks as generation success", async () => {
    setup();
    await screen.findByText("writer-a");
    fireEvent.click(
      screen.getByRole("button", { name: /^查看模型 .*writer-a/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "检查连接" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "目录跳过，连接尚未验证",
    );
    expect(screen.queryByText("连接通过")).not.toBeInTheDocument();
  });
  it("blocks removing an assigned model and supports explicit role reassignment", async () => {
    setup();
    await screen.findByText("writer-a");
    fireEvent.click(screen.getAllByRole("button", { name: /^配置连接 / })[0]);
    fireEvent.click(screen.getByRole("button", { name: "移除 writer-a" }));
    expect(screen.getByRole("alert")).toHaveTextContent("正在用于制作分工");
    expect(
      screen.getByText("writer-a", { selector: "#service-dialog strong" }),
    ).toBeVisible();
  });
});
it("keeps visual review separate even when an older inventory omits the vision role flag", async () => {
  const { update } = setup();
  await screen.findByText("writer-a");
  update({
    roles: inventory().roles.map((r) => ({ ...r, requires_vision: undefined })),
  });
  await roleTab();
  expect(nativeControl(screen.getByLabelText("视觉审核模型"))).toHaveValue(
    "writer",
  );
  await userEvent.click(screen.getByLabelText("视觉审核模型"));
  expect(screen.getByRole("option", { name: /critic-a/ })).toHaveAttribute(
    "data-disabled",
  );
  await userEvent.keyboard("{Escape}");
});
it("removes an unused service with one save and preserves assigned models", async () => {
  const { update, calls } = setup();
  await screen.findByText("writer-a");
  update({
    role_assignments: {
      screenwriter: "writer",
      independent_critic: "writer",
      visual_critic: "writer",
    },
  });
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: /^配置连接 兼容接口/ })
        .closest("article"),
    ).toHaveTextContent("未分配用途"),
  );
  fireEvent.click(screen.getByRole("button", { name: /^配置连接 兼容接口/ }));
  fireEvent.click(screen.getByText("高级连接设置"));
  fireEvent.click(screen.getByRole("button", { name: "移除服务" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  const saved = calls.find((c) => c.body?.expected_revision)?.body;
  expect(Object.keys(saved.profiles)).toEqual(["writer"]);
  expect(saved.roles.independent_critic).toBe("writer");
});

it("keeps services with the same provider and distinct endpoints as separate overview cards", async () => {
  const { view, update, calls } = setup();
  await screen.findByText("writer-a");
  const first = inventory().editable_profiles.writer;
  update({
    editable_profiles: {
      writer: first,
      remote: {
        ...first,
        model: "remote-writer",
        base_url: "https://other.example.test/models/writer/v1",
      },
    },
  });
  await screen.findByText("remote-writer");
  expect(
    view.container.querySelectorAll(
      '.model-service-card[data-service^="llm:"]',
    ),
  ).toHaveLength(2);
  expect(screen.getByText("other.example.test/models/writer/v1")).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(
    calls.filter((call) => call.body || call.path.endsWith("/test")),
  ).toHaveLength(0);
});

it("opens full service details on demand and closes the drawer before editing the same connection", async () => {
  const user = userEvent.setup(),
    { update, calls } = setup();
  await screen.findByText("writer-a");
  const profile = {
    ...inventory().editable_profiles.writer,
    tls_client_cert_env: "FIXTURE_CERT",
    tls_client_key_env: "FIXTURE_KEY",
    tls_ca_file_env: "FIXTURE_CA",
    base_url: "https://inference.example.test/models/writer/v1",
  };
  update({ editable_profiles: { writer: profile } });
  const opener = await screen.findByRole("button", {
    name: /^查看模型 .*inference.example.test/,
  });
  expect(screen.queryByText("双向 TLS 身份已关联")).not.toBeInTheDocument();
  await user.click(opener);
  const drawer = screen.getByRole("dialog", { name: "服务详情" });
  expect(drawer).toHaveClass("service-details-drawer");
  expect(within(drawer).getByText(profile.base_url)).toBeVisible();
  expect(within(drawer).getByText("双向 TLS 身份已关联")).toBeVisible();
  expect(
    within(drawer).getByRole("button", { name: "检查连接" }),
  ).toBeVisible();
  expect(calls.filter((call) => call.body)).toHaveLength(0);
  await user.click(within(drawer).getByRole("button", { name: "管理服务" }));
  expect(
    screen.queryByRole("dialog", { name: "服务详情" }),
  ).not.toBeInTheDocument();
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  const editor = screen.getByRole("dialog", { name: "管理服务" });
  expect(within(editor).getByLabelText("服务 URL")).toHaveValue(
    profile.base_url,
  );
  await user.click(within(editor).getByRole("button", { name: "关闭" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
});

it("labels overview configuration truthfully without probing, and filters by usage or address", async () => {
  const { update, calls, view } = setup();
  await screen.findByText("writer-a");
  update({
    editable_profiles: {
      ...inventory().editable_profiles,
      critic: { ...inventory().editable_profiles.critic, enabled: false },
    },
  });
  await waitFor(() =>
    expect(
      view.container.querySelector('[data-service="llm:critic"]'),
    ).toHaveTextContent("已停用"),
  );
  expect(
    view.container.querySelector('[data-service="llm:writer"]'),
  ).toHaveTextContent("已配置");
  expect(screen.queryByText("连接成功")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索服务" }), {
    target: { value: "视觉审核" },
  });
  expect(view.container.querySelectorAll(".model-service-card")).toHaveLength(
    1,
  );
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索服务" }), {
    target: { value: "127.0.0.1:8000" },
  });
  expect(
    view.container.querySelector('[data-service="llm:critic"]'),
  ).toBeVisible();
  expect(
    view.container.querySelector('[data-service="llm:writer"]'),
  ).not.toBeInTheDocument();
  expect(
    calls.filter((call) => call.body || call.path.endsWith("/test")),
  ).toHaveLength(0);
});
