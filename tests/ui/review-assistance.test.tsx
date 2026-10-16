import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { DecisionCenter } from "../../ui/factory/src/Decisions";
import type { RecordData } from "../../ui/factory/src/api";

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi
    .fn()
    .mockImplementation(() =>
      Promise.resolve(new Response("{}", { status: 200 })),
    );
  vi.stubGlobal("fetch", fetchMock);
});
const proposal = {
  recommendation: "repair",
  rationale: "问题集中在 S03 的动机与 S05 的衔接，可在原故事内修复。",
  feedback:
    "补齐 S03 的行动理由，修正 S05 道具状态，保留其余镜头并重新独立审核。",
  risks: ["修改动作后要重新检查对白时长"],
  decisions: [],
};
function project(advice?: RecordData): RecordData {
  return {
    id: "012345abcdef",
    revision: 8,
    status: "blocked",
    stage: "script_review",
    review_automation: {
      phase: "script",
      status: "blocked",
      round: 4,
      max_rounds: 4,
      automatic_repairs: 3,
    },
    operator: {
      attention: {
        blocking: true,
        kind: "creative",
        title: "审核未通过，请选择处理方式",
        review_advice: advice,
        actions: [
          {
            id: "request_review_advice",
            label: "请 AI 分析并推荐",
            enabled: true,
            inputs: [],
          },
          {
            id: "provide_feedback",
            label: "提交意见并重新审核",
            enabled: true,
            inputs: [
              {
                name: "feedback",
                label: "修改要求",
                type: "textarea",
                required: true,
                maxlength: 4000,
                choices: [
                  {
                    id: "motivation",
                    label: "补齐人物动机",
                    kind: "direction",
                    feedback: "只补齐人物动机，保持其余设定。",
                  },
                ],
              },
            ],
          },
        ],
      },
    },
  };
}
function mount(p: RecordData) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DecisionCenter project={p} onNotify={vi.fn()} onTab={vi.fn()} />
    </QueryClientProvider>,
  );
}
function sent() {
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  return { url, body: JSON.parse(init.body) };
}

it("shows active automatic repair instead of asking for human feedback", () => {
  const p = project();
  p.operator.attention = null;
  p.status = "scripting";
  p.review_automation = {
    phase: "script",
    status: "writing",
    round: 3,
    max_rounds: 4,
    automatic_repairs: 2,
  };
  mount(p);
  expect(screen.getByText(/正在按审核意见返修/)).toBeVisible();
  expect(screen.getByText(/第 3 \/ 4 轮审核/)).toBeVisible();
  expect(screen.getByText("当前没有人工待办")).toBeVisible();
  expect(fetchMock).not.toHaveBeenCalled();
});

it("requests AI analysis through the queued server action without submitting repairs", async () => {
  const user = userEvent.setup();
  mount(project());
  expect(fetchMock).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "请 AI 分析并推荐" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(sent()).toEqual({
    url: "/api/factory/projects/012345abcdef/action",
    body: { action: "request_review_advice", expected_revision: 8 },
  });
});

it("continues available automatic rounds using the original checkpoint command", async () => {
  const user = userEvent.setup(),
    p = project();
  p.operator.attention.actions.push({
    id: "continue_auto_review",
    label: "继续自动返修",
    enabled: true,
    inputs: [],
  });
  mount(p);
  await user.click(screen.getByRole("button", { name: "继续自动返修" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(sent().body).toEqual({
    action: "continue_auto_review",
    expected_revision: 8,
  });
});

it("shows an unresolved prior request as a manual choice instead of an active model call", () => {
  mount(
    project({
      status: "manual",
      current: true,
      reason_code: "previous_request_unresolved",
    }),
  );
  expect(screen.getByText(/上次 AI 请求的结果尚未确认/)).toBeVisible();
  expect(screen.queryByText("AI 正在分析")).not.toBeInTheDocument();
});

it("applies current AI advice through ordinary versioned feedback only after confirmation", async () => {
  const user = userEvent.setup();
  mount(
    project({
      status: "ready",
      current: true,
      requested_model: "test-producer",
      proposal,
    }),
  );
  expect(screen.getByText(proposal.rationale)).toBeVisible();
  expect(screen.getByText(proposal.risks[0])).toBeVisible();
  expect(fetchMock).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "采纳 AI 方案并继续返修" }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  expect(sent().body).toEqual({
    action: "provide_feedback",
    feedback: proposal.feedback,
    expected_revision: 8,
  });
});

it("keeps user edits and requires clearing them before applying a different AI plan", async () => {
  const user = userEvent.setup();
  mount(project({ status: "ready", current: true, proposal }));
  const apply = screen.getByRole("button", { name: "采纳 AI 方案并继续返修" });
  await user.click(screen.getByRole("button", { name: "补齐人物动机" }));
  expect(apply).toBeDisabled();
  expect(screen.getByText(/你已有自选修改要求/)).toBeVisible();
  expect(fetchMock).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "补齐人物动机" }));
  await user.type(
    screen.getByRole("textbox", { name: "修改要求" }),
    "保留原结局",
  );
  expect(apply).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "修改要求" })).toHaveValue(
    "保留原结局",
  );
  await user.clear(screen.getByRole("textbox", { name: "修改要求" }));
  expect(apply).toBeEnabled();
});

it("does not offer adoption for stale advice or unresolved human decisions", () => {
  const view = mount(project({ status: "ready", current: false, proposal }));
  expect(
    screen.queryByRole("button", { name: "采纳 AI 方案并继续返修" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/对应较早的稿件/)).toBeVisible();
  view.unmount();
  mount(
    project({
      status: "ready",
      current: true,
      proposal: {
        ...proposal,
        recommendation: "human_decision",
        decisions: ["需要你确认是否更换故事方向"],
      },
    }),
  );
  expect(screen.getByText("需要你确认是否更换故事方向")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "采纳 AI 方案并继续返修" }),
  ).not.toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});

it("retains advice after a failed adoption and never reports an automatic approval", async () => {
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ error: "版本冲突，请刷新" }), {
      status: 409,
    }),
  );
  const user = userEvent.setup();
  mount(project({ status: "ready", current: true, proposal }));
  await user.click(
    screen.getByRole("button", { name: "采纳 AI 方案并继续返修" }),
  );
  await waitFor(() =>
    expect(screen.getAllByText("版本冲突，请刷新").length).toBeGreaterThan(0),
  );
  expect(screen.getByText(proposal.rationale)).toBeVisible();
  expect(sent().body.action).toBe("provide_feedback");
});
