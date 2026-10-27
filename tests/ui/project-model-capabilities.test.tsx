import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import { ProjectModelCapabilities } from "../../ui/factory/src/ProjectModelCapabilities";

const snapshot = {
  version: "ai-studio.model-capabilities.v1",
  profiles: {
    image: {
      kind: "image",
      model: "Qwen-Image-2.1",
      reference_counts: [0, 1, 2, 3, 4],
      source: "repository_adapter_contract",
    },
    video: {
      kind: "video",
      model: "MiniMax-H3",
      reference_counts: [1],
      first_frame: true,
      last_frame: false,
      duration: { min: 1, max: 15 },
      source: "repository_adapter_contract",
    },
    audio: {
      kind: "audio",
      model: "Qwen3-TTS",
      reference_counts: [0],
      source: "repository_adapter_contract",
    },
  },
};
const state = {
  revision: 7,
  enabled: false,
  snapshot: null,
  history: [],
  can_adopt: true,
  blocked_reason: "",
};
function mount(
  data: any,
  post: (body: any) => Response | Promise<Response> = () => new Response("{}"),
) {
  const writes: any[] = [];
  const fetch = vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === "GET") return new Response(JSON.stringify(data));
    const body = JSON.parse(init.body as string);
    writes.push(body);
    return post(body);
  });
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ProjectModelCapabilities
        project={{ id: "012345abcdef", status: "paused" }}
      />
    </QueryClientProvider>,
  );
  return { writes, client, fetch };
}

it("requires explicit consent and authoritative server idle permission", async () => {
  const data = {
    ...state,
    can_adopt: false,
    blocked_reason: "已有模型请求结果未知，请先核对",
  };
  const { writes, client } = mount(data);
  const button = await screen.findByRole("button", {
    name: "启用本项目能力校验",
  });
  expect(button).toBeDisabled();
  expect(screen.getByRole("checkbox")).toBeDisabled();
  expect(screen.getByText(data.blocked_reason)).toBeVisible();
  expect(writes).toHaveLength(0);
  client.clear();
});

it("only adopts after confirmation and sends no generation or budget fields", async () => {
  const data: any = { ...state };
  const { writes, client } = mount(data, () => {
    Object.assign(data, { revision: 8, enabled: true, snapshot });
    return new Response(JSON.stringify(data));
  });
  const button = await screen.findByRole("button", {
    name: "启用本项目能力校验",
  });
  expect(button).toBeDisabled();
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(button);
  await screen.findByRole("button", { name: "更新本项目能力版本" });
  expect(writes).toHaveLength(1);
  expect(writes[0]).toEqual({
    expected_revision: 7,
    request_id: expect.any(String),
    confirmed: true,
  });
  expect(screen.getByText("视频 · MiniMax-H3")).toBeVisible();
  expect(screen.getAllByText(/不支持尾帧/).length).toBeGreaterThan(0);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  client.clear();
});

it("retains the identical operation on failure, then refresh requires new consent", async () => {
  const data: any = { ...state };
  let first = true;
  const { writes, client } = mount(data, () => {
    if (first) {
      first = false;
      return new Response(JSON.stringify({ error: "响应中断，请核实" }), {
        status: 409,
      });
    }
    Object.assign(data, { revision: 8, enabled: true, snapshot });
    return new Response("{}");
  });
  await screen.findByRole("button", { name: "启用本项目能力校验" });
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(
    screen.getByRole("button", { name: "启用本项目能力校验" }),
  );
  await screen.findByText("响应中断，请核实");
  expect(screen.getByRole("checkbox")).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "重试此操作" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[0]).toEqual(writes[1]);
  await screen.findByRole("button", { name: "更新本项目能力版本" });
  client.clear();
});

it("reads actual frozen history without manufacturing an adoption", async () => {
  const { writes, client } = mount({
    ...state,
    enabled: true,
    snapshot,
    history: [
      {
        ...snapshot,
        profiles: {
          ...snapshot.profiles,
          video: { ...snapshot.profiles.video, model: "Wan2.2-TI2V-5B" },
        },
      },
    ],
  });
  await screen.findByRole("button", { name: "更新本项目能力版本" });
  await userEvent.click(screen.getByText("历史能力版本（1）"));
  await userEvent.click(screen.getByText("历史版本 1"));
  expect(screen.getByText("视频 · Wan2.2-TI2V-5B")).toBeVisible();
  expect(screen.getByText("视频 · MiniMax-H3")).toBeVisible();
  expect(writes).toHaveLength(0);
  client.clear();
});

it("requires fresh consent when the observed project revision changes", async () => {
  const data = { ...state };
  const { writes, client } = mount(data);
  const button = await screen.findByRole("button", {
    name: "启用本项目能力校验",
  });
  await userEvent.click(screen.getByRole("checkbox"));
  expect(button).toBeEnabled();
  client.setQueryData(["project-model-capabilities", "012345abcdef"], {
    ...data,
    revision: 8,
  });
  await waitFor(() => expect(button).toBeDisabled());
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(writes).toHaveLength(0);
  client.clear();
});

it("does not infer a legacy contract when the capability read fails", async () => {
  const fetch = vi.fn(
    async () =>
      new Response(JSON.stringify({ error: "读取失败" }), { status: 500 }),
  );
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ProjectModelCapabilities project={{ id: "012345abcdef" }} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/尚不能判断当前采用版本/)).toBeVisible();
  expect(screen.queryByText(/本项目沿用原制作合约/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "启用本项目能力校验" }),
  ).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledOnce();
  client.clear();
});
