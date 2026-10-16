import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TeamConsole, TeamOverview } from "../../ui/factory/src/TeamConsole";
import type { RecordData } from "../../ui/factory/src/api";

const id = "012345abcdef";
const task = (overrides: RecordData = {}) => ({
  id: "video:S01",
  label: "第 1 镜视频",
  kind: "video",
  scope: "S01",
  shot_id: "S01",
  owner: "video_generator",
  department: "visual",
  status: "running",
  generation: 2,
  attempt: 1,
  input_version: "storyboard-v3",
  dependencies: ["image:S01"],
  inputs: [{ label: "动作要求", value: "表情克制，保留服装" }],
  artifacts: [],
  review_status: "pending",
  adoption_status: "unselected",
  priority: 0,
  hold_after: false,
  locked: false,
  model: "actual-model-v1",
  attempts: [],
  ...overrides,
});
const departments = [
  ["production", "制片统筹"],
  ["story", "故事编剧"],
  ["director", "导演分镜"],
  ["visual", "美术与影像"],
  ["sound", "声音制作"],
  ["post", "后期制作"],
  ["quality", "独立质检"],
];
function team() {
  return {
    schema: 1,
    enabled: true,
    control_revision: 0,
    departments: departments.map(([id, label]) => ({
      id,
      label,
      lead: "video_generator",
      responsibility: `${label}职责`,
      members: id === "visual" ? ["video_generator"] : [],
    })),
    members: [
      {
        id: "video_generator",
        role: "video_generator",
        label: "视频制作",
        department: "visual",
        leader: false,
        status: "running",
        current_tasks: ["video:S01"],
      },
    ],
    task_summary: {
      total: 2,
      running: 1,
      waiting: 0,
      blocked: 0,
      completed: 1,
    },
    events: [
      {
        id: "e1",
        at: "2026-10-16T04:00:00Z",
        message: "首帧已交付，进入视频制作",
        kind: "handoff",
        task_id: "image:S01",
      },
    ],
    instructions: [],
    locks: [],
    actions: [],
  };
}
function mount(fallback?: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <TeamConsole projectId={id}>
        {fallback && <div>已有执行回执</div>}
      </TeamConsole>
    </QueryClientProvider>,
  );
  return client;
}
function mockApi() {
  const image = task({
    id: "image:S01",
    label: "第 1 镜首帧",
    status: "completed",
    review_status: "passed",
    artifacts: [
      { label: "已确认首帧", url: `/media/production/factory-${id}/frame.png` },
    ],
    dependencies: [],
    owner: "image_generator",
  });
  return vi.fn(async (url: string) => {
    const path = new URL(url, "http://localhost");
    const data = path.pathname.endsWith("/team")
      ? team()
      : path.pathname.endsWith("/tasks/image%3AS01")
        ? { task: image }
        : path.pathname.endsWith("/tasks/video%3AS01")
          ? { task: task() }
          : {
              tasks:
                path.searchParams.get("status") === "completed"
                  ? [image]
                  : [task(), image],
              total: 2,
              offset: 0,
              limit: 30,
            };
    return new Response(JSON.stringify(data), {
      headers: { "Content-Type": "application/json" },
    });
  });
}
afterEach(() => vi.unstubAllGlobals());
describe("team command console", () => {
  it("shows real departments, independent statuses, evidence, and event drilldown", async () => {
    const fetch = mockApi();
    vi.stubGlobal("fetch", fetch);
    mount();
    expect(
      await screen.findByRole("heading", { name: "团队指挥台" }),
    ).toBeVisible();
    for (const [, label] of departments)
      expect(screen.getByRole("heading", { name: label })).toBeVisible();
    const detail = within(
      screen.getByRole("complementary", { name: "制作任务详情" }),
    );
    expect(await detail.findByText("actual-model-v1")).toBeVisible();
    expect(detail.getByText("storyboard-v3")).toBeVisible();
    expect(await detail.findByText("表情克制，保留服装")).toBeVisible();
    expect(detail.getByText("待审核")).toBeVisible();
    expect(detail.getByText("未采用")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "介入并下达要求" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "首帧已交付，进入视频制作" }),
    );
    expect(
      await detail.findByRole("heading", { name: "第 1 镜首帧" }),
    ).toBeVisible();
    expect(detail.getByRole("link", { name: "已确认首帧" })).toHaveAttribute(
      "href",
      expect.stringContaining(`/factory-${id}/frame.png`),
    );
    expect(
      fetch.mock.calls.every(([, options]: any[]) => options.method === "GET"),
    ).toBe(true);
  });
  it("sends filters to the server and retains the selected task across refresh and filtering", async () => {
    const fetch = mockApi();
    vi.stubGlobal("fetch", fetch);
    const client = mount();
    await screen.findByRole("heading", { name: "团队指挥台" });
    const detail = within(
      screen.getByRole("complementary", { name: "制作任务详情" }),
    );
    await detail.findByRole("heading", { name: "第 1 镜视频" });
    await userEvent.click(
      screen.getByRole("button", { name: /第 1 镜视频.*执行中/ }),
    );
    await userEvent.selectOptions(screen.getByLabelText("部门"), "visual");
    await userEvent.selectOptions(
      screen.getByLabelText("执行状态"),
      "completed",
    );
    await userEvent.type(screen.getByLabelText("镜头编号"), "S01");
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([url]) =>
          String(url).includes(
            "department=visual&status=completed&shot_id=S01",
          ),
        ),
      ).toBe(true),
    );
    await client.invalidateQueries();
    expect(detail.getByRole("heading", { name: "第 1 镜视频" })).toBeVisible();
    expect(screen.getByLabelText("镜头编号")).toHaveValue("S01");
  });
  it("preserves legacy receipts if the server does not expose team data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "not found" }), { status: 404 }),
      ),
    );
    mount(true);
    expect(screen.getByText("已有执行回执")).toBeVisible();
    await waitFor(() =>
      expect(screen.queryByText("团队指挥台")).not.toBeInTheDocument(),
    );
  });
  it("keeps existing receipts accessible when live team is available", async () => {
    vi.stubGlobal("fetch", mockApi());
    mount(true);
    await screen.findByRole("heading", { name: "团队指挥台" });
    await userEvent.click(screen.getByText("工作流与历史执行回执"));
    expect(screen.getByText("已有执行回执")).toBeVisible();
  });
  it("provides the project overview summary with a single team entry", async () => {
    vi.stubGlobal("fetch", mockApi());
    const client = new QueryClient();
    const onOpen = vi.fn();
    render(
      <QueryClientProvider client={client}>
        <TeamOverview projectId={id} onOpen={onOpen} />
      </QueryClientProvider>,
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "打开团队指挥台" }),
    );
    expect(onOpen).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("团队任务概况")).toHaveTextContent("1执行中");
  });
});

