import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { CreativeQualityPanel } from "../../ui/factory/src/CreativeQualityPanel";

it("shows the current evidence and distinguishes author choice from automatic repair", () => {
  render(<QueryClientProvider client={new QueryClient()}><CreativeQualityPanel project={{ id: "111122223333", creative_quality_contract: { version: "ai-studio.creative-quality.v1" }, draft_review: { issues: [{ nature: "AESTHETIC", severity: "minor", description: "选择开放结局或明确结局", location: "S02", evidence: "最后一镜留有空白", repair_target: "由作者确定结局方向", requires_choice: true }] } }} /></QueryClientProvider>);
  expect(screen.getByText("审美选择")).toBeInTheDocument();
  expect(screen.getByText("需要作者选择")).toBeInTheDocument();
  expect(screen.getByText("证据：最后一镜留有空白")).toBeInTheDocument();
});

it("does not enable an existing project without explicit adoption", () => {
  render(<QueryClientProvider client={new QueryClient()}><CreativeQualityPanel project={{ id: "111122223333" }} /></QueryClientProvider>);
  expect(screen.getByRole("button", { name: "启用新审核契约" })).toBeDisabled();
  expect(screen.getByText(/此项目沿用原审核契约/)).toBeInTheDocument();
});


it("requests only the explicit authorized existing-review operation", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((_url, options) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            enabled: true,
            recheck: options?.method === "POST" ? { task: { status: "queued" } } : {},
          }),
          { status: 200 },
        ),
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  try {
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <CreativeQualityPanel
          project={{
            id: "111122223333",
            revision: 12,
            creative_quality_contract: {
              version: "ai-studio.creative-quality.v1",
            },
          }}
        />
      </QueryClientProvider>,
    );
    const button = screen.getByRole("button", { name: "复核现有稿件与成片" });
    expect(button).toBeDisabled();
    await userEvent.click(
      screen.getByLabelText("授权本次已有内网文字与视觉模型复核"),
    );
    await userEvent.click(button);
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(
          ([url, options]) =>
            String(url).endsWith("/creative-quality/review") &&
            options?.method === "POST",
        ),
      ).toBe(true),
    );
    const [, options] = fetcher.mock.calls.find(
      ([url, options]) =>
        String(url).endsWith("/creative-quality/review") &&
        options?.method === "POST",
    )!;
    expect(JSON.parse(options.body)).toMatchObject({
      expected_revision: 12,
      confirmed: true,
      authorize_review: true,
    });
    expect(
      fetcher.mock.calls.filter(([, options]) => options?.method === "POST"),
    ).toHaveLength(1);
  } finally {
    vi.unstubAllGlobals();
  }
});



it("separates mapped labels from semantic evidence and shows missing events", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CreativeQualityPanel
        project={{
          id: "111122223333",
          creative_quality_contract: {
            version: "ai-studio.creative-quality.v1",
          },
          creative_quality: {
            coverage: {
              status: "fail",
              semantic_status: "not_reviewed",
              events: [
                {
                  id: "B1",
                  description: "把钥匙交给乙",
                  source_refs: ["src_original"],
                },
              ],
              mapping: [],
              missing: ["B1"],
              invalid: [],
            },
          },
        }}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText(/缺少必要事件：B1/)).toBeInTheDocument();
  expect(screen.getByText(/尚未建立当前版本结论/)).toBeInTheDocument();
  expect(screen.getByText(/引用完整不代表画面已经发生/)).toBeInTheDocument();
});
