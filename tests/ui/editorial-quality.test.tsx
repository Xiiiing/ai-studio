import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { CreativeQualityPanel } from "../../ui/factory/src/CreativeQualityPanel";

it("shows the current evidence and distinguishes author choice from automatic repair", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CreativeQualityPanel
        project={{
          id: "111122223333",
          creative_quality_contract: {
            version: "ai-studio.creative-quality.v1",
          },
          draft_review: {
            issues: [
              {
                nature: "AESTHETIC",
                severity: "minor",
                description: "选择开放结局或明确结局",
                location: "S02",
                evidence: "最后一镜留有空白",
                repair_target: "由作者确定结局方向",
                requires_choice: true,
              },
            ],
          },
        }}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("审美选择")).toBeInTheDocument();
  expect(screen.getByText("需要作者选择")).toBeInTheDocument();
  expect(screen.getByText("证据：最后一镜留有空白")).toBeInTheDocument();
});

it("does not enable an existing project without explicit adoption", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CreativeQualityPanel project={{ id: "111122223333" }} />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("button", { name: "启用新审核契约" })).toBeDisabled();
  expect(screen.getByText(/此项目沿用原审核契约/)).toBeInTheDocument();
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
