import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { MediaServiceEditor } from "../../ui/factory/src/MediaServiceEditor";
import { WorkflowTools } from "../../ui/factory/src/WorkflowTools";
import type { RecordData } from "../../ui/factory/src/api";
const h3 = {
  provider: "comfyui_h3",
  model: "MiniMax-H3",
  enabled: true,
  quality: "production",
  steps: 20,
  shift_video: 12,
  shift_audio: 3,
  sampler: "euler",
  revision: "wkp-dualgpu-base-v1",
};
const inventory = {
  revision: "one",
  editable_profiles: {},
  roles: [],
  video_profiles: { h3 },
  video_roles: { video_generator: "h3" },
  video_presets: { comfyui_h3: h3 },
  video_providers: [{ id: "comfyui_h3", label: "ComfyUI H3" }],
};
it("saves the selected H3 production recipe once and preserves stable assignment", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(
    <MediaServiceEditor
      kind="video"
      inventory={inventory}
      service={{
        id: "video:h3",
        kind: "video",
        provider: "comfyui_h3",
        profiles: [["h3", h3]],
      }}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("采样步数"), {
    target: { value: "28" },
  });
  fireEvent.click(screen.getByText("采样参数"));
  fireEvent.change(screen.getByLabelText("视频采样偏移"), {
    target: { value: "10.5" },
  });
  fireEvent.change(screen.getByLabelText("采样器"), {
    target: { value: "heun" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onSave.mock.calls[0][0].video_profiles.h3).toMatchObject({
    steps: 28,
    shift_video: 10.5,
    shift_audio: 3,
    sampler: "heun",
    revision: "wkp-dualgpu-base-v1",
  });
  expect(onSave.mock.calls[0][0].video_roles.video_generator).toBe("h3");
  expect(onSave.mock.calls[0][1]).toBe("one");
});
it("switches H3 to the fixed preview recipe without inventing an API key", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(
    <MediaServiceEditor
      kind="video"
      inventory={inventory}
      service={{
        id: "video:h3",
        kind: "video",
        provider: "comfyui_h3",
        profiles: [["h3", h3]],
      }}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("H3 质量档"), {
    target: { value: "preview" },
  });
  expect(screen.queryByLabelText("采样步数")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onSave.mock.calls[0][0].video_profiles.h3).toMatchObject({
    quality: "preview",
    steps: 4,
    shift_video: 12,
    sampler: "euler",
    revision: "wkp-dualgpu-turbo4-v1",
  });
});
it("retains cloud speech fields and posts a write-only key in one save", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const preset = {
    provider: "dashscope_tts",
    model: "qwen3-tts-flash",
    enabled: true,
    base_url: "https://dashscope.aliyuncs.com/api/v1",
    api_key_env: "DASHSCOPE_API_KEY",
    default_voice: "Cherry",
    language: "Chinese",
    voices: { narrator: "Cherry" },
  };
  render(
    <MediaServiceEditor
      kind="audio"
      inventory={{
        ...inventory,
        audio_profiles: {},
        audio_roles: {},
        media_presets: { audio: { dashscope_tts: preset } },
        media_providers: { dashscope_tts: { label: "百炼语音 API" } },
      }}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "百炼语音 API" }));
  fireEvent.change(screen.getByLabelText("API Key"), {
    target: { value: "fake-speech-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  const draft = onSave.mock.calls[0][0],
    id = Object.keys(draft.audio_profiles)[0];
  expect(draft.audio_profiles[id]).toMatchObject({
    ...preset,
    voices: { narrator: "Cherry" },
  });
  expect(draft.secrets).toEqual({ ["audio:" + id]: "fake-speech-key" });
  expect(JSON.stringify(draft.audio_profiles)).not.toContain("fake-speech-key");
});
it("keeps media save errors in the editor and rejects duplicate pending saves", async () => {
  let finish!: () => void;
  const onSave = vi.fn(
    () =>
      new Promise<void>((_, reject) => {
        finish = () => reject(new Error("stale revision"));
      }),
  );
  render(
    <MediaServiceEditor
      kind="video"
      inventory={inventory}
      service={{
        id: "video:h3",
        kind: "video",
        provider: "comfyui_h3",
        profiles: [["h3", h3]],
      }}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );
  const save = screen.getByRole("button", { name: "保存连接" });
  fireEvent.click(save);
  fireEvent.click(save);
  expect(onSave).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "保存中…" })).toBeDisabled();
  await act(async () => finish());
  expect(screen.getByRole("alert")).toHaveTextContent("stale revision");
  expect(screen.getByLabelText("模型 ID")).toHaveValue("MiniMax-H3");
});
it("imports workflow templates separately without saving or generating model configuration", async () => {
  const fetch = vi.fn(
    async (_path: any, _init?: RequestInit) =>
      new Response(JSON.stringify({ id: "a".repeat(64) })),
  );
  vi.stubGlobal("fetch", fetch);
  render(<WorkflowTools notify={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("工作流模板 JSON"), {
    target: { value: JSON.stringify({ kind: "image", graph: { node: {} } }) },
  });
  fireEvent.click(screen.getByRole("button", { name: "导入模板" }));
  await waitFor(() =>
    expect(screen.getByLabelText("模板编号")).toHaveValue("a".repeat(64)),
  );
  expect(fetch).toHaveBeenCalledOnce();
  expect(fetch.mock.calls[0][0]).toBe("/api/factory/workflow-templates");
});

it("preserves an untouched legacy H3 preview cache identity", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const legacy = {
    provider: "comfyui_h3",
    model: "MiniMax-H3",
    enabled: true,
    revision: "wkp-dualgpu-turbo4-v1",
  };
  render(
    <MediaServiceEditor
      kind="video"
      inventory={inventory}
      service={{
        id: "video:h3",
        kind: "video",
        provider: "comfyui_h3",
        profiles: [["h3", legacy]],
      }}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("H3 质量档")).toHaveValue("preview");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onSave.mock.calls[0][0].video_profiles.h3).toEqual(legacy);
});
