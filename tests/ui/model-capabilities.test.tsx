import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { MediaServiceEditor } from "../../ui/factory/src/MediaServiceEditor";
import { ModelCapabilities } from "../../ui/factory/src/ModelCapabilities";
import { selectControl } from "./select-control";

const profile = {
  provider: "minimax",
  model: "MiniMax-Hailuo-2.3",
  enabled: true,
  base_url: "https://api.minimax.io/v1",
  duration: 6,
  resolution: "768P",
  api_key_env: "MINIMAX_KEY",
};
const capability = {
  schema: "ai-studio.model-capabilities.v1",
  source: "repository_adapter_contract",
  reference_counts: [1],
  first_frame: true,
  last_frame: false,
  lip_sync: false,
  native_audio: false,
  duration: { min: 0.5, max: 6 },
  duration_options: [6, 10],
  resolution_options: ["768P", "1080P"],
  invalid_combinations: [{ duration: 10, resolution: "1080P" }],
  unknown: ["upstream_model_availability"],
};
const inventory = {
  revision: "one",
  editable_profiles: {},
  roles: [],
  video_profiles: { remote: profile },
  video_roles: { video_generator: "remote" },
  video_presets: { minimax: profile },
  video_providers: [{ id: "minimax", label: "MiniMax" }],
  model_capabilities: { video: { remote: capability } },
};

it("shows known limits and explicit unknowns without claiming generation verification", () => {
  const { rerender } = render(<ModelCapabilities value={capability} />);
  expect(screen.getByRole("region", { name: "模型能力" })).toHaveTextContent(
    "不支持尾帧",
  );
  expect(
    screen.getByText(/不代表在线状态或真实生成效果已验证/),
  ).toBeInTheDocument();
  expect(screen.getByText(/部分能力尚未验证/)).toBeInTheDocument();
  rerender(<ModelCapabilities />);
  expect(screen.getByText(/尚无能力记录/)).toBeInTheDocument();
});

it("uses server capability choices and saves explicit MiniMax parameters", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(
    <MediaServiceEditor
      kind="video"
      inventory={inventory}
      service={{
        id: "video:remote",
        kind: "video",
        provider: "minimax",
        profiles: [["remote", profile]],
      }}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );
  await selectControl(screen.getByLabelText("生成时长"), "10");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(onSave.mock.calls[0][0].video_profiles.remote).toMatchObject({
    duration: 10,
    resolution: "768P",
  });
  expect(onSave.mock.calls[0][0].video_profiles.remote).not.toHaveProperty(
    "model_capabilities",
  );
});

it("rejects an incompatible selected combination before saving", async () => {
  const onSave = vi.fn();
  render(
    <MediaServiceEditor
      kind="video"
      inventory={inventory}
      service={{
        id: "video:remote",
        kind: "video",
        provider: "minimax",
        profiles: [["remote", { ...profile, resolution: "1080P" }]],
      }}
      onSave={onSave}
      onClose={vi.fn()}
    />,
  );
  await selectControl(screen.getByLabelText("生成时长"), "10");
  fireEvent.click(screen.getByRole("button", { name: "保存连接" }));
  // Disabled controls cannot cause a hidden switch to another resolution.
  await waitFor(() =>
    expect(screen.getByText(/请明确选择可用组合/)).toBeInTheDocument(),
  );
  expect(onSave).not.toHaveBeenCalled();
});