function controlApi(
  options: {
    conflict?: boolean;
    failSubmit?: boolean;
    disabled?: boolean;
  } = {},
) {
  let generation = 2,
    control = 3,
    locked = false;
  const submissions: RecordData[] = [];
  const quickActions: RecordData[] = [];
  const current = () =>
    task({
      generation,
      locked,
      actions: [
        {
          id: "pause",
          label: "暂停任务",
          enabled: !options.disabled,
          disabled_reason: options.disabled ? "任务已完成" : undefined,
        },
        {
          id: locked ? "unlock" : "lock",
          label: locked ? "解锁成果" : "锁定成果",
          enabled: true,
        },
        { id: "hold_after", label: "完成后等我确认", enabled: true },
        { id: "intervene", label: "介入并下达要求", enabled: true },
      ],
    });
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, "http://localhost");
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    let data: RecordData;
    if (path.pathname.endsWith("/team"))
      data = {
        ...team(),
        control_revision: control,
        actions: [{ id: "intervene", enabled: true }],
      };
    else if (path.pathname.endsWith("/interventions/preview"))
      data = {
        affected_tasks: ["video:S01", "edit:project"],
        preserved_tasks: body.preserve,
        conflicts: options.conflict
          ? [
              {
                task_id: "video:S01",
                reason: "视频候选已锁定，请解锁或调整范围",
              },
            ]
          : [],
      };
    else if (
      path.pathname.endsWith("/interventions") &&
      init?.method === "POST"
    ) {
      submissions.push(body);
      if (options.failSubmit)
        return new Response(JSON.stringify({ error: "任务基线已变化" }), {
          status: 409,
        });
      generation++;
      data = {
        instruction: {
          id: "instruction-1",
          status: "pending",
          affected_tasks: ["video:S01"],
        },
        control_revision: ++control,
      };
    } else if (path.pathname.endsWith("/actions")) {
      quickActions.push(body);
      if (body.action === "lock") locked = true;
      if (body.action === "unlock") locked = false;
      data = { task: current(), control_revision: ++control };
    } else if (path.pathname.endsWith("/tasks/video%3AS01"))
      data = { task: current(), control_revision: control };
    else
      data = {
        tasks: [
          current(),
          task({
            id: "speech:S01:D1",
            label: "第 1 镜配音",
            kind: "speech",
            owner: "speech",
            department: "sound",
          }),
        ],
        total: 2,
        control_revision: control,
      };
    return new Response(JSON.stringify(data), {
      headers: { "Content-Type": "application/json" },
    });
  });
  return {
    fetch,
    submissions,
    quickActions,
    bump: () => {
      generation++;
      control++;
    },
  };
}

