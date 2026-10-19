import { selectControl } from "./select-control";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ActionForm, DecisionCenter } from "../../ui/factory/src/Decisions";
import type { RecordData } from "../../ui/factory/src/api";

const ID = "012345abcdef";
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(
    new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
});
it("opens the exact shot referenced by a review issue", async () => {
  const onShot = vi.fn(),
    onTab = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <DecisionCenter
        project={project({
          operator: {
            attention: {
              blocking: true,
              kind: "creative",
              actions: [],
              issues: [
                {
                  severity: "major",
                  description: "补齐动作",
                  shot_ids: ["S03"],
                },
              ],
            },
          },
        })}
        onNotify={vi.fn()}
        onTab={onTab}
        onShot={onShot}
      />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "S03" }));
  expect(onShot).toHaveBeenCalledWith("S03");
  expect(onTab).not.toHaveBeenCalled();
});
function project(extra: RecordData = {}): RecordData {
  return {
    id: ID,
    revision: 3,
    status: "blocked",
    workflow_version: 2,
    operator: {
      attention: {
        blocking: true,
        kind: "creative",
        title: "处理审核问题",
        actions: [],
      },
    },
    ...extra,
  };
}
function gateProject(kind: string, extra: RecordData = {}): RecordData {
  return project({
    status: `awaiting_${kind}`,
    operator: {
      attention: {
        blocking: true,
        kind,
        gate: {
          id: `${ID}:${kind}:1`,
          type: kind,
          status: "pending",
          options:
            kind === "final"
              ? ["approve", "revise", "reedit"].map((id) => ({ id }))
              : kind === "style"
                ? [{ id: "ink" }, { id: "__restyle__" }]
                : [],
        },
        actions: [],
      },
    },
    ...extra,
  });
}
function mount(initial: RecordData) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const notify = vi.fn(),
    onTab = vi.fn();
  const view = (p: RecordData) => (
    <QueryClientProvider client={client}>
      <DecisionCenter project={p} onNotify={notify} onTab={onTab} />
    </QueryClientProvider>
  );
  const result = render(view(initial));
  return {
    ...result,
    notify,
    onTab,
    update: (p: RecordData) => result.rerender(view(p)),
  };
}
function sent() {
  const [url, options] = fetchMock.mock.calls.at(-1)!;
  return { url, body: JSON.parse(options.body) };
}
const sourceAction = {
  id: "provide_authorized_source",
  label: "补充授权素材",
  enabled: true,
  inputs: [
    {
      name: "title",
      label: "来源标题",
      type: "text",
      required: false,
      maxlength: 200,
    },
    {
      name: "text",
      label: "获准改编的原文",
      type: "textarea",
      required: false,
      maxlength: 12000,
    },
    {
      name: "rights",
      label: "素材权限",
      type: "select",
      required: true,
      options: [
        { id: "owned", label: "自有" },
        { id: "licensed", label: "已授权" },
      ],
    },
  ],
};

const reviewChoices = [
  {
    id: "all",
    label: "按全部审核建议修改",
    kind: "all_issues",
    feedback: "修复全部审核问题，保留其他设定。",
  },
  {
    id: "issue-one",
    label: "S03 · 补齐救助动机",
    kind: "issue",
    feedback: "S03 补齐救助动机，修改后重新独立审核。",
  },
  {
    id: "dialogue",
    label: "让对白更自然精炼",
    kind: "direction",
    feedback: "精简重复对白，保持人物语气。",
  },
];
function selectableAction(choices = reviewChoices) {
  return {
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
        choices,
      },
    ],
  };
}

