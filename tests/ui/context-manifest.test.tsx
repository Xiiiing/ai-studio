import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import {
  ContextManifest,
  type CallContextManifest,
} from "../../ui/factory/src/ContextManifest";

const manifest: CallContextManifest = {
  schema: "ai-studio.context-manifest.v1",
  project_version: { script_generation: 2 },
  prompt: { version: "1.0.0" },
  delivery: "response_received",
  budget: { fits: true, prompt_tokens: 300, context_tokens: 32768 },
  materials: [
    {
      field: "producer_feedback",
      label: "出品人反馈",
      sha256: "a".repeat(64),
      bytes: 24,
      state: "selected",
      reason: "包含在本次输入",
      summary: "结尾保留悬念",
      version: { version: 1 },
    },
  ],
};
it("shows exact input, its own version and budget without claiming comprehension", async () => {
  render(<ContextManifest value={manifest} />);
  await userEvent.click(screen.getByText("本次收到的材料 · 1 项"));
  expect(screen.getByText("结尾保留悬念")).toBeVisible();
  expect(screen.getByText(/不代表模型已正确理解或落实/)).toBeVisible();
  await userEvent.click(screen.getByText("来源与版本"));
  expect(screen.getByText("材料版本：version: 1")).toBeVisible();
  expect(screen.getByText(/窗口检查：通过/)).toBeVisible();
});
it("separates reuse from a new call and keeps legacy origin unknown", () => {
  const { rerender } = render(<ContextManifest reused />);
  expect(screen.getByText(/原始调用来源未知/)).toBeVisible();
  rerender(
    <ContextManifest value={manifest} reused originCall="original-call" />,
  );
  expect(screen.getByText(/未发起新的模型调用/)).toHaveTextContent(
    "original-call",
  );
});
it("does not label an overflowing unsent input as delivered", async () => {
  render(
    <ContextManifest
      value={{
        ...manifest,
        delivery: "not_submitted",
        budget: { fits: false },
      }}
    />,
  );
  await userEvent.click(screen.getByText(/本次收到的材料/));
  expect(screen.getByText(/生成请求尚未提交/)).toBeVisible();
  expect(screen.getByText(/超限，已阻止本次提交/)).toBeVisible();
});