describe("task control and intervention", () => {
  it("enables legacy v2 control only through the server capability", async () => {
    const base = mockApi();
    let enabled = false;
    const enableBodies: RecordData[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith("/team/actions")) {
          enableBodies.push(JSON.parse(String(init?.body)));
          enabled = true;
          return new Response(
            JSON.stringify({ team: { ...team(), control_revision: 1 } }),
          );
        }
        if (url.endsWith("/team"))
          return new Response(
            JSON.stringify({
              ...team(),
              enabled,
              enable_available: !enabled,
              control_revision: enabled ? 1 : 0,
              actions: enabled
                ? []
                : [
                    {
                      id: "enable_control",
                      label: "启用团队控制",
                      enabled: true,
                    },
                  ],
            }),
          );
        return base(url);
      }),
    );
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "启用团队控制" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "启用团队控制" }),
      ).not.toBeInTheDocument(),
    );
    expect(enableBodies).toHaveLength(1);
    expect(enableBodies[0]).toMatchObject({
      action: "enable",
      expected_control_revision: 0,
    });
  });
  it("uses server capabilities and optimistic versions for lock/pause/hold actions", async () => {
    const api = controlApi({ disabled: true });
    vi.stubGlobal("fetch", api.fetch);
    mount();
    const lock = await screen.findByRole("button", { name: "锁定成果" });
    expect(screen.getByRole("button", { name: "暂停任务" })).toBeDisabled();
    expect(screen.getByText("暂停任务：任务已完成")).toBeVisible();
    await userEvent.click(lock);
    await screen.findByRole("button", { name: "解锁成果" });
    expect(api.quickActions[0]).toMatchObject({
      action: "lock",
      expected_generation: 2,
      expected_control_revision: 3,
    });
    expect(api.quickActions[0].request_id).toEqual(expect.any(String));
    await userEvent.click(
      screen.getByRole("button", { name: "完成后等我确认" }),
    );
    await waitFor(() =>
      expect(api.quickActions[1]).toMatchObject({
        action: "hold_after",
        expected_control_revision: 4,
      }),
    );
  });
  it("previews impact, preserves selected sound and registers explicit wait behavior", async () => {
    const api = controlApi();
    vi.stubGlobal("fetch", api.fetch);
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "介入并下达要求" }),
    );
    const form = within(screen.getByRole("dialog"));
    await userEvent.type(
      form.getByLabelText("新的制作要求"),
      "人物表情更克制，保留配音",
    );
    await userEvent.click(
      await form.findByRole("checkbox", { name: /第 1 镜配音/ }),
    );
    await userEvent.selectOptions(form.getByLabelText("完成后的安排"), "wait");
    await userEvent.click(form.getByRole("button", { name: "查看影响" }));
    expect(await form.findByLabelText("返工影响预览")).toHaveTextContent(
      "edit:project",
    );
    await userEvent.click(form.getByRole("button", { name: "登记指令并执行" }));
    expect(
      await screen.findByText("指令已登记，当前调用收尾后生效。"),
    ).toBeVisible();
    expect(api.submissions).toHaveLength(1);
    expect(api.submissions[0]).toMatchObject({
      target_type: "task",
      target_id: "video:S01",
      task_ids: ["video:S01"],
      task_generations: { "video:S01": 2 },
      preserve: ["speech:S01:D1"],
      after: "wait",
      expected_control_revision: 3,
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "介入并下达要求" }),
    );
    expect(screen.getByLabelText("新的制作要求")).toHaveValue("");
    expect(screen.queryByText(/任务版本已变化/)).not.toBeInTheDocument();
  });
  it("keeps the instruction draft on lock conflict and does not dispatch", async () => {
    const api = controlApi({ conflict: true });
    vi.stubGlobal("fetch", api.fetch);
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "介入并下达要求" }),
    );
    await userEvent.type(
      screen.getByLabelText("新的制作要求"),
      "保留构图，修改表情",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "登记指令并执行" }),
    );
    expect(await screen.findByText(/视频候选已锁定/)).toBeVisible();
    expect(screen.getByLabelText("新的制作要求")).toHaveValue(
      "保留构图，修改表情",
    );
    expect(api.submissions).toHaveLength(0);
  });
  it("retains drafts across refresh and tab unmount, keeps original generation until explicit reload", async () => {
    const api = controlApi();
    vi.stubGlobal("fetch", api.fetch);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = (visible: boolean) => (
      <QueryClientProvider client={client}>
        {visible && <TeamConsole projectId={id} />}
      </QueryClientProvider>
    );
    const rendered = render(view(true));
    await userEvent.click(
      await screen.findByRole("button", { name: "介入并下达要求" }),
    );
    await userEvent.type(screen.getByLabelText("新的制作要求"), "不要改动服装");
    rendered.rerender(view(false));
    rendered.rerender(view(true));
    expect(await screen.findByLabelText("新的制作要求")).toHaveValue(
      "不要改动服装",
    );
    api.bump();
    await client.invalidateQueries({
      predicate: (query) => !String(query.queryKey[0]).includes("draft"),
    });
    expect(await screen.findByText(/任务版本已变化/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "登记指令并执行" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "重新读取基线（保留要求）" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "登记指令并执行" }),
      ).toBeEnabled(),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "登记指令并执行" }),
    );
    await waitFor(() =>
      expect(api.submissions[0].task_generations).toEqual({ "video:S01": 3 }),
    );
  });
  it("preserves the draft and original generation after a server rejection", async () => {
    const api = controlApi({ failSubmit: true });
    vi.stubGlobal("fetch", api.fetch);
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "介入并下达要求" }),
    );
    await userEvent.type(screen.getByLabelText("新的制作要求"), "只重做动作");
    await userEvent.click(
      screen.getByRole("button", { name: "登记指令并执行" }),
    );
    expect(await screen.findByText("任务基线已变化")).toBeVisible();
    expect(screen.getByLabelText("新的制作要求")).toHaveValue("只重做动作");
    expect(api.submissions[0].task_generations).toEqual({ "video:S01": 2 });
    expect(
      screen.getByRole("button", { name: "重新读取基线（保留要求）" }),
    ).toBeVisible();
  });
  it("selects explicit owned tasks for member intervention", async () => {
    const api = controlApi();
    vi.stubGlobal("fetch", api.fetch);
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "介入成员任务" }),
    );
    const dialog = within(screen.getByRole("dialog"));
    expect(
      dialog.getByRole("button", { name: "登记指令并执行" }),
    ).toBeDisabled();
    await userEvent.click(
      await within(dialog.getByRole("group", { name: "修改范围" })).findByRole(
        "checkbox",
        { name: /第 1 镜视频/ },
      ),
    );
    await userEvent.type(dialog.getByLabelText("新的制作要求"), "按原要求重做");
    await userEvent.selectOptions(dialog.getByLabelText("处理方式"), "retry");
    await userEvent.click(
      dialog.getByRole("button", { name: "登记指令并执行" }),
    );
    await waitFor(() =>
      expect(api.submissions[0]).toMatchObject({
        target_type: "member",
        target_id: "video_generator",
        task_ids: ["video:S01"],
        operation: "retry",
      }),
    );
    expect(
      api.fetch.mock.calls.some(([url]) =>
        String(url).includes("owner=video_generator"),
      ),
    ).toBe(true);
  });
});