describe("review choice buttons", () => {
  it("submits selected review instructions without requiring typed notes", async () => {
    const submit = vi.fn(),
      user = userEvent.setup();
    render(
      <ActionForm
        action={selectableAction()}
        project={project()}
        busy={false}
        onSubmit={submit}
      />,
    );
    const notes = screen.getByRole("textbox", { name: "修改要求" });
    await user.click(
      screen.getByRole("button", { name: "按全部审核建议修改" }),
    );
    expect(notes).toHaveValue("");
    expect(notes).not.toBeRequired();
    expect(submit).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "提交意见并重新审核" }),
    );
    expect(submit).toHaveBeenCalledWith({
      action: "provide_feedback",
      feedback: reviewChoices[0].feedback,
      expected_revision: 3,
    });
  });

  it("switches between all issues and individual issues while keeping optional notes", async () => {
    const submit = vi.fn(),
      user = userEvent.setup();
    render(
      <ActionForm
        action={selectableAction()}
        project={project()}
        busy={false}
        onSubmit={submit}
      />,
    );
    const all = screen.getByRole("button", { name: "按全部审核建议修改" });
    const issue = screen.getByRole("button", { name: "S03 · 补齐救助动机" });
    await user.click(all);
    await user.click(issue);
    expect(all).toHaveAttribute("aria-pressed", "false");
    expect(issue).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "让对白更自然精炼" }));
    await user.type(
      screen.getByRole("textbox", { name: "修改要求" }),
      "保留当前结局",
    );
    await user.click(issue);
    await user.click(
      screen.getByRole("button", { name: "提交意见并重新审核" }),
    );
    expect(submit).toHaveBeenLastCalledWith({
      action: "provide_feedback",
      feedback: reviewChoices[2].feedback + "\n\n保留当前结局",
      expected_revision: 3,
    });
  });

  it("retains the selected wording across polling and requires revision confirmation", async () => {
    const submit = vi.fn(),
      user = userEvent.setup();
    const view = render(
      <ActionForm
        action={selectableAction()}
        project={project()}
        busy={false}
        onSubmit={submit}
      />,
    );
    await user.click(
      screen.getByRole("button", { name: "按全部审核建议修改" }),
    );
    view.rerender(
      <ActionForm
        action={selectableAction(
          reviewChoices.map((c) => ({ ...c, feedback: "新版本审核要求" })),
        )}
        project={project({ revision: 4 })}
        busy={false}
        onSubmit={submit}
      />,
    );
    expect(
      screen.getByRole("button", { name: "提交意见并重新审核" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "按全部审核建议修改" }),
    ).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "已核对当前状态" }));
    await user.click(
      screen.getByRole("button", { name: "提交意见并重新审核" }),
    );
    expect(submit).toHaveBeenLastCalledWith({
      action: "provide_feedback",
      feedback: reviewChoices[0].feedback,
      expected_revision: 4,
    });
  });

  it("supports visual regeneration using a selected direction alone", async () => {
    const user = userEvent.setup(),
      p = gateProject("style", { styles: [{ id: "ink", name: "水墨方案" }] });
    p.operator.attention.feedback_choices = [
      {
        id: "warm",
        label: "改用暖色自然光",
        kind: "direction",
        feedback: "保留人物设定，改用暖色自然光重新生成视觉方案。",
      },
    ];
    mount(p);
    await user.click(screen.getByText("重新调整视觉方向"));
    await user.click(screen.getByRole("button", { name: "改用暖色自然光" }));
    await user.click(
      screen.getByRole("button", { name: "提交新方向并重新生成" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent().body).toEqual({
      type: "style",
      choice: "__restyle__",
      feedback: p.operator.attention.feedback_choices[0].feedback,
      decision_id: `${ID}:style:1`,
      expected_revision: 3,
    });
  });

  it("retains selected final review instructions after a rejected submission", async () => {
    const user = userEvent.setup(),
      p = gateProject("final", {
        delivery: { video_url: `/media/production/factory-${ID}/final.mp4` },
      });
    p.operator.attention.feedback_choices = [
      {
        id: "pacing",
        label: "调整剪辑节奏",
        kind: "direction",
        feedback: "缩短重复空镜，保留完整对白。",
      },
    ];
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: "项目已更新，请核对当前版本" }), {
        status: 409,
      }),
    );
    mount(p);
    const choice = screen.getByRole("button", { name: "调整剪辑节奏" });
    await user.click(choice);
    await user.click(screen.getByRole("button", { name: "重新剪辑" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent().body.choice).toBe("reedit");
    expect(sent().body.feedback).toBe(
      p.operator.attention.feedback_choices[0].feedback,
    );
    await waitFor(() =>
      expect(
        screen.getAllByText("项目已更新，请核对当前版本").length,
      ).toBeGreaterThan(0),
    );
    expect(choice).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("textbox", { name: "你的处理意见" })).toHaveValue(
      "",
    );
  });

  it("rejects oversized composed feedback without silently truncating selected requirements", async () => {
    const user = userEvent.setup(),
      submit = vi.fn(),
      action = selectableAction([
        { ...reviewChoices[0], feedback: "完整要求超过五字" },
      ]);
    action.inputs[0].maxlength = 5;
    render(
      <ActionForm
        action={action}
        project={project()}
        busy={false}
        onSubmit={submit}
      />,
    );
    await user.click(
      screen.getByRole("button", { name: "按全部审核建议修改" }),
    );
    await user.click(
      screen.getByRole("button", { name: "提交意见并重新审核" }),
    );
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByText(/修改要求超过 5 字/)).toBeVisible();
  });
});

