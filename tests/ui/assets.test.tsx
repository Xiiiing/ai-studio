import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import {
  CloudAuthorization,
  Costs,
  FeeConfirmation,
  SeriesOperation,
  Shots,
} from "../../ui/factory/src/Assets";
import { keys, type RecordData } from "../../ui/factory/src/api";

const project = {
  id: "012345abcdef",
  slug: "factory-012345abcdef",
  revision: 17,
  status: "paused",
  production_revision: 2,
  series_key: "fixture-series",
  selected_story: "A",
  selected_style: "style-1",
  script_review: { passed: true },
  shots: [{ id: "S01" }],
  script: {
    cast: { female: "Fictional adult woman" },
    shots: [
      {
        id: "S01",
        seconds: 4,
        prompt: "Original character keyframe",
        motion_prompt: "Slow camera push in",
        camera_motion: "push_in",
        dialogue: [],
        reference_group: "female",
      },
    ],
  },
};
const clients: QueryClient[] = [];
function queryWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  clients.push(client);
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper: Wrapper };
}
function captureRequests(get: () => RecordData = () => ({})) {
  const posts: { path: string; body: RecordData }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST")
        posts.push({ path, body: JSON.parse(String(init.body)) });
      return new Response(
        JSON.stringify(init?.method === "POST" ? { project } : get()),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  return posts;
}
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

describe("series drafts bind the actual Bible and asset versions", () => {
  it("keeps a Bible draft stale when its version changes without a project revision change", async () => {
    const user = userEvent.setup(),
      posts = captureRequests(),
      notify = vi.fn();
    const initialDocument = {
      schema_version: 1,
      world: "原始世界",
      visual_rules: "柔和线条",
      characters: [],
      scenes: [],
      props: [],
    };
    const draftDocument = { ...initialDocument, world: "出品人正在编辑的世界" };
    const props = {
      project,
      base: {
        action: "save_bible",
        expected_version: 3,
        document: initialDocument,
      },
      path: "/projects/" + project.id + "/series",
      label: "保存设定",
      notify,
      library: {},
    };
    const view = render(<SeriesOperation {...props} />, {
      wrapper: queryWrapper().wrapper,
    });
    fireEvent.change(screen.getByLabelText("正式设定 JSON"), {
      target: { value: JSON.stringify(draftDocument) },
    });
    view.rerender(
      <SeriesOperation
        {...props}
        base={{
          ...props.base,
          expected_version: 4,
          document: { ...initialDocument, world: "另一终端提交的世界" },
        }}
      />,
    );
    expect(screen.getByLabelText("正式设定 JSON")).toHaveValue(
      JSON.stringify(draftDocument),
    );
    expect(screen.getByText(/草稿保留/)).toBeVisible();
    expect(screen.getByRole("button", { name: "保存设定" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存设定" }));
    expect(posts).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "已核对当前版本" }));
    await user.click(screen.getByRole("button", { name: "保存设定" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      path: "/api/factory/projects/" + project.id + "/series",
      body: {
        action: "save_bible",
        expected_revision: 17,
        expected_version: 4,
        document: draftDocument,
      },
    });
  });

  it("freezes an asset revision and source choice until the user acknowledges an external asset update", async () => {
    const user = userEvent.setup(),
      posts = captureRequests();
    const props = {
      project,
      base: {
        action: "register",
        asset_id: "asset_fixture",
        version: 2,
        expected_asset_revision: 5,
      },
      path: "/projects/" + project.id + "/series",
      label: "登记图片",
      notify: vi.fn(),
      library: { sources: [{ key: "style:ink" }, { key: "shot:S01" }] },
    };
    const view = render(<SeriesOperation {...props} />, {
      wrapper: queryWrapper().wrapper,
    });
    await user.selectOptions(
      screen.getByLabelText("当前项目已登记图片"),
      "shot:S01",
    );
    view.rerender(
      <SeriesOperation
        {...props}
        base={{ ...props.base, expected_asset_revision: 6 }}
      />,
    );
    expect(screen.getByLabelText("当前项目已登记图片")).toHaveValue("shot:S01");
    expect(screen.getByRole("button", { name: "登记图片" })).toBeDisabled();
    expect(posts).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "已核对当前版本" }));
    await user.click(screen.getByRole("button", { name: "登记图片" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      action: "register",
      expected_revision: 17,
      asset_id: "asset_fixture",
      version: 2,
      expected_asset_revision: 6,
      source_key: "shot:S01",
    });
  });

  it("binds approved references using only the Python bind API fields", async () => {
    const user = userEvent.setup(),
      posts = captureRequests();
    render(
      <SeriesOperation
        project={project}
        base={{
          action: "bind",
          asset_id: "asset_fixture",
          version: 2,
          kind: "character",
          entity_id: "character_01",
        }}
        path={"/projects/" + project.id + "/series"}
        label="固定引用"
        notify={vi.fn()}
        library={{}}
      />,
      { wrapper: queryWrapper().wrapper },
    );
    await user.click(screen.getByLabelText("S01"));
    await user.click(screen.getByRole("button", { name: "固定引用" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      action: "bind",
      expected_revision: 17,
      asset_id: "asset_fixture",
      version: 2,
      shot_ids: ["S01"],
      role: "female",
    });
  });
});

const tasks = [
  {
    intent_id: "intent-A",
    task_id: "supplier-A",
    shot_id: "S01",
    currency: "USD",
    confirmed_cost_micros: null,
  },
  {
    intent_id: "intent-B",
    task_id: "supplier-B",
    shot_id: "S02",
    currency: "CNY",
    confirmed_cost_micros: null,
  },
];
describe("supplier cost confirmation binds the immutable intent", () => {
  it("does not transfer task A's amount to B when polling removes A", async () => {
    const user = userEvent.setup(),
      submit = vi.fn();
    const view = render(
      <FeeConfirmation tasks={tasks} busy={false} onSubmit={submit} />,
    );
    await user.click(screen.getByText("按供应商账单确认实际费用"));
    await user.selectOptions(screen.getByLabelText("任务"), "intent-A");
    await user.type(screen.getByLabelText("实际金额"), "12.50");
    await user.type(screen.getByLabelText("核对依据"), "Task A invoice");
    expect(screen.getByLabelText("币种")).toHaveValue("USD");
    view.rerender(
      <FeeConfirmation tasks={[tasks[1]]} busy={false} onSubmit={submit} />,
    );
    expect(screen.getByLabelText("任务")).toHaveValue("intent-A");
    expect(screen.getByLabelText("实际金额")).toHaveValue("12.50");
    expect(screen.getByLabelText("核对依据")).toHaveValue("Task A invoice");
    expect(screen.getByRole("button", { name: "确认实际费用" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "确认实际费用" }));
    expect(submit).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText("任务"), "intent-B");
    expect(screen.getByLabelText("实际金额")).toHaveValue("");
    expect(screen.getByLabelText("核对依据")).toHaveValue("");
    expect(screen.getByLabelText("币种")).toHaveValue("CNY");
    await user.type(screen.getByLabelText("实际金额"), "3.125001");
    await user.type(screen.getByLabelText("核对依据"), "Task B invoice");
    await user.click(screen.getByRole("button", { name: "确认实际费用" }));
    expect(submit).toHaveBeenCalledExactlyOnceWith({
      intent_id: "intent-B",
      amount_micros: 3125001,
      currency: "CNY",
      evidence: "Task B invoice",
    });
  });

  it("stops an already confirmed task and preserves its draft for reconciliation", async () => {
    const user = userEvent.setup(),
      submit = vi.fn();
    const view = render(
      <FeeConfirmation tasks={tasks} busy={false} onSubmit={submit} />,
    );
    await user.click(screen.getByText("按供应商账单确认实际费用"));
    await user.selectOptions(screen.getByLabelText("任务"), "intent-A");
    await user.type(screen.getByLabelText("实际金额"), "4");
    await user.type(screen.getByLabelText("核对依据"), "invoice for A");
    view.rerender(
      <FeeConfirmation
        tasks={[{ ...tasks[0], confirmed_cost_micros: 5000000 }, tasks[1]]}
        busy={false}
        onSubmit={submit}
      />,
    );
    expect(screen.getByText(/此任务已被确认或币种发生变化/)).toBeVisible();
    expect(screen.getByLabelText("实际金额")).toHaveValue("4");
    expect(screen.getByRole("button", { name: "确认实际费用" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });
});

const quote = {
  id: "batch-A",
  estimated_submissions: 2,
  estimated_total_micros: 4000000,
  pricing: {
    unit_cost_micros: 2000000,
    currency: "USD",
    source: "fixture pricing",
  },
};
describe("cost page feedback stays outside the ledger layout", () => {
  it("starts with the cost panel when no authorization or fee form is needed", async () => {
    captureRequests(() => ({ tasks: [], totals: {}, authorizations: [] }));
    const { container } = render(<Costs project={project} notify={vi.fn()} />, {
      wrapper: queryWrapper().wrapper,
    });
    await screen.findByRole("heading", { name: "云媒体费用与授权" });
    const stack = container.querySelector(".stack")!;
    expect(stack.firstElementChild).toHaveClass("panel");
    expect(stack.querySelector(".form-feedback-slot")).toBeNull();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a failed cost confirmation in an overlay and retains the entered amount and evidence", async () => {
    const user = userEvent.setup(),
      notify = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (_path: string, init?: RequestInit) =>
          new Response(
            JSON.stringify(
              init?.method === "POST"
                ? { error: "账单服务暂时不可用" }
                : { tasks, totals: {}, authorizations: [] },
            ),
            {
              status: init?.method === "POST" ? 503 : 200,
              headers: { "Content-Type": "application/json" },
            },
          ),
      ),
    );
    const { container } = render(<Costs project={project} notify={notify} />, {
      wrapper: queryWrapper().wrapper,
    });
    await user.click(await screen.findByText("按供应商账单确认实际费用"));
    await user.selectOptions(screen.getByLabelText("任务"), "intent-A");
    await user.type(screen.getByLabelText("实际金额"), "12.50");
    await user.type(screen.getByLabelText("核对依据"), "Task A invoice");
    await user.click(screen.getByRole("button", { name: "确认实际费用" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("账单服务暂时不可用");
    expect(alert.closest(".feedback-region")).not.toBeNull();
    expect(container.querySelector(".stack")!.contains(alert)).toBe(false);
    expect(container.querySelector(".stack")!.firstElementChild).toHaveClass(
      "panel",
    );
    expect(screen.getByLabelText("任务")).toHaveValue("intent-A");
    expect(screen.getByLabelText("实际金额")).toHaveValue("12.50");
    expect(screen.getByLabelText("核对依据")).toHaveValue("Task A invoice");
    expect(screen.getByRole("button", { name: "确认实际费用" })).toBeEnabled();
    expect(notify).not.toHaveBeenCalled();
  });
});
describe("cloud authorization is bound to the reviewed quote", () => {
  it("preserves a stale cap draft and requires an explicit review of a new batch", async () => {
    const user = userEvent.setup(),
      submit = vi.fn();
    const view = render(
      <CloudAuthorization
        quote={quote}
        projectRevision={17}
        busy={false}
        onSubmit={submit}
      />,
    );
    fireEvent.change(screen.getByLabelText("累计估价额度 / USD"), {
      target: { value: "9.125001" },
    });
    view.rerender(
      <CloudAuthorization
        quote={{ ...quote, id: "batch-B", estimated_total_micros: 6000000 }}
        projectRevision={18}
        busy={false}
        onSubmit={submit}
      />,
    );
    expect(screen.getByLabelText("累计估价额度 / USD")).toHaveValue("9.125001");
    expect(
      screen.getByRole("button", { name: "明确授权本批次" }),
    ).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "重新核对当前批次" }));
    expect(screen.getByLabelText("累计估价额度 / USD")).toHaveValue("6");
    await user.click(screen.getByRole("button", { name: "明确授权本批次" }));
    expect(submit).toHaveBeenCalledExactlyOnceWith({
      batch_id: "batch-B",
      expected_revision: 18,
      max_submissions: 2,
      max_estimated_cost_micros: 6000000,
      confirm_unknown_cost: false,
    });
  });

  it("requires explicit unknown-price consent and sends a bounded submission count", async () => {
    const user = userEvent.setup(),
      submit = vi.fn();
    render(
      <CloudAuthorization
        quote={{
          ...quote,
          pricing: { ...quote.pricing, unit_cost_micros: null },
          estimated_total_micros: null,
        }}
        projectRevision={17}
        busy={false}
        onSubmit={submit}
      />,
    );
    const consent = screen.getByRole("checkbox", { name: /单价未确认/ });
    expect(consent).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "明确授权本批次" }));
    expect(submit).not.toHaveBeenCalled();
    await user.click(consent);
    await user.click(screen.getByRole("button", { name: "明确授权本批次" }));
    expect(submit).toHaveBeenCalledExactlyOnceWith({
      batch_id: "batch-A",
      expected_revision: 17,
      max_submissions: 2,
      max_estimated_cost_micros: null,
      confirm_unknown_cost: true,
    });
  });
});

