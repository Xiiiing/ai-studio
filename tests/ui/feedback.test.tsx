import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  ErrorNotice,
  FloatingErrorNotice,
  FeedbackProvider,
  Modal,
} from "../../ui/factory/src/ui";

afterEach(() => vi.useRealTimers());

describe("inline and floating error feedback", () => {
  it("keeps inline errors in their form with contextual children", () => {
    render(
      <form aria-label="连接表单">
        <ErrorNotice error={new Error("URL 无法连接")}>
          <a href="#connection-url">检查服务地址</a>
        </ErrorNotice>
      </form>,
    );
    const form = screen.getByRole("form", { name: "连接表单" });
    expect(within(form).getByRole("alert")).toHaveTextContent("URL 无法连接");
    expect(
      within(form).getByRole("link", { name: "检查服务地址" }),
    ).toBeVisible();
    expect(
      within(form).queryByRole("button", { name: "关闭错误提示" }),
    ).not.toBeInTheDocument();
  });

  it("does not show an alert for an empty floating error", () => {
    const view = render(<FloatingErrorNotice error="" />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    view.rerender(<FloatingErrorNotice error={undefined} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps a request error visible past the ordinary seven-second toast timeout", () => {
    vi.useFakeTimers();
    render(<FloatingErrorNotice error="项目读取失败" />);
    expect(screen.getByRole("alert")).toHaveTextContent("项目读取失败");
    act(() => vi.advanceTimersByTime(30_000));
    expect(screen.getByRole("alert")).toHaveTextContent("项目读取失败");
    fireEvent.click(screen.getByRole("button", { name: "关闭错误提示" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not redisplay a dismissed continuous error when polling repeats its text", () => {
    const view = render(<FloatingErrorNotice error="镜头请求失败" />);
    fireEvent.click(screen.getByRole("button", { name: "关闭错误提示" }));
    view.rerender(<FloatingErrorNotice error="镜头请求失败" />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    view.rerender(<FloatingErrorNotice error={new Error("镜头请求失败")} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a different error after dismissal and makes its close control keyboard reachable", () => {
    const view = render(<FloatingErrorNotice error="模型目录读取失败" />);
    fireEvent.click(screen.getByRole("button", { name: "关闭错误提示" }));
    view.rerender(
      <FloatingErrorNotice error={new Error("审核回执读取失败")} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("审核回执读取失败");
    const close = screen.getByRole("button", { name: "关闭错误提示" });
    close.focus();
    expect(close).toHaveFocus();
    expect(close).toHaveAttribute("type", "button");
  });

  it("dismissing transient feedback does not dismiss an inline correction requirement", () => {
    render(
      <>
        <form aria-label="模型设置">
          <ErrorNotice error="请填写 URL" />
        </form>
        <FloatingErrorNotice error="模型保存失败" />
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭错误提示" }));
    expect(screen.queryByText("模型保存失败")).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("form", { name: "模型设置" })).getByRole("alert"),
    ).toHaveTextContent("请填写 URL");
  });
});

it("allows the same text to reappear when a recovered request fails again", () => {
  const view = render(<FloatingErrorNotice error="镜头请求失败" />);
  fireEvent.click(screen.getByRole("button", { name: "关闭错误提示" }));
  view.rerender(<FloatingErrorNotice error="" />);
  view.rerender(<FloatingErrorNotice error="镜头请求失败" />);
  expect(screen.getByRole("alert")).toHaveTextContent("镜头请求失败");
});

it("merges simultaneous equal errors from different page requests", () => {
  render(
    <FeedbackProvider>
      <FloatingErrorNotice error="连接暂不可用" />
      <FloatingErrorNotice error={new Error("连接暂不可用")} />
    </FeedbackProvider>,
  );
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "关闭错误提示" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("reserves a stable inline form feedback slot without announcing an empty error", () => {
  const view = render(<ErrorNotice error="" reserveSpace />);
  const slot = view.container.querySelector(".form-feedback-slot");
  expect(slot).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  view.rerender(<ErrorNotice error="请检查模型地址" reserveSpace />);
  expect(view.container.querySelector(".form-feedback-slot")).toBe(slot);
  expect(within(slot as HTMLElement).getByRole("alert")).toHaveTextContent(
    "请检查模型地址",
  );
  view.rerender(<ErrorNotice error="" reserveSpace />);
  expect(view.container.querySelector(".form-feedback-slot")).toBe(slot);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("portals request feedback outside the panel and form layout", () => {
  const view = render(
    <main>
      <form aria-label="页面表单">
        <FloatingErrorNotice error="连接断开" />
      </form>
    </main>,
  );
  const alert = screen.getByRole("alert");
  expect(view.container.contains(alert)).toBe(false);
  const region = screen.getByRole("complementary", { name: "操作反馈" });
  expect(region.parentElement).toBe(document.body);
  expect(region.contains(alert)).toBe(true);
});

it("keeps provider-managed dismissal until that error recovers", () => {
  const component = (error: Error | string) => (
    <FeedbackProvider>
      <FloatingErrorNotice error={error} />
    </FeedbackProvider>
  );
  const view = render(component("请求失败"));
  fireEvent.click(screen.getByRole("button", { name: "关闭错误提示" }));
  view.rerender(component(new Error("请求失败")));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  view.rerender(component(""));
  view.rerender(component(new Error("请求失败")));
  expect(screen.getByRole("alert")).toHaveTextContent("请求失败");
});

it("opens the full inline error without submitting or losing form input, then restores focus on close and cancel", async () => {
  const user = userEvent.setup();
  const submit = vi.fn((event: React.FormEvent) => event.preventDefault());
  const error =
    "连接校验失败。" +
    "服务器返回了较长的协议与地址校验信息，请检查完整响应。".repeat(12) +
    "最终诊断：请求协议不匹配。";
  const form = (message: string) => (
    <form aria-label="模型连接" onSubmit={submit}>
      <label>
        服务地址
        <input name="url" defaultValue="" />
      </label>
      <ErrorNotice error={message} reserveSpace />
      <button type="submit">保存连接</button>
    </form>
  );
  const view = render(form(""));
  const address = screen.getByLabelText("服务地址");
  await user.type(address, "https://inference.example.test/v1");
  view.rerender(form(error));
  const details = screen.getByRole("button", { name: "查看详情" });
  expect(details).toHaveAttribute("type", "button");
  await user.click(details);
  const dialog = screen.getByRole("dialog", { name: "错误详情" });
  expect(within(dialog).getByText(error, { exact: true })).toBeVisible();
  expect(address).toHaveValue("https://inference.example.test/v1");
  expect(submit).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "关闭" }));
  expect(
    screen.queryByRole("dialog", { name: "错误详情" }),
  ).not.toBeInTheDocument();
  expect(details).toHaveFocus();
  await user.click(details);
  // Native dialogs dispatch cancel on Escape; jsdom does not implement that default action.
  fireEvent(
    screen.getByRole("dialog", { name: "错误详情" }),
    new Event("cancel", { cancelable: true }),
  );
  expect(
    screen.queryByRole("dialog", { name: "错误详情" }),
  ).not.toBeInTheDocument();
  expect(details).toHaveFocus();
  expect(address).toHaveValue("https://inference.example.test/v1");
  expect(submit).not.toHaveBeenCalled();
});

it("canceling nested error details keeps the parent modal and unsaved form input", async () => {
  const user = userEvent.setup(),
    closeParent = vi.fn();
  render(
    <Modal id="connection-parent" title="连接服务" onClose={closeParent}>
      <form aria-label="连接草稿">
        <label>
          服务地址
          <input defaultValue="" />
        </label>
        <ErrorNotice
          reserveSpace
          error="模型目录响应协议不匹配，请保留草稿核对地址。"
        />
      </form>
    </Modal>,
  );
  const parent = screen.getByRole("dialog", { name: "连接服务" });
  const input = within(parent).getByLabelText("服务地址");
  await user.type(input, "https://draft.example.test/v1");
  const details = within(parent).getByRole("button", { name: "查看详情" });
  await user.click(details);
  const child = screen.getByRole("dialog", { name: "错误详情" });
  fireEvent(child, new Event("cancel", { cancelable: true }));
  expect(closeParent).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "连接服务" })).toBeVisible();
  expect(
    screen.queryByRole("dialog", { name: "错误详情" }),
  ).not.toBeInTheDocument();
  expect(input).toHaveValue("https://draft.example.test/v1");
  expect(details).toHaveFocus();
});
