import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import {
  TimelineEditor,
  moveEditClip,
  splitEditClip,
  deleteEditClip,
  editPositions,
  type EditDocument,
} from "../../ui/factory/src/TimelineEditor";
import { request } from "../../ui/factory/src/api";
vi.mock("../../ui/factory/src/api", () => ({ request: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function document(): EditDocument {
  return {
    schema: "ai-studio.edit.v1",
    fps: 24,
    resolution: [160, 288],
    confirmed_duration_frames: 144,
    caption_text_confirmed: false,
    clips: [
      {
        id: "c1",
        source_id: "s1",
        in_frame: 0,
        out_frame: 72,
        source_gain: 1,
        transition: { kind: "cut", frames: 0 },
      },
      {
        id: "c2",
        source_id: "s2",
        in_frame: 0,
        out_frame: 72,
        source_gain: 1,
        transition: { kind: "cut", frames: 0 },
      },
    ],
    audio: [
      {
        id: "a1",
        source_id: "voice",
        in_frame: 0,
        out_frame: 24,
        start_frame: 12,
        gain: 1,
        anchor_clip_id: "c1",
      },
    ],
    subtitles: [
      {
        id: "s1",
        text: "原字幕",
        source_text: "原字幕",
        start_frame: 12,
        end_frame: 36,
        anchor_clip_id: "c1",
      },
    ],
  };
}
it("reordering moves linked voice and caption together without mutating original", () => {
  const original = document(),
    next = moveEditClip(original, 0, 1);
  expect(next.clips.map((c) => c.id)).toEqual(["c2", "c1"]);
  expect(next.audio[0].start_frame).toBe(84);
  expect(next.subtitles[0].start_frame).toBe(84);
  expect(original.audio[0].start_frame).toBe(12);
});
it("split preserves source frame intervals and clip order", () => {
  const original = document(),
    next = splitEditClip(original, 0, 24);
  expect(next.clips.map((c) => [c.in_frame, c.out_frame])).toEqual([
    [0, 24],
    [24, 72],
    [0, 72],
  ]);
  expect(next.clips[0].source_id).toBe(next.clips[1].source_id);
  expect(editPositions(next.clips).at(-1)?.end).toBe(144);
  expect(original.clips).toHaveLength(2);
});
it("deleting a clip removes its linked tracks but protects final remaining clip", () => {
  const next = deleteEditClip(document(), 0);
  expect(next.audio).toHaveLength(0);
  expect(next.subtitles).toHaveLength(0);
  expect(next.clips[0].id).toBe("c2");
  expect(() => deleteEditClip(next, 0)).toThrow("至少保留");
});
it("keeps formal export disabled until current checks pass and saves explicit new versions", async () => {
  const user = userEvent.setup(),
    doc = document();
  const data = {
    project_id: "abc",
    revision: 7,
    editable: true,
    versions: [],
    catalog: {
      default: doc,
      sources: {
        s1: { global_shot_id: "S01", media_url: "/fixture1.mp4" },
        s2: { global_shot_id: "S02", media_url: "/fixture2.mp4" },
        voice: { kind: "voice", media_url: "/voice.wav" },
      },
    },
  };
  vi.mocked(request).mockImplementation(async (_path, body) => {
    if (body)
      return {
        ...data,
        revision: 8,
        versions: [
          {
            id: "version1",
            document: (body as any).document,
            structural: { status: "pass" },
            semantic: { status: "not_reviewed" },
            source_current: true,
          },
        ],
      } as any;
    return data as any;
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TimelineEditor projectId="abc" />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByRole("heading", { name: "时间线剪辑" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "正式导出" })).toBeDisabled();
  await user.clear(screen.getByLabelText("拆分源帧 1"));
  await user.type(screen.getByLabelText("拆分源帧 1"), "24");
  await user.click(screen.getAllByRole("button", { name: "按指定帧拆分" })[0]);
  await user.click(screen.getByRole("button", { name: "保存为新版本" }));
  expect(
    vi.mocked(request).mock.calls.find((c) => Boolean(c[1]))?.[1],
  ).toMatchObject({
    action: "save",
    expected_revision: 7,
    document: {
      ...doc,
      clips: [
        expect.objectContaining({ in_frame: 0, out_frame: 24 }),
        expect.objectContaining({ in_frame: 24, out_frame: 72 }),
        expect.objectContaining({ in_frame: 0, out_frame: 72 }),
      ],
    },
  });
  expect(screen.getByRole("button", { name: "正式导出" })).toBeDisabled();
});
