import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ProjectNavigation } from "../../ui/factory/src/ProjectNavigation";

it.each([
  ["quality", "审核", "审核页面", "质检记录"],
  ["series", "素材", "素材页面", "系列资产"],
])(
  "keeps the legacy %s route inside its primary section",
  async (tab, section, label, page) => {
    const onTab = vi.fn();
    render(<ProjectNavigation tab={tab} onTab={onTab} attention />);
    const nav = screen.getByRole("navigation", { name: "项目内容" });
    expect(within(nav).getAllByRole("button")).toHaveLength(6);
    expect(within(nav).getByRole("button", { name: section })).toHaveAttribute(
      "aria-current",
      "page",
    );
    const sub = screen.getByRole("navigation", { name: label });
    expect(within(sub).getByRole("button", { name: page })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await userEvent.click(within(sub).getAllByRole("button")[0]);
    expect(onTab).toHaveBeenCalledWith(
      tab === "quality" ? "decisions" : "materials",
    );
  },
);
it("reaches team and costs and closes the more menu without losing keyboard focus", async () => {
  const onTab = vi.fn();
  render(<ProjectNavigation tab="overview" onTab={onTab} />);
  const trigger = screen.getByText("更多");
  await userEvent.click(trigger);
  await userEvent.click(screen.getByRole("menuitem", { name: "云任务与费用" }));
  expect(onTab).toHaveBeenCalledWith("costs");
  expect(trigger).toHaveFocus();
  await userEvent.click(trigger);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
