import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  ThemeControl,
  applyTheme,
  readTheme,
  THEME_STORAGE_KEY,
} from "../../ui/factory/src/Theme";

afterEach(() => {
  localStorage.removeItem(THEME_STORAGE_KEY);
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.themePreference;
  document.documentElement.style.colorScheme = "";
});

it("remembers an explicit theme and applies the browser color scheme", async () => {
  render(<ThemeControl />);
  await userEvent.click(
    screen.getByRole("button", { name: /外观主题：跟随系统/ }),
  );
  await userEvent.click(screen.getByRole("button", { name: /外观主题：浅色/ }));
  expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(document.documentElement.style.colorScheme).toBe("dark");
});

it("follows changing system appearance only in system mode", () => {
  applyTheme("system", true);
  expect(document.documentElement.dataset.theme).toBe("dark");
  applyTheme("system", false);
  expect(document.documentElement.dataset.theme).toBe("light");
  applyTheme("light", true);
  expect(document.documentElement.dataset.theme).toBe("light");
});

it("falls back to system for invalid or unavailable storage", () => {
  localStorage.setItem(THEME_STORAGE_KEY, "invalid");
  expect(readTheme()).toBe("system");
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("Storage unavailable");
  });
  expect(readTheme()).toBe("system");
});

it("keeps a session theme usable when storage writes fail", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("Storage unavailable");
  });
  render(<ThemeControl />);
  await userEvent.click(
    screen.getByRole("button", { name: /外观主题：跟随系统/ }),
  );
  await userEvent.click(screen.getByRole("button", { name: /外观主题：浅色/ }));
  expect(document.documentElement.dataset.theme).toBe("dark");
});

it("cycles all three preferences with a current and next-mode accessible name", async () => {
  render(<ThemeControl />);
  for (const [current, next, value] of [
    ["跟随系统", "浅色", "light"],
    ["浅色", "深色", "dark"],
    ["深色", "跟随系统", "system"],
  ]) {
    const control = screen.getByRole("button", {
      name: `外观主题：${current}；切换为${next}`,
    });
    expect(control).toHaveAttribute(
      "title",
      control.getAttribute("aria-label"),
    );
    await userEvent.click(control);
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe(value);
    expect(document.documentElement.dataset.themePreference).toBe(value);
  }
});