function leadershipApi(rootAvailable = true) {
  const base = controlApi();
  const submissions: RecordData[] = [];
  const leadRoles = [
    "executive_producer",
    "story_editor",
    "storyboard_director",
    "visual_designer",
    "dialogue_editor",
    "edit_director",
    "independent_critic",
  ];
  const leadLabels = [
    "总制片",
    "故事编辑",
    "分镜导演",
    "视觉设计",
    "对白编辑",
    "剪辑导演",
    "独立审稿",
  ];
  const video = task({
    allowed_owners: ["video_generator"],
    default_owner: "video_generator",
  });
  const sound = task({
    id: "speech:S01:D1",
    label: "第 1 镜配音",
    department: "sound",
    owner: "speech",
  });
  const report = {
    id: "leader-instruction",
    target_type: "leader",
    target_id: "executive_producer",
    instruction: "先完成第 1 镜，再安排配音",
    status: "applied",
    affected_tasks: [video.id, sound.id],
    plan: {
      management_tasks: ["management:leader-1"],
      summary: "先做关键镜头，再交接声音制作",
      operations: [
        { action: "prioritize", task_id: video.id, priority: 9 },
        { action: "revise", task_id: sound.id, requirements: "语速更自然" },
      ],
      delegations: [
        {
          department: "sound",
          task_ids: [sound.id],
          summary: "按新节奏完成对白",
        },
      ],
    },
  };
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, "http://localhost");
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    let data: RecordData;
    if (path.pathname.endsWith("/team"))
      data = {
        ...team(),
        actions: [
          { id: "intervene", enabled: true },
          { id: "replan", enabled: rootAvailable },
        ],
        instructions: [report],
        departments: team().departments.map(
          (item: RecordData, index: number) => ({
            ...item,
            lead: leadRoles[index],
            management_available: item.id !== "sound",
            management_unavailable_reason:
              item.id === "sound" ? "声音负责人模型未配置" : undefined,
          }),
        ),
        members: [
          ...team().members,
          ...leadRoles.map((role, index) => ({
            id: role,
            role,
            label: leadLabels[index],
            department: departments[index][0],
            leader: true,
            leader_available:
              role !== "dialogue_editor" &&
              (rootAvailable || role !== "executive_producer"),
            leader_unavailable_reason:
              role === "dialogue_editor" ? "声音负责人模型未配置" : undefined,
            status: "idle",
            current_tasks: [],
          })),
        ],
      };
    else if (path.pathname.endsWith("/interventions/preview"))
      data = {
        affected_tasks: body.task_ids,
        preserved_tasks: body.preserve,
        conflicts: [],
      };
    else if (
      path.pathname.endsWith("/interventions") &&
      init?.method === "POST"
    ) {
      submissions.push(body);
      data = {
        instruction: { id: "leader-new", status: "queued" },
        control_revision: 1,
      };
    } else if (path.pathname.endsWith("/interventions"))
      data = { instructions: [report], total: 1, offset: 0, limit: 20 };
    else if (path.pathname.endsWith("/tasks/management%3Aleader-1"))
      data = {
        task: task({
          id: "management:leader-1",
          label: "总制片安排回执",
          kind: "management",
          owner: "executive_producer",
          model: "manager-actual-model",
          inputs: [],
          dependencies: [],
        }),
      };
    else if (path.pathname.endsWith("/tasks"))
      data = {
        tasks:
          path.searchParams.get("department") === "visual"
            ? [video]
            : [video, sound],
        total: path.searchParams.get("department") === "visual" ? 1 : 2,
        control_revision: 0,
      };
    else return base.fetch(url, init);
    return new Response(JSON.stringify(data));
  });
  return { fetch, submissions };
}

