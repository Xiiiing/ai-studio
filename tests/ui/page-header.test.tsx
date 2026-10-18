import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import {
  PageHeader,
  PageHeaderProvider,
} from "../../ui/factory/src/PageHeader";
import { ThemeProvider } from "../../ui/factory/src/Theme";

it("keeps one title with real parent navigation, actions and shared tools", async () => {
  const refresh = vi.fn(),
    navigate = vi.fn();
  const navigationTrigger = createRef<HTMLButtonElement>();
  render(
    <ThemeProvider>
      <PageHeaderProvider
        value={{
          navigationOpen: false,
          navigationTrigger,
          toggleNavigation: navigate,
          syncError: true,
          syncing: false,
          refresh,
        }}
      >
        <PageHeader
          title="模型与连接"
          parent={<button>返回项目列表</button>}
          action={<button>添加服务</button>}
        />
      </PageHeaderProvider>
    </ThemeProvider>,
  );
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(screen.queryByText("工作室 /")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "展开导航" }));
  expect(navigate).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByRole("button", { name: "刷新项目" }));
  expect(refresh).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "外观设置" })).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("同步中断");
  expect(navigationTrigger.current).toBe(
    screen.getByRole("button", { name: "展开导航" }),
  );
});

it("closes page tools on Escape and restores keyboard focus", async () => {
  render(
    <PageHeaderProvider
      value={{
        navigationOpen: false,
        navigationTrigger: createRef<HTMLButtonElement>(),
        toggleNavigation: vi.fn(),
        syncError: false,
        syncing: false,
        refresh: vi.fn(),
      }}
    >
      <PageHeader title="总览" />
    </PageHeaderProvider>,
  );
  const tools = screen.getByRole("button", { name: "页面工具" });
  await userEvent.click(tools);
  expect(tools).toHaveAttribute("aria-expanded", "true");
  await userEvent.tab();
  await userEvent.keyboard("{Escape}");
  expect(tools).toHaveAttribute("aria-expanded", "false");
  expect(tools).toHaveFocus();
});
