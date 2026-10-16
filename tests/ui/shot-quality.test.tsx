import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ShotQuality } from "../../ui/factory/src/ShotQuality";

const shots = Array.from({ length: 14 }, (_, i) => ({
  id: `S${String(i + 1).padStart(2, "0")}`,
  status: "passed",
  qa: {
    technical: {
      status: "pass",
      metrics: { nested: { explanation: "完整技术原文" } },
    },
    semantic: {
      status: i === 8 ? "fail" : "pass",
      summary: "当前画面审核结论",
      issues: [{ detail: "人物一致性提示" }],
    },
  },
  attempts: [{ attempt: 1, parameters: { width: 720, height: 1280 } }],
}));
describe("镜头质检摘要", () => {
  it("pages large real-shaped records and finds semantic failures even when the shot status is passed", async () => {
    const onShot = vi.fn(),
      user = userEvent.setup();
    const view = render(<ShotQuality shots={shots} onShot={onShot} />);
    expect(view.container.querySelectorAll(".quality-shot")).toHaveLength(6);
    expect(screen.getByText("14 镜记录 · 1 镜需关注")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "下一页镜头" }));
    await user.click(screen.getByRole("button", { name: "打开镜头 S09" }));
    expect(onShot).toHaveBeenCalledWith("S09");
    await user.click(
      screen.getByRole("button", { name: "需关注" }),
    );
    expect(view.container.querySelectorAll(".quality-shot")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "打开镜头 S09" })).toBeVisible();
    expect(
      screen.queryByRole("navigation", { name: "质检镜头分页" }),
    ).not.toBeInTheDocument();
  });
  it("keeps raw technical evidence collapsed and reachable without exposing it as the primary conclusion", async () => {
    const user = userEvent.setup();
    render(<ShotQuality shots={[shots[0]]} onShot={vi.fn()} />);
    expect(screen.getByText("当前画面审核结论", { selector: "p" })).toBeVisible();
    expect(screen.getByText("完整技术原文")).not.toBeVisible();
    await user.click(screen.getByText("完整质检记录", { exact: true }));
    expect(
      within(screen.getByLabelText("S01 完整质检记录")).getByText(
        "完整技术原文",
      ),
    ).toBeVisible();
    expect(screen.getByLabelText("S01 完整质检记录")).toHaveAttribute(
      "tabindex",
      "0",
    );
    await user.click(screen.getByText("1 条审核提示"));
    expect(screen.getByText("人物一致性提示", { selector: "li" })).toBeVisible();
  });
  it("clamps a disappearing last page and reports empty attention results accurately", async () => {
    const user = userEvent.setup(),
      onShot = vi.fn();
    const view = render(<ShotQuality shots={shots} onShot={onShot} />);
    await user.click(screen.getByRole("button", { name: "下一页镜头" }));
    await user.click(screen.getByRole("button", { name: "下一页镜头" }));
    view.rerender(<ShotQuality shots={shots.slice(0, 7)} onShot={onShot} />);
    expect(screen.getByRole("button", { name: "打开镜头 S07" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "上一页镜头" }));
    expect(screen.getByRole("button", { name: "打开镜头 S01" })).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: "需关注" }),
    );
    expect(screen.getByText("当前没有审核未通过的镜头")).toBeVisible();
  });
});