describe("leader scheduling", () => {
  it("retains the leader draft when availability changes while editing", async () => {
    const api = leadershipApi();
    vi.stubGlobal("fetch", api.fetch);
    const client = mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "向总制片下达要求" }),
    );
    const dialog = within(screen.getByRole("dialog"));
    await userEvent.click(
      await within(dialog.getByRole("group", { name: "修改范围" })).findByRole(
        "checkbox",
        { name: /第 1 镜视频/ },
      ),
    );
    await userEvent.type(
      dialog.getByLabelText("新的制作要求"),
      "先安排这个镜头",
    );
    client.setQueryData(["team", id], (old: RecordData) => ({
      ...old,
      members: old.members.map((member: RecordData) =>
        member.id === "executive_producer"
          ? {
              ...member,
              leader_available: false,
              leader_unavailable_reason: "总制片模型暂不可用",
            }
          : member,
      ),
    }));
    expect(await dialog.findByText("总制片模型暂不可用")).toBeVisible();
    expect(
      dialog.getByRole("button", { name: "登记指令并执行" }),
    ).toBeDisabled();
    expect(dialog.getByLabelText("新的制作要求")).toHaveValue("先安排这个镜头");
  });
  it("keeps configured department leaders available if the total producer is unavailable", async () => {
    const api = leadershipApi(false);
    vi.stubGlobal("fetch", api.fetch);
    mount();
    expect(
      await screen.findByRole("button", { name: "向总制片下达要求" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "请美术与影像负责人安排" }),
    ).toBeEnabled();
  });
  it("submits a total producer replan with explicit tasks across departments", async () => {
    const api = leadershipApi();
    vi.stubGlobal("fetch", api.fetch);
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "向总制片下达要求" }),
    );
    const dialog = within(screen.getByRole("dialog"));
    const scope = within(dialog.getByRole("group", { name: "修改范围" }));
    expect(dialog.getByLabelText("处理方式")).toHaveValue("replan");
    expect(
      dialog.getByRole("button", { name: "登记指令并执行" }),
    ).toBeDisabled();
    await userEvent.click(
      await scope.findByRole("checkbox", { name: /第 1 镜视频/ }),
    );
    await userEvent.click(scope.getByRole("checkbox", { name: /第 1 镜配音/ }));
    await userEvent.type(
      dialog.getByLabelText("新的制作要求"),
      "优先完成画面，再处理声音",
    );
    await userEvent.click(
      dialog.getByRole("button", { name: "登记指令并执行" }),
    );
    await waitFor(() =>
      expect(api.submissions[0]).toMatchObject({
        target_type: "leader",
        target_id: "executive_producer",
        operation: "replan",
        task_ids: ["video:S01", "speech:S01:D1"],
        task_generations: { "video:S01": 2, "speech:S01:D1": 2 },
      }),
    );
  });
  it("limits department leader scope to that department and retains other operation choices", async () => {
    const api = leadershipApi();
    vi.stubGlobal("fetch", api.fetch);
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "请美术与影像负责人安排" }),
    );
    const dialog = within(screen.getByRole("dialog"));
    await userEvent.click(
      await within(dialog.getByRole("group", { name: "修改范围" })).findByRole(
        "checkbox",
        { name: /第 1 镜视频/ },
      ),
    );
    expect(
      within(dialog.getByRole("group", { name: "修改范围" })).queryByRole(
        "checkbox",
        { name: /配音/ },
      ),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(dialog.getByLabelText("处理方式"), "revise");
    await userEvent.type(
      dialog.getByLabelText("新的制作要求"),
      "重新安排人物动作",
    );
    await userEvent.click(
      dialog.getByRole("button", { name: "登记指令并执行" }),
    );
    await waitFor(() =>
      expect(api.submissions[0]).toMatchObject({
        target_type: "leader",
        target_id: "visual_designer",
        operation: "revise",
        task_ids: ["video:S01"],
      }),
    );
    expect(
      api.fetch.mock.calls.some(([url]) =>
        String(url).includes("department=visual"),
      ),
    ).toBe(true);
  });
  it("shows unavailable leader reasons on demand and links actual management task evidence", async () => {
    const api = leadershipApi();
    vi.stubGlobal("fetch", api.fetch);
    mount();
    const unavailable = await screen.findByRole("button", {
      name: "请声音制作负责人安排",
    });
    expect(unavailable).toBeDisabled();
    const departmentCard = within(unavailable.closest("article")!);
    await userEvent.click(departmentCard.getByText("查看不可用原因"));
    expect(departmentCard.getByText("声音负责人模型未配置")).toBeVisible();
    const history = within(await screen.findByLabelText("已登记的制作指令"));
    expect(
      await history.findByText("先做关键镜头，再交接声音制作"),
    ).toBeVisible();
    expect(history.getByText("已应用的安排")).toBeVisible();
    expect(history.getByText("调整优先级")).toBeVisible();
    expect(history.getByText("语速更自然")).toBeVisible();
    await userEvent.click(
      history.getByRole("button", {
        name: /查看管理任务：(?:management:leader-1|总制片安排回执)/,
      }),
    );
    const detail = within(
      screen.getByRole("complementary", { name: "制作任务详情" }),
    );
    expect(
      await detail.findByRole("heading", { name: "总制片安排回执" }),
    ).toBeVisible();
    expect(detail.getByText("manager-actual-model")).toBeVisible();
  });
});