describe("server-driven producer decisions", () => {
  it("does not invent retry actions when the server supplies none, and respects unavailable actions", async () => {
    const p = project();
    const view = mount(p);
    expect(screen.getByRole("heading", { name: "处理审核问题" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /重试|重新生成/ }),
    ).not.toBeInTheDocument();
    view.update(
      project({
        operator: {
          attention: {
            blocking: true,
            actions: [
              {
                id: "restore_asset",
                label: "恢复已核对素材",
                enabled: false,
                disabled_reason: "需要先核对云任务",
                inputs: [],
              },
            ],
          },
        },
      }),
    );
    expect(
      screen.getByRole("button", { name: "恢复已核对素材" }),
    ).toBeDisabled();
    expect(screen.getByText("需要先核对云任务")).toBeVisible();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders descriptor constraints and submits untouched default values", async () => {
    const onSubmit = vi.fn();
    render(
      <ActionForm
        project={project()}
        busy={false}
        onSubmit={onSubmit}
        action={{
          id: "revise_brief",
          label: "保存新方向",
          enabled: true,
          inputs: [
            {
              name: "title",
              label: "标题",
              type: "text",
              required: true,
              maxlength: 8,
              value: "预填标题",
            },
            {
              name: "brief",
              label: "创作方向",
              type: "textarea",
              required: true,
              maxlength: 40,
              value: "同一晚解决误会",
            },
            {
              name: "wall_minutes",
              label: "制作时间",
              type: "number",
              required: true,
              min: 10,
              max: 120,
              value: 30,
            },
          ],
        }}
      />,
    );
    expect(screen.getByRole("textbox", { name: "标题" })).toHaveAttribute(
      "maxlength",
      "8",
    );
    expect(screen.getByRole("textbox", { name: "标题" })).toBeRequired();
    expect(screen.getByRole("textbox", { name: "创作方向" })).toHaveValue(
      "同一晚解决误会",
    );
    expect(screen.getByRole("textbox", { name: "创作方向" })).toHaveAttribute(
      "maxlength",
      "40",
    );
    expect(
      screen.getByRole("spinbutton", { name: "制作时间" }),
    ).toHaveAttribute("min", "10");
    expect(
      screen.getByRole("spinbutton", { name: "制作时间" }),
    ).toHaveAttribute("max", "120");
    await userEvent.click(screen.getByRole("button", { name: "保存新方向" }));
    expect(onSubmit).toHaveBeenCalledWith({
      action: "revise_brief",
      title: "预填标题",
      brief: "同一晚解决误会",
      wall_minutes: 30,
      expected_revision: 3,
    });
  });

  it("keeps an edited draft across polling and requires an explicit revision confirmation", async () => {
    const user = userEvent.setup(),
      submit = vi.fn();
    const action = {
      id: "provide_feedback",
      label: "提交创作修改",
      enabled: true,
      inputs: [
        {
          name: "feedback",
          label: "修改要求",
          type: "textarea",
          required: true,
          maxlength: 4000,
        },
      ],
    };
    const result = render(
      <ActionForm
        project={project()}
        action={action}
        busy={false}
        onSubmit={submit}
      />,
    );
    await user.type(
      screen.getByRole("textbox", { name: "修改要求" }),
      "保留结局，只改动机",
    );
    result.rerender(
      <ActionForm
        project={project({ revision: 4 })}
        action={action}
        busy={false}
        onSubmit={submit}
      />,
    );
    expect(screen.getByRole("textbox", { name: "修改要求" })).toHaveValue(
      "保留结局，只改动机",
    );
    expect(screen.getByRole("button", { name: "提交创作修改" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "已核对当前状态" }));
    await user.click(screen.getByRole("button", { name: "提交创作修改" }));
    expect(submit).toHaveBeenCalledWith({
      action: "provide_feedback",
      feedback: "保留结局，只改动机",
      expected_revision: 4,
    });
  });

  it("sends source and original-mode commands with their actual server action identifiers", async () => {
    const user = userEvent.setup();
    mount(
      project({
        operator: {
          attention: {
            blocking: true,
            actions: [
              sourceAction,
              {
                id: "switch_to_original",
                label: "切换原创创作",
                enabled: true,
                inputs: [],
              },
            ],
          },
        },
      }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "来源标题" }),
      "自有故事",
    );
    await user.type(
      screen.getByRole("textbox", { name: "获准改编的原文" }),
      "这是获准改编的完整素材。",
    );
    await selectControl(
      screen.getByRole("combobox", { name: "素材权限" }),
      "licensed",
      user,
    );
    await user.click(screen.getByRole("button", { name: "补充授权素材" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent()).toEqual({
      url: `/api/factory/projects/${ID}/action`,
      body: {
        action: "provide_authorized_source",
        title: "自有故事",
        text: "这是获准改编的完整素材。",
        rights: "licensed",
        expected_revision: 3,
      },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "切换原创创作" }),
      ).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "切换原创创作" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(sent().body).toEqual({
      action: "switch_to_original",
      expected_revision: 3,
    });
  });

  it("preserves a child action's revision when the parent decision draft belongs to an older version", async () => {
    const user = userEvent.setup();
    const p = gateProject("story");
    p.operator.attention.actions = [sourceAction];
    const view = mount(p);
    await user.click(screen.getByText("这些方向都不合适？"));
    await user.type(
      screen.getByRole("textbox", { name: "你的处理意见" }),
      "保留此旧版草稿",
    );
    view.update({ ...p, revision: 4 });
    await user.type(
      screen.getByRole("textbox", { name: "获准改编的原文" }),
      "当前版本补充的素材",
    );
    await selectControl(
      screen.getByRole("combobox", { name: "素材权限" }),
      "owned",
      user,
    );
    await user.click(screen.getByRole("button", { name: "补充授权素材" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent().body.expected_revision).toBe(4);
    expect(sent().body.action).toBe("provide_authorized_source");
  });

  it("shows only eligible candidates named by the current gate", async () => {
    const p = gateProject("story", {
      proposals: [
        { id: "A", title: "合格且在当前关卡", eligible: true },
        { id: "B", title: "审核被否决", eligible: false },
        { id: "C", title: "旧版候选", eligible: true },
        { id: "D", title: "尚未记录审核资格" },
      ],
    });
    p.operator.attention.gate.options = [{ id: "A" }, { id: "B" }, { id: "D" }];
    mount(p);
    expect(screen.getByText("合格且在当前关卡")).toBeVisible();
    expect(screen.queryByText("审核被否决")).not.toBeInTheDocument();
    expect(screen.queryByText("旧版候选")).not.toBeInTheDocument();
    expect(screen.queryByText("尚未记录审核资格")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "选择这个故事" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent()).toEqual({
      url: `/api/factory/projects/${ID}/decision`,
      body: {
        type: "story",
        choice: "A",
        decision_id: `${ID}:story:1`,
        expected_revision: 3,
      },
    });
  });

  it("does not treat an empty gate option list as permission to select historical proposals", () => {
    mount(
      gateProject("story", {
        proposals: [{ id: "old", title: "历史合格稿", eligible: true }],
      }),
    );
    expect(
      screen.queryByRole("button", { name: "选择这个故事" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("当前没有可选择的合格方案")).toBeVisible();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires visual feedback before rejecting every style, then submits the restyle gate decision", async () => {
    const user = userEvent.setup();
    mount(gateProject("style", { styles: [{ id: "ink", name: "水墨方案" }] }));
    await user.click(screen.getByText("重新调整视觉方向"));
    await user.click(
      screen.getByRole("button", { name: "提交新方向并重新生成" }),
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(
      screen.getAllByText("请选择修改方向，或填写新的创作方向。").length,
    ).toBeGreaterThan(0);
    await user.type(
      screen.getByRole("textbox", { name: "你的处理意见" }),
      "全部不满意，改用暖色细线",
    );
    await user.click(
      screen.getByRole("button", { name: "提交新方向并重新生成" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent().body).toEqual({
      type: "style",
      choice: "__restyle__",
      feedback: "全部不满意，改用暖色细线",
      decision_id: `${ID}:style:1`,
      expected_revision: 3,
    });
  });

  it("does not approve a final gate without project-owned playable media", () => {
    const view = mount(
      gateProject("final", {
        delivery: {
          video_url: "/media/production/factory-fedcba654321/other.mp4",
        },
      }),
    );
    expect(screen.getByText("暂时没有可播放的成片")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "确认通过并交付" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看制作记录" }));
    expect(view.onTab).toHaveBeenCalledWith("quality");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("requires concrete feedback for a final revision and retains it after a failed request", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: "项目已更新，请核对当前版本" }), {
        status: 409,
      }),
    );
    mount(
      gateProject("final", {
        delivery: { video_url: `/media/production/factory-${ID}/final.mp4` },
      }),
    );
    await user.click(screen.getByRole("button", { name: "局部修改" }));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(
      screen.getAllByText("请选择修改要求，或填写具体修改意见。").length,
    ).toBeGreaterThan(0);
    await user.type(
      screen.getByRole("textbox", { name: "你的处理意见" }),
      "S03对白提前半秒",
    );
    await user.click(screen.getByRole("button", { name: "局部修改" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sent().body).toEqual({
      type: "final",
      choice: "revise",
      feedback: "S03对白提前半秒",
      decision_id: `${ID}:final:1`,
      expected_revision: 3,
    });
    expect(
      await screen.findAllByText("项目已更新，请核对当前版本"),
    ).not.toHaveLength(0);
    expect(screen.getByRole("textbox", { name: "你的处理意见" })).toHaveValue(
      "S03对白提前半秒",
    );
  });

  it("has no pending decisions after approval even if old gates remain in history", () => {
    mount(
      project({
        status: "approved",
        operator: { attention: null },
        decisions: [{ id: "old", type: "final", status: "pending" }],
      }),
    );
    expect(screen.getByText("当前没有人工待办")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /交付|重试|修改/ }),
    ).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

it("allows paid image preview authorization before any shots exist", async () => {
  mount(
    project({
      stage: "style_previews",
      needs_decision: { code: "cloud_authorization_required" },
      cloud_quote: {
        id: "image-batch",
        media_kind: "image",
        estimated_submissions: 3,
        estimated_total_micros: null,
        pricing: { currency: "CNY", unit_cost_micros: null },
      },
    }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "我已了解单价未确认，仍授权此批请求",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "明确授权本批次" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(fetchMock.mock.calls[0][0]).toContain("/cloud-authorize");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
    batch_id: "image-batch",
    expected_revision: 3,
    max_submissions: 3,
    confirm_unknown_cost: true,
  });
});
