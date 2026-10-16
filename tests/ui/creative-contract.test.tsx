import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { CreateProject } from "../../ui/factory/src/App";
import { CreativeContract } from "../../ui/factory/src/CreativeContract";
import type { RecordData } from "../../ui/factory/src/api";

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify({ project: { id: "fedcba654321" } }), {
        status: 201,
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
});
function mount(seed?: RecordData) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CreateProject
        catalog={{ presets: { compact6: { label: "紧凑短片 · 6 镜" } } }}
        inventory={{ role_assignments: { executive_producer: "producer" } }}
        seed={seed}
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

it("defaults to actual 2D video generation and an explicit standalone short drama", () => {
  mount();
  expect(screen.getByLabelText("画面与运动方式")).toHaveValue("anime_2d");
  expect(screen.getByText("单部闭环短剧")).toBeVisible();
  expect(screen.getByText(/当前流程不承诺同步口型/)).toBeVisible();
  expect(fetchMock).not.toHaveBeenCalled();
});

it.each([
  ["anime_2d", "animation", "generated_video"],
  ["stylized_3d", "animation", "generated_video"],
  ["photorealistic", "live_action", "generated_video"],
  ["illustrated", "motion_comic", "still_camera"],
])(
  "submits %s with the matching format and motion contract",
  async (style, format, motion) => {
    const user = userEvent.setup();
    mount();
    await user.selectOptions(screen.getByLabelText("画面与运动方式"), style);
    await user.type(
      screen.getByLabelText("创作方向"),
      "成年修表师主动找出真相，最终完成和解。",
    );
    if (motion === "still_camera")
      expect(screen.getByText(/不生成角色动作或同步口型/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "开始自主策划" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/factory/projects");
    expect(JSON.parse(init.body)).toMatchObject({
      format,
      creative_contract: {
        schema_version: 1,
        visual_style: style,
        story_mode: "standalone",
        pacing: "short_drama",
        motion,
      },
    });
  },
);

it("seeds a separate remake form without changing or posting the existing project", async () => {
  const seed = {
    id: "012345abcdef",
    title: "旧版作品",
    brief: "这是原作品的创作目标。",
    format: "motion_comic",
    delivery: { video_url: "/media/old.mp4" },
    revision: 23,
  };
  const snapshot = structuredClone(seed);
  mount(seed);
  expect(screen.getByLabelText("项目名称 / 可选")).toHaveValue(
    "旧版作品 · 重制",
  );
  expect(screen.getByLabelText("创作方向")).toHaveValue(seed.brief);
  expect(screen.getByLabelText("画面与运动方式")).toHaveValue("anime_2d");
  expect(
    screen.getByText(/原项目的成片、素材与历史记录继续保留/),
  ).toBeVisible();
  await userEvent.type(screen.getByLabelText("创作方向"), "新的修改要求。");
  expect(seed).toEqual(snapshot);
  expect(fetchMock).not.toHaveBeenCalled();
});

it("preserves an explicit 3D choice when seeding a new remake", () => {
  mount({
    title: "三维故事",
    brief: "原始创作方向。",
    creative_contract: { visual_style: "stylized_3d" },
  });
  expect(screen.getByLabelText("画面与运动方式")).toHaveValue("stylized_3d");
});

it("reports camera-only receipts truthfully even when requested style is 2D animation", () => {
  render(
    <CreativeContract
      project={{
        format: "animation",
        creative_contract: {
          visual_style: "anime_2d",
          story_mode: "standalone",
          motion: "generated_video",
        },
        shots: [{ video: { origin: "procedural_camera_motion" } }],
      }}
    />,
  );
  expect(screen.getByText(/实际制作：静帧图片加程序运镜/)).toBeVisible();
  expect(
    screen.queryByText(/实际制作：模型生成运动视频/),
  ).not.toBeInTheDocument();
});

it("does not invent old animation style or successful character animation", () => {
  render(
    <CreativeContract
      project={{
        format: "animation",
        shots: [{ video_url: "/media/old.mp4" }],
      }}
    />,
  );
  expect(screen.getByText("动画 · 旧项目未声明画风")).toBeVisible();
  expect(screen.getByText(/尚无可确认的镜头制作回执/)).toBeVisible();
});

it("shows delivery provenance including unknown shots without hiding camera-only segments", () => {
  render(
    <CreativeContract
      project={{
        format: "animation",
        creative_contract: { visual_style: "stylized_3d" },
        delivery: {
          motion_provenance: {
            generated_video_shots: 3,
            still_camera_shots: 1,
            unknown_shots: 2,
          },
        },
      }}
    />,
  );
  expect(screen.getByText(/混合制作：部分镜头为生成视频/)).toBeVisible();
  expect(
    screen.getByText("生成视频 3 镜 · 静帧运镜 1 镜 · 未知 2 镜"),
  ).toBeVisible();
});
