import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { AppearanceSettings } from "../../ui/factory/src/AppearanceSettings";
import { ThemeProvider, THEME_STORAGE_KEY } from "../../ui/factory/src/Theme";
import { ACCENT_STORAGE_KEY } from "../../ui/factory/src/palettes";

afterEach(() => {
  localStorage.removeItem(ACCENT_STORAGE_KEY);
  localStorage.removeItem(THEME_STORAGE_KEY);
  delete document.documentElement.dataset.accent;
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.themePreference;
});

it("lets the user choose a named palette separately from display mode", async () => {
  render(<ThemeProvider><AppearanceSettings /></ThemeProvider>);
  const accents = screen.getByRole("group", { name: "主题色" });
  expect(within(accents).getAllByRole("radio")).toHaveLength(6);
  expect(within(accents).getByRole("radio", { name: "深灰蓝" })).toBeChecked();
  await userEvent.click(within(accents).getByRole("radio", { name: "雾紫" }));
  expect(localStorage.getItem(ACCENT_STORAGE_KEY)).toBe("iris");
  expect(document.documentElement.dataset.accent).toBe("iris");
  await userEvent.click(screen.getByRole("radio", { name: "深色" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(within(accents).getByRole("radio", { name: "雾紫" })).toBeChecked();
  expect(screen.getByRole("region", { name: "主题效果预览" })).toHaveTextContent("雾紫");
  expect(screen.getByText("选择后即时生效，保存在当前浏览器。")).toBeVisible();
});
