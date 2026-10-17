import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShotVersions } from "../../ui/factory/src/ShotVersions";
import type { RecordData } from "../../ui/factory/src/api";

afterEach(cleanup);
const projectId = "012345abcdef";
function candidate(
  take: string,
  stage = "video",
  patch: RecordData = {},
): RecordData {
  return {
    id: stage + ":" + take,
    take,
    stage,
    status: "passed",
    selectable: true,
    plan_current: true,
    model: "fixture-model",
    qa: { eligible_for_selection: true },
    media_url: `/media/production/factory-${projectId}/assets/${stage === "video" ? "videos" : "images"}/S01/${take}/${stage === "video" ? "movie.mp4" : "frame.png"}`,
    ...patch,
  };
}
function props(candidates: RecordData[]) {
  return {
    projectId,
    candidates,
    revision: 17,
    disabled: false,
    onSubmit: vi.fn(),
  };
}
describe("shot preview and compact version history", () => {
  it("shows chosen video and current keyframe while historical takes remain compact", () => {
    const versions = [
      candidate("take_003"),
      candidate("take_002", "video", { producer_selected: true }),
      candidate("take_001", "video", { plan_current: false }),
      candidate("take_002", "image"),
      candidate("take_001", "image"),
    ];
    const view = render(<ShotVersions {...props(versions)} />);
    expect(view.container.querySelectorAll(".candidate-card")).toHaveLength(2);
    expect(
      view.container
        .querySelector(".candidate-card")
        ?.getAttribute("data-candidate-id"),
    ).toBe("video:take_002");
    expect(view.container.querySelectorAll("[data-shot-version]")).toHaveLength(
      5,
    );
    expect(
      screen.getByRole("button", { name: "查看 视频 take_003" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "对比所选版本" })).toBeDisabled();
  });

  it("compares exactly two explicit choices without saving or generating, then returns to current media", async () => {
    const user = userEvent.setup(),
      input = props([
        candidate("take_003"),
        candidate("take_002"),
        candidate("take_001", "image"),
      ]);
    const view = render(<ShotVersions {...input} />);
    await user.click(
      screen.getByRole("checkbox", { name: "对比 视频 take_003" }),
    );
    await user.click(
      screen.getByRole("checkbox", { name: "对比 视频 take_002" }),
    );
    expect(
      screen.getByRole("checkbox", { name: "对比 首帧 take_001" }),
    ).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "对比所选版本（2/2）" }),
    );
    expect(
      view.container.querySelector('[data-shot-preview="compare"]'),
    ).toBeInTheDocument();
    expect(
      view.container.querySelectorAll(".candidate-card video"),
    ).toHaveLength(2);
    expect(
      view.container.querySelector(".candidate-card img"),
    ).not.toBeInTheDocument();
    expect(input.onSubmit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "返回当前素材" }));
    expect(
      view.container.querySelectorAll(".candidate-card video"),
    ).toHaveLength(1);
    expect(
      view.container.querySelector(".candidate-card img"),
    ).toBeInTheDocument();
  });

  it("exposes current failure and old-preview provenance without pretending a video is regenerated", async () => {
    const user = userEvent.setup(),
      input = props([
        candidate("take_003", "video", {
          media_url: null,
          status: "failed",
          selectable: false,
          error: "首帧连接审核失败",
        }),
        candidate("take_002", "video", { plan_current: false }),
      ]);
    const view = render(<ShotVersions {...input} />);
    expect(
      view.container.querySelector(".candidate-card video"),
    ).toBeInTheDocument();
    expect(screen.getByText(/预览中包含历史参数素材/)).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "当前参数审核" })).getByText(
        "首帧连接审核失败",
      ),
    ).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "查看 视频 take_003" }),
    );
    expect(
      screen.getByText("首帧连接审核失败", { selector: ".candidate-review p" }),
    ).toBeVisible();
    expect(
      screen.getByRole("checkbox", { name: /确认采用此候选/ }),
    ).toBeDisabled();
    expect(screen.getByText("尚无可预览素材")).toBeVisible();
    expect(input.onSubmit).not.toHaveBeenCalled();
  });

  it("keeps playing video, selection consent and revision guard through polling and comparison-list changes", async () => {
    const user = userEvent.setup(),
      versions = [candidate("take_002"), candidate("take_001")],
      input = props(versions);
    const view = render(<ShotVersions {...input} />),
      video = view.container.querySelector("video")!;
    video.currentTime = 1.25;
    const consent = screen.getByRole("checkbox", { name: /确认采用此候选/ });
    await user.click(consent);
    await user.click(
      screen.getByRole("checkbox", { name: "对比 视频 take_001" }),
    );
    view.rerender(
      <ShotVersions
        {...input}
        candidates={versions.map((item) => ({ ...item }))}
        revision={18}
      />,
    );
    expect(view.container.querySelector("video")).toBe(video);
    expect(video.currentTime).toBe(1.25);
    expect(consent).toBeChecked();
    expect(screen.getByRole("button", { name: "设为选定素材" })).toBeDisabled();
    await user.click(consent);
    await user.click(consent);
    await user.click(screen.getByRole("button", { name: "设为选定素材" }));
    expect(input.onSubmit).toHaveBeenCalledExactlyOnceWith({
      action: "select",
      stage: "video",
      take: "take_002",
      confirmed: true,
      expected_revision: 18,
    });
  });

  it("handles a missing compared version, image-only boards, and empty boards", async () => {
    const user = userEvent.setup(),
      input = props([candidate("take_002"), candidate("take_001")]);
    const view = render(<ShotVersions {...input} />);
    await user.click(
      screen.getByRole("checkbox", { name: "对比 视频 take_002" }),
    );
    await user.click(
      screen.getByRole("checkbox", { name: "对比 视频 take_001" }),
    );
    await user.click(
      screen.getByRole("button", { name: "对比所选版本（2/2）" }),
    );
    view.rerender(
      <ShotVersions {...input} candidates={[candidate("take_001", "image")]} />,
    );
    expect(
      view.container.querySelector('[data-shot-preview="compare"]'),
    ).not.toBeInTheDocument();
    expect(view.container.querySelector("img")).toBeInTheDocument();
    expect(view.container.querySelector("video")).not.toBeInTheDocument();
    view.rerender(<ShotVersions {...input} candidates={[]} />);
    expect(screen.getByText("此镜头尚无候选")).toBeVisible();
  });

  it("keeps full parameters and raw review evidence collapsed until requested", async () => {
    const user = userEvent.setup(),
      input = props([
        candidate("take_001", "video", {
          qa: {
            semantic: {
              status: "fail",
              summary: "动作接触位置不一致",
              diagnostic: "底层审核依据",
            },
          },
          parameters: { sampling_note: "底层采样配置" },
          selectable: false,
        }),
      ]);
    render(<ShotVersions {...input} />);
    const summary = screen.getByLabelText("审核结论");
    expect(within(summary).getByText("动作接触位置不一致")).toBeVisible();
    expect(screen.getByText("底层采样配置")).not.toBeVisible();
    await user.click(screen.getByText("评分、参数与失败原因"));
    expect(screen.getByText("底层采样配置")).toBeVisible();
    expect(screen.getByText("底层审核依据")).toBeVisible();
  });
});

it("distinguishes unfinished semantic review from a failed review", () => {
  const input = props([
    candidate("take_001", "video", {
      status: "semantic_pending",
      selectable: false,
      qa: {
        technical: { status: "pass" },
        semantic: { status: "not_run" },
        eligible_for_selection: false,
      },
    }),
  ]);
  render(<ShotVersions {...input} />);
  expect(screen.getByText("画面审核尚未完成，等待审核结论。")).toBeVisible();
  expect(screen.queryByText(/此版本未通过审核/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "设为选定素材" })).toBeDisabled();
});
