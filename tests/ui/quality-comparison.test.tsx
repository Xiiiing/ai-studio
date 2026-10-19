import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { QualityComparison } from "../../ui/factory/src/QualityComparison";

const project = { id: "012345abcdef", revision: 7 };
const first = {
  id: "a".repeat(32),
  label: "分镜优化前",
  created_at: "2026-10-19T02:00:00Z",
  project_revision: 7,
  versions: { production_revision: 1 },
  input_sha256: "abc",
  project_sha256: "def",
  source_sha256: "ghi",
  gaps: [],
  metrics: {
    observed_operations: 1,
    failed_operations: 1,
    elapsed_seconds_sum: null,
    elapsed_known_count: 0,
    schema_retries: null,
    review_repairs: {},
    costs: [],
  },
  records: [],
  reviews: [],
};
const second = {
  ...first,
  id: "b".repeat(32),
  label: "分镜优化后",
  project_revision: 8,
};
let saved: (typeof first)[];
let mock: ReturnType<typeof vi.fn>;
let failSave = false;
beforeEach(() => {
  saved = [];
  failSave = false;
  mock = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === "POST") {
      if (failSave)
        return new Response(
          JSON.stringify({ error: "项目已更新，请同步后重试" }),
          { status: 409 },
        );
      const payload = JSON.parse(init.body as string);
      const result = { ...first, id: payload.request_id, label: payload.label };
      saved.unshift(result);
      return new Response(JSON.stringify(result));
    }
    if (url.includes("/compare?"))
      return new Response(
        JSON.stringify({
          baseline: first,
          candidate: second,
          same_inputs: false,
          reasons: ["需求发生变化，不能作为同输入效果实验"],
          review_changes: [],
          quality_conclusion: "not_established",
          note: "历史记录比较",
        }),
      );
    if (/quality-baselines\/[ab]{32}$/.test(url))
      return new Response(
        JSON.stringify(url.endsWith(second.id) ? second : first),
      );
    return new Response(
      JSON.stringify({ project_id: project.id, baselines: saved }),
    );
  });
  vi.stubGlobal("fetch", mock);
});

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <QualityComparison project={project} />
    </QueryClientProvider>,
  );
}

it("freezes only on explicit action with the displayed revision and a durable retry identity", async () => {
  mount();
  await screen.findByText("尚未保存质量基线");
  expect(
    mock.mock.calls.filter(([, init]) => init.method === "POST"),
  ).toHaveLength(0);
  await userEvent.type(screen.getByLabelText("快照名称"), "第一份基线");
  await userEvent.click(screen.getByRole("button", { name: "冻结当前版本" }));
  await waitFor(() => expect(saved).toHaveLength(1));
  const writes = mock.mock.calls.filter(([, init]) => init.method === "POST");
  expect(JSON.parse(writes[0][1].body)).toMatchObject({
    label: "第一份基线",
    expected_revision: 7,
  });
  expect(JSON.parse(writes[0][1].body).request_id).toMatch(/^[a-f0-9]{32}$/);
});

it("keeps unknown costs and quality unknown while showing input mismatch", async () => {
  saved = [second, first];
  mount();
  await screen.findByLabelText("基线版本");
  await userEvent.selectOptions(screen.getByLabelText("基线版本"), first.id);
  await userEvent.selectOptions(screen.getByLabelText("候选版本"), second.id);
  await screen.findByText("尚未建立真实效果改善结论");
  expect(
    screen.getByText("需求发生变化，不能作为同输入效果实验"),
  ).toBeVisible();
  expect(
    screen.getAllByText("费用：未知（未记录可汇总的云任务费用）").length,
  ).toBeGreaterThan(0);
  expect(
    screen.getByText("匿名效果评测尚未运行。", { exact: false }),
  ).toBeVisible();
});

it("does not claim an improvement when the same snapshot is selected twice", async () => {
  saved = [first];
  mount();
  await screen.findByLabelText("基线版本");
  await userEvent.selectOptions(screen.getByLabelText("基线版本"), first.id);
  await userEvent.selectOptions(screen.getByLabelText("候选版本"), first.id);
  expect(screen.getByText("请选择两份不同的基线。")).toBeVisible();
  expect(mock.mock.calls.some(([url]) => url.includes("/compare?"))).toBe(
    false,
  );
});

it("retries with the original id after an uncertain save and offers a fresh synchronization", async () => {
  failSave = true;
  mount();
  await screen.findByLabelText("快照名称");
  await userEvent.type(screen.getByLabelText("快照名称"), "超时重试");
  await userEvent.click(screen.getByRole("button", { name: "冻结当前版本" }));
  await screen.findByText("项目已更新，请同步后重试");
  await userEvent.click(screen.getByRole("button", { name: "重试保存此快照" }));
  await waitFor(() =>
    expect(
      mock.mock.calls.filter(([, init]) => init.method === "POST"),
    ).toHaveLength(2),
  );
  const writes = mock.mock.calls.filter(([, init]) => init.method === "POST");
  expect(writes[0][1].body).toEqual(writes[1][1].body);
  await userEvent.click(screen.getByRole("button", { name: "同步并重新准备" }));
  expect(screen.getByLabelText("快照名称")).toBeEnabled();
});
