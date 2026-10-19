import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { DramaNextStep } from "../../ui/factory/src/DramaNextStep";

const blocked = {
  id: "012345abcdef",
  revision: 7,
  status: "blocked",
  stage: "plan_repair",
  plan_version: 0,
  plan_phase: "exhausted",
  plan_attempt: 3,
  plan_max_attempts: 3,
  plan_review: { passed: false, issues: ["第二集重复第一集"] },
  episodes: [],
};

it("explains why waiting cannot produce a video and directs the workflow to planning", async () => {
  const onTab = vi.fn(),
    repair = vi.fn();
  render(
    <DramaNextStep
      project={blocked}
      tab="workflow"
      busy={false}
      onTab={onTab}
      repair={repair}
    />,
  );
  expect(screen.getByText(/视频制作尚未开始/)).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "查看问题并修复规划" }),
  );
  expect(onTab).toHaveBeenCalledWith("plan");
  expect(repair).not.toHaveBeenCalled();
});

it("retains supplementary requirements after a failed operation and a project refresh", async () => {
  const repair = vi
      .fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true),
    onTab = vi.fn();
  const { rerender } = render(
    <DramaNextStep
      project={blocked}
      tab="plan"
      busy={false}
      onTab={onTab}
      repair={repair}
    />,
  );
  const input = screen.getByRole("textbox", {
    name: "补充本轮规划要求（可选）",
  });
  await userEvent.type(input, "第二集调查新线索，保留第一集结果。");
  await userEvent.click(screen.getByRole("button", { name: "修复规划并继续" }));
  expect(input).toHaveValue("第二集调查新线索，保留第一集结果。");
  rerender(
    <DramaNextStep
      project={{ ...blocked, revision: 8 }}
      tab="plan"
      busy={false}
      onTab={onTab}
      repair={repair}
    />,
  );
  expect(input).toHaveValue("第二集调查新线索，保留第一集结果。");
  await userEvent.click(screen.getByRole("button", { name: "修复规划并继续" }));
  expect(repair.mock.calls).toEqual([
    ["第二集调查新线索，保留第一集结果。"],
    ["第二集调查新线索，保留第一集结果。"],
  ]);
  expect(input).toHaveValue("");
});

it("does not offer another repair while the durable job is active", () => {
  render(
    <DramaNextStep
      project={{ ...blocked, workflow: { active_job: true } }}
      tab="plan"
      busy={false}
      onTab={vi.fn()}
      repair={vi.fn()}
    />,
  );
  expect(screen.getByRole("heading", { name: "正在规划与审核" })).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "修复规划并继续" }),
  ).not.toBeInTheDocument();
});

it("keeps automatic repair disabled while manual planning edits are unsubmitted", async () => {
  const repair = vi.fn();
  render(
    <DramaNextStep
      project={blocked}
      tab="plan"
      busy={false}
      manualChanges
      onTab={vi.fn()}
      repair={repair}
    />,
  );
  expect(screen.getByRole("button", { name: "修复规划并继续" })).toBeDisabled();
  expect(screen.getByText(/下方有未提交的规划修改/)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "修复规划并继续" }));
  expect(repair).not.toHaveBeenCalled();
});

it("guides approved planning into confirmation and then episode selection without approving for the user", async () => {
  const onTab = vi.fn(),
    repair = vi.fn();
  const { rerender } = render(
    <DramaNextStep
      project={{ ...blocked, status: "awaiting_plan", plan_phase: "approved" }}
      tab="overview"
      busy={false}
      onTab={onTab}
      repair={repair}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "检查并确认规划" }));
  expect(onTab).toHaveBeenLastCalledWith("plan");
  rerender(
    <DramaNextStep
      project={{
        ...blocked,
        status: "ready",
        stage: "episodes",
        plan_version: 1,
        episodes: [{ status: "draft" }],
      }}
      tab="overview"
      busy={false}
      onTab={onTab}
      repair={repair}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "选择剧集并设置预算" }),
  );
  expect(onTab).toHaveBeenLastCalledWith("episodes");
  expect(repair).not.toHaveBeenCalled();
});