function shotBoard(): RecordData {
  return {
    project_id: project.id,
    revision: 17,
    production_revision: 2,
    editable: true,
    errors: [],
    shots: [
      {
        id: "S01",
        seconds: 4,
        dramatic_beat: "发现失物",
        status: "passed",
        prompt: "Original character keyframe",
        motion_prompt: "Slow camera push in",
        camera_motion: "push_in",
        shot_revision: 1,
        candidates: [
          {
            id: "video:take_003",
            stage: "video",
            take: "take_003",
            status: "passed",
            selectable: true,
            plan_current: true,
            media_url:
              "/media/production/" +
              project.slug +
              "/assets/videos/S01/take_003/movie.mp4",
            qa: { eligible_for_selection: true, technical: { status: "pass" } },
            parameters: {},
            receipt: { model: "fixture-video" },
          },
        ],
      },
    ],
  };
}
async function openShotEditor(user: ReturnType<typeof userEvent.setup>) {
  const summary = screen.getByText("单镜精修").closest("summary")!;
  if (!summary.closest("details")!.open) await user.click(summary);
}
describe("the actual shot workbench submits the Python mutation contract", () => {
  it("shows videos before image candidates and keeps playback and selection through editor toggles and polling", async () => {
    const user = userEvent.setup();
    let board = shotBoard();
    board.shots[0].candidates.unshift({
      id: "image:take_001",
      stage: "image",
      take: "take_001",
      status: "passed",
      selectable: true,
      media_url:
        "/media/production/" +
        project.slug +
        "/assets/images/S01/take_001/frame.png",
    });
    captureRequests(() => board);
    const { client, wrapper } = queryWrapper();
    client.setQueryData(keys.shots(project.id), board);
    const view = render(<Shots project={project} notify={vi.fn()} />, {
      wrapper,
    });
    await waitFor(() =>
      expect(client.isFetching({ queryKey: keys.shots(project.id) })).toBe(0),
    );
    const video = view.container.querySelector("video")!;
    const editor = screen.getByText("单镜精修").closest("details")!;
    expect(editor).not.toHaveAttribute("open");
    expect(
      video.compareDocumentPosition(editor) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(view.container.querySelector(".candidate-card video")).toBe(
      view.container.querySelector(".candidate-card")!.querySelector("video"),
    );
    expect(screen.getByLabelText("首帧提示词")).not.toBeVisible();
    video.currentTime = 1.25;
    const consent = screen.getAllByRole("checkbox", {
      name: /确认采用此候选/,
    })[0];
    await user.click(consent);
    await openShotEditor(user);
    fireEvent.change(screen.getByLabelText("首帧提示词"), {
      target: { value: "Draft remains while comparing candidates" },
    });
    await user.click(screen.getByText("单镜精修"));
    board = {
      ...board,
      shots: [{ ...board.shots[0], prompt: "Background refresh" }],
    };
    await act(async () => {
      client.setQueryData(keys.shots(project.id), board);
    });
    expect(view.container.querySelector("video")).toBe(video);
    expect(video.currentTime).toBe(1.25);
    expect(consent).toBeChecked();
    await openShotEditor(user);
    expect(screen.getByLabelText("首帧提示词")).toHaveValue(
      "Draft remains while comparing candidates",
    );
  });

  it("retains separate shot drafts across shot selection and route remounts", async () => {
    const user = userEvent.setup(),
      board = shotBoard();
    board.shots.push({
      ...board.shots[0],
      id: "S02",
      prompt: "Second shot",
      candidates: [],
    });
    captureRequests(() => board);
    const { client, wrapper } = queryWrapper();
    client.setQueryData(keys.shots(project.id), board);
    const view = render(<Shots project={project} notify={vi.fn()} />, {
      wrapper,
    });
    await waitFor(() =>
      expect(client.isFetching({ queryKey: keys.shots(project.id) })).toBe(0),
    );
    await openShotEditor(user);
    fireEvent.change(screen.getByLabelText("首帧提示词"), {
      target: { value: "First shot draft" },
    });
    await user.click(screen.getByText("单镜精修"));
    expect(screen.getByLabelText("首帧提示词")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: /S02 4 秒/ }));
    await openShotEditor(user);
    fireEvent.change(screen.getByLabelText("首帧提示词"), {
      target: { value: "Second shot draft" },
    });
    await user.click(screen.getByRole("button", { name: /S01 4 秒/ }));
    expect(screen.getByLabelText("首帧提示词")).toHaveValue("First shot draft");
    expect(screen.getByLabelText("首帧提示词")).toBeVisible();
    view.unmount();
    render(<Shots project={project} initialShot="S02" notify={vi.fn()} />, {
      wrapper,
    });
    expect(screen.getByLabelText("首帧提示词")).toHaveValue(
      "Second shot draft",
    );
  });

  it("keeps in-flight shot edits locked even after leaving and returning", async () => {
    const user = userEvent.setup();
    let board = shotBoard();
    let finish!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url, init) => {
        if (init?.method === "POST")
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        return new Response(JSON.stringify(board));
      }),
    );
    const { client, wrapper } = queryWrapper();
    client.setQueryData(keys.shots(project.id), board);
    const view = render(<Shots project={project} notify={vi.fn()} />, {
      wrapper,
    });
    await waitFor(() =>
      expect(client.isFetching({ queryKey: keys.shots(project.id) })).toBe(0),
    );
    await openShotEditor(user);
    fireEvent.change(screen.getByLabelText("首帧提示词"), {
      target: { value: "Saved shot draft" },
    });
    await user.click(screen.getByRole("button", { name: "保存局部制作版本" }));
    expect(screen.getByLabelText("首帧提示词")).toBeDisabled();
    view.unmount();
    render(<Shots project={project} notify={vi.fn()} />, { wrapper });
    expect(screen.getByLabelText("首帧提示词")).toBeDisabled();
    expect(screen.getByLabelText("首帧提示词")).toHaveValue("Saved shot draft");
    board = {
      ...board,
      revision: 18,
      shots: [{ ...board.shots[0], prompt: "Saved shot draft" }],
    };
    await act(async () => {
      finish(new Response("{}"));
    });
    await waitFor(() =>
      expect(screen.getByLabelText("首帧提示词")).toBeEnabled(),
    );
    expect(screen.getByLabelText("首帧提示词")).toHaveValue("Saved shot draft");
  });

  it("keeps previews available and editing and selection read-only during production", async () => {
    const user = userEvent.setup();
    const board = { ...shotBoard(), editable: false };
    captureRequests(() => board);
    const { client, wrapper } = queryWrapper();
    client.setQueryData(keys.shots(project.id), board);
    const view = render(<Shots project={project} notify={vi.fn()} />, {
      wrapper,
    });
    expect(screen.getByText("当前只读")).toBeVisible();
    expect(view.container.querySelector("video")).toHaveAttribute("controls");
    expect(
      screen.getByRole("checkbox", { name: /确认采用此候选/ }),
    ).toBeDisabled();
    await openShotEditor(user);
    expect(screen.getByLabelText("首帧提示词")).toBeDisabled();
    expect(screen.getByLabelText("程序运镜")).toBeDisabled();
  });

  it("sends an explicit video retake with its original revision and visual edit fields", async () => {
    const user = userEvent.setup(),
      board = shotBoard(),
      posts = captureRequests(() => board);
    const { client, wrapper } = queryWrapper();
    client.setQueryData(keys.shots(project.id), board);
    render(<Shots project={project} notify={vi.fn()} />, { wrapper });
    await waitFor(() =>
      expect(client.isFetching({ queryKey: keys.shots(project.id) })).toBe(0),
    );
    await openShotEditor(user);
    fireEvent.change(screen.getByLabelText("视频提示词"), {
      target: { value: "A gentler camera movement" },
    });
    await user.selectOptions(screen.getByLabelText("重制范围"), "video");
    await user.click(
      screen.getByRole("checkbox", { name: /明确创建新的生成版本/ }),
    );
    await user.click(screen.getByRole("button", { name: "保存局部制作版本" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      path: "/api/factory/projects/" + project.id + "/shots/S01",
      body: {
        action: "edit",
        expected_revision: 17,
        changes: {
          prompt: "Original character keyframe",
          motion_prompt: "A gentler camera movement",
          camera_motion: "push_in",
        },
        regenerate: true,
        stage: "video",
      },
    });
  });

  it("preserves a shot edit during a server update and never silently rebinds its revision", async () => {
    const user = userEvent.setup();
    let board = shotBoard();
    const posts = captureRequests(() => board),
      { client, wrapper } = queryWrapper();
    client.setQueryData(keys.shots(project.id), board);
    render(<Shots project={project} notify={vi.fn()} />, { wrapper });
    await waitFor(() =>
      expect(client.isFetching({ queryKey: keys.shots(project.id) })).toBe(0),
    );
    await openShotEditor(user);
    fireEvent.change(screen.getByLabelText("首帧提示词"), {
      target: { value: "Producer draft awaiting review" },
    });
    board = {
      ...board,
      revision: 18,
      shots: [{ ...board.shots[0], prompt: "External edit" }],
    };
    await act(async () => {
      client.setQueryData(keys.shots(project.id), board);
    });
    expect(screen.getByLabelText("首帧提示词")).toHaveValue(
      "Producer draft awaiting review",
    );
    await waitFor(() =>
      expect(screen.getByText(/草稿仍绑定旧版本/)).toBeVisible(),
    );
    expect(
      screen.getByRole("checkbox", { name: /确认采用此候选/ }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "保存局部制作版本" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存局部制作版本" }));
    expect(posts).toHaveLength(0);
    await user.click(
      screen.getByRole("button", { name: "放弃草稿并读取新版本" }),
    );
    expect(screen.getByLabelText("首帧提示词")).toHaveValue("External edit");
  });

  it("requires fresh explicit candidate consent after a project revision changes", async () => {
    const user = userEvent.setup();
    let board = shotBoard();
    const posts = captureRequests(() => board),
      { client, wrapper } = queryWrapper();
    client.setQueryData(keys.shots(project.id), board);
    render(<Shots project={project} notify={vi.fn()} />, { wrapper });
    await waitFor(() =>
      expect(client.isFetching({ queryKey: keys.shots(project.id) })).toBe(0),
    );
    const consent = screen.getByRole("checkbox", { name: /确认采用此候选/ });
    await user.click(consent);
    board = { ...board, revision: 18 };
    await act(async () => {
      client.setQueryData(keys.shots(project.id), board);
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "设为选定素材" }),
      ).toBeDisabled(),
    );
    expect(posts).toHaveLength(0);
    await user.click(consent);
    await user.click(consent);
    await user.click(screen.getByRole("button", { name: "设为选定素材" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({
      action: "select",
      stage: "video",
      take: "take_003",
      confirmed: true,
      expected_revision: 18,
    });
  });
});
