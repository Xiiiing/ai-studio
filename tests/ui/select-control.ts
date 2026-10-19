import { screen } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";

/** Read the real successful form control behind a custom combobox. */
export function nativeControl(element: HTMLElement): HTMLElement {
  if (
    element.getAttribute("role") !== "combobox" ||
    element.tagName === "SELECT"
  )
    return element;
  const native = element.previousElementSibling;
  if (!(native instanceof HTMLSelectElement))
    throw new Error("Combobox has no backing form control");
  return native;
}

/** Exercise the visible Radix surface while keeping fixtures expressed in values. */
export async function selectControl(
  element: HTMLElement,
  values: string | string[],
  user:
    | Pick<UserEvent, "click" | "keyboard" | "selectOptions">
    | Pick<
        typeof userEvent,
        "click" | "keyboard" | "selectOptions"
      > = userEvent,
) {
  const native = nativeControl(element);
  if (!(native instanceof HTMLSelectElement))
    throw new Error("Expected select-backed control");
  if (element.tagName === "SELECT") return user.selectOptions(element, values);
  for (const value of Array.isArray(values) ? values : [values]) {
    const option = Array.from(native.options).find(
      (item) => item.value === value,
    );
    if (!option) throw new Error(`Missing option ${value}`);
    if (element.getAttribute("aria-expanded") !== "true")
      await user.click(element);
    const role = native.multiple ? "menuitemcheckbox" : "option";
    const item = screen.getByRole(role, { name: option.label });
    await user.click(item);
  }
  if (native.multiple) await user.keyboard("{Escape}");
}
