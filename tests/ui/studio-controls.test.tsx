import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, expect, it, vi } from "vitest";
import { useState } from "react";
import { StudioSelect } from "../../ui/factory/src/StudioSelect";
import { StudioMenu } from "../../ui/factory/src/StudioMenu";
import { Field } from "../../ui/factory/src/ui";

beforeAll(() => {
  for (const name of [
    "hasPointerCapture",
    "setPointerCapture",
    "releasePointerCapture",
  ])
    if (!(name in HTMLElement.prototype))
      Object.defineProperty(HTMLElement.prototype, name, {
        configurable: true,
        value: () => false,
      });
});

it("selects real options and retains native form values, defaults and reset", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  const view = render(
    <form aria-label="配音">
      <Field label="声线">
        <StudioSelect name="voice" defaultValue="female" onChange={onChange}>
          <option value="female">女声</option>
          <option value="male">男声</option>
          <option value="narrator" disabled>
            旁白
          </option>
        </StudioSelect>
      </Field>
      <button type="reset">重置</button>
    </form>,
  );
  const combo = screen.getByRole("combobox", { name: "声线" });
  const form = screen.getByRole("form") as HTMLFormElement;
  expect(new FormData(form).get("voice")).toBe("female");
  await user.click(combo);
  expect(screen.getByRole("option", { name: "旁白" })).toHaveAttribute(
    "data-disabled",
  );
  await user.click(screen.getByRole("option", { name: "男声" }));
  expect(combo).toHaveTextContent("男声");
  expect(new FormData(form).get("voice")).toBe("male");
  expect(onChange).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "重置" }));
  expect(combo).toHaveTextContent("女声");
  expect(new FormData(form).get("voice")).toBe("female");
  view.unmount();
});

it("preserves controlled values and blocks required empty submissions", async () => {
  const user = userEvent.setup();
  function Controlled() {
    const [value, setValue] = useState("");
    return (
      <form aria-label="配置">
        <Field label="模型">
          <StudioSelect
            name="model"
            value={value}
            required
            onChange={(event) => setValue(event.target.value)}
          >
            <option value="">请选择模型</option>
            <optgroup label="模型服务">
              <option value="qwen">Qwen</option>
            </optgroup>
          </StudioSelect>
        </Field>
      </form>
    );
  }
  render(<Controlled />);
  const form = screen.getByRole("form") as HTMLFormElement;
  expect(form.checkValidity()).toBe(false);
  const combo = screen.getByRole("combobox", { name: "模型" });
  expect(combo).toHaveFocus();
  await user.keyboard("{ArrowDown}");
  await user.click(screen.getByRole("option", { name: "Qwen" }));
  expect(form.checkValidity()).toBe(true);
  expect(new FormData(form).get("model")).toBe("qwen");
});

it("portals selects and nonmodal menus inside native dialogs and restores focus", async () => {
  const user = userEvent.setup();
  const action = vi.fn();
  render(
    <dialog open aria-label="编辑">
      <Field label="采样器">
        <StudioSelect defaultValue="euler">
          <option value="euler">Euler</option>
          <option value="heun">Heun</option>
        </StudioSelect>
      </Field>
      <StudioMenu
        label="更多"
        items={[
          { label: "复制", onSelect: action },
          { label: "删除", disabled: true, onSelect: action },
        ]}
      />
    </dialog>,
  );
  const dialog = screen.getByRole("dialog");
  const combo = within(dialog).getByRole("combobox");
  const originalOverflow = document.documentElement.style.overflow;
  await user.click(combo);
  expect(dialog.contains(screen.getByRole("listbox"))).toBe(true);
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(document.documentElement.style.overflow).toBe(originalOverflow),
  );
  expect(combo).toHaveFocus();
  const more = screen.getByRole("button", { name: "更多" });
  await user.click(more);
  expect(dialog.contains(screen.getByRole("menu"))).toBe(true);
  expect(document.documentElement.style.overflow).toBe(originalOverflow);
  await user.click(screen.getByRole("menuitem", { name: "复制" }));
  expect(action).toHaveBeenCalledOnce();
  await waitFor(() => expect(more).toHaveFocus());
});

it("supports checkbox selections and native FormData for multiple values", async () => {
  const user = userEvent.setup();
  render(
    <form aria-label="镜头">
      <Field label="适用镜头">
        <StudioSelect name="shots" multiple defaultValue={["one"]}>
          <option value="one">镜头一</option>
          <option value="two">镜头二</option>
        </StudioSelect>
      </Field>
    </form>,
  );
  await user.click(screen.getByRole("combobox", { name: "适用镜头" }));
  await user.click(screen.getByRole("menuitemcheckbox", { name: "镜头二" }));
  expect(
    new FormData(screen.getByRole("form") as HTMLFormElement).getAll("shots"),
  ).toEqual(["one", "two"]);
  await user.click(screen.getByRole("menuitemcheckbox", { name: "镜头一" }));
  expect(
    new FormData(screen.getByRole("form") as HTMLFormElement).getAll("shots"),
  ).toEqual(["two"]);
});

it("honors disabled fieldsets and custom native change events", async () => {
  const onChange = vi.fn();
  const view = render(
    <fieldset disabled>
      <Field label="模型">
        <StudioSelect name="model" onChange={onChange}>
          <option value="one">一</option>
          <option value="two">二</option>
        </StudioSelect>
      </Field>
    </fieldset>,
  );
  expect(screen.getByRole("combobox", { name: "模型" })).toBeDisabled();
  view.rerender(
    <Field label="模型">
      <StudioSelect name="model" onChange={onChange}>
        <option value="one">一</option>
        <option value="two">二</option>
      </StudioSelect>
    </Field>,
  );
  const native = view.container.querySelector("select[name='model']")!;
  fireEvent.change(native, { target: { value: "two" } });
  expect(onChange).toHaveBeenCalledOnce();
  expect(screen.getByRole("combobox", { name: "模型" })).toHaveTextContent(
    "二",
  );
});

it("keeps asynchronous option defaults aligned with the submitted form value", () => {
  const view = render(
    <form aria-label="异步模型">
      <Field label="模型">
        <StudioSelect name="model" />
      </Field>
    </form>,
  );
  view.rerender(
    <form aria-label="异步模型">
      <Field label="模型">
        <StudioSelect name="model">
          <option value="qwen">Qwen</option>
          <option value="other">其他模型</option>
        </StudioSelect>
      </Field>
    </form>,
  );
  expect(screen.getByRole("combobox", { name: "模型" })).toHaveTextContent(
    "Qwen",
  );
  expect(
    new FormData(screen.getByRole("form") as HTMLFormElement).get("model"),
  ).toBe("qwen");
});

it("respects a canceled form reset", async () => {
  const user = userEvent.setup();
  render(
    <form onReset={(event) => event.preventDefault()}>
      <Field label="声线">
        <StudioSelect name="voice" defaultValue="female">
          <option value="female">女声</option>
          <option value="male">男声</option>
        </StudioSelect>
      </Field>
      <button type="reset">重置</button>
    </form>,
  );
  const combo = screen.getByRole("combobox", { name: "声线" });
  await user.click(combo);
  await user.click(screen.getByRole("option", { name: "男声" }));
  await user.click(screen.getByRole("button", { name: "重置" }));
  expect(combo).toHaveTextContent("男声");
});
