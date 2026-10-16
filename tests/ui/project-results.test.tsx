import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import {
  ProjectResultActions,
  currentResultPage,
} from "../../ui/factory/src/ProjectResultActions";

const id = "012345abcdef";
const video = `/media/production/factory-${id}/release/episode.mp4`;

it("opens the latest available work and downloads the existing project video", async () => {
  const onOpen = vi.fn();
  render(
    <ProjectResultActions
      project={{ id, delivery: { video_url: video } }}
      onOpen={onOpen}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "查看作品" }));
  expect(onOpen).toHaveBeenCalledWith("delivery");
  expect(screen.getByRole("link", { name: "下载成片" })).toHaveAttribute(
    "href",
    expect.stringContaining(video),
  );
  expect(screen.getByRole("link", { name: "下载成片" })).toHaveAttribute(
    "download",
  );
});

it.each([
  undefined,
  "/media/production/factory-aaaaaaaaaaaa/episode.mp4",
  "https://other.example/video.mp4",
  `/media/production/factory-${id}/../episode.mp4`,
])("does not offer an unavailable or foreign download: %s", (video_url) => {
  render(
    <ProjectResultActions
      project={{ id, delivery: { video_url } }}
      onOpen={vi.fn()}
    />,
  );
  expect(
    screen.queryByRole("link", { name: "下载成片" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "下载成片" })).toBeDisabled();
  expect(screen.getByText("成片尚未生成")).toBeVisible();
});

it("routes unfinished work to its available shot, script, or story without inventing a delivery", () => {
  expect(
    currentResultPage({
      id,
      script: { title: "剧本" },
      shots: [{ image_url: video.replace(".mp4", ".png") }],
    }),
  ).toBe("shots");
  expect(currentResultPage({ id, shots: [{ video: { path: video } }] })).toBe(
    "shots",
  );
  expect(currentResultPage({ id, script: { title: "剧本" } })).toBe("script");
  expect(
    currentResultPage({
      id,
      script: { title: "剧本" },
      styles: [{ id: "style-A" }],
    }),
  ).toBe("decisions");
  expect(currentResultPage({ id })).toBe("decisions");
});
