import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  ThemeControl,
  applyTheme,
  readTheme,
  THEME_STORAGE_KEY,
  ThemeProvider,
  useAppearance,
} from "../../ui/factory/src/Theme";
import {
  ACCENT_PALETTES,
  ACCENT_STORAGE_KEY,
  readAccent,
} from "../../ui/factory/src/palettes";

afterEach(() => {
  localStorage.removeItem(THEME_STORAGE_KEY);
  localStorage.removeItem(ACCENT_STORAGE_KEY);
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.themePreference;
  delete document.documentElement.dataset.accent;
  document.documentElement.style.colorScheme = "";
});

function AppearanceChoices() {
  const { accent, chooseAccent, preference } = useAppearance();
  return (
    <>
      <output>{`${preference}:${accent}`}</output>
      {ACCENT_PALETTES.map((palette) => (
        <button key={palette.id} onClick={() => chooseAccent(palette.id)}>
          {palette.label}
        </button>
      ))}
      <ThemeControl />
    </>
  );
}

it("persists the accent independently and preserves it across mode switches and remounts", async () => {
  const view = render(
    <ThemeProvider>
      <AppearanceChoices />
    </ThemeProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "薄荷青" }));
  expect(localStorage.getItem(ACCENT_STORAGE_KEY)).toBe("teal");
  expect(document.documentElement.dataset.accent).toBe("teal");
  await userEvent.click(
    screen.getByRole("button", { name: /外观主题：跟随系统/ }),
  );
  await userEvent.click(screen.getByRole("button", { name: /外观主题：浅色/ }));
  expect(screen.getByRole("status")).toHaveTextContent("dark:teal");
  view.unmount();
  render(
    <ThemeProvider>
      <AppearanceChoices />
    </ThemeProvider>,
  );
  expect(screen.getByRole("status")).toHaveTextContent("dark:teal");
});

it("defaults invalid or blocked accent storage to slate", () => {
  localStorage.setItem(ACCENT_STORAGE_KEY, "javascript:purple");
  expect(readAccent()).toBe("slate");
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  expect(readAccent()).toBe("slate");
});

it("syncs accent and mode across tabs, including storage clear and invalid external values", async () => {
  render(
    <ThemeProvider>
      <AppearanceChoices />
    </ThemeProvider>,
  );
  act(() => {
    localStorage.setItem(ACCENT_STORAGE_KEY, "iris");
    window.dispatchEvent(
      new StorageEvent("storage", { key: ACCENT_STORAGE_KEY }),
    );
  });
  expect(document.documentElement.dataset.accent).toBe("iris");
  act(() => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    window.dispatchEvent(
      new StorageEvent("storage", { key: THEME_STORAGE_KEY }),
    );
  });
  expect(screen.getByRole("status")).toHaveTextContent("dark:iris");
  act(() => {
    localStorage.setItem(ACCENT_STORAGE_KEY, "invalid");
    window.dispatchEvent(
      new StorageEvent("storage", { key: ACCENT_STORAGE_KEY }),
    );
  });
  expect(screen.getByRole("status")).toHaveTextContent("dark:slate");
  act(() => {
    localStorage.clear();
    window.dispatchEvent(new StorageEvent("storage", { key: null }));
  });
  expect(screen.getByRole("status")).toHaveTextContent("system:slate");
});

it("retains the session accent when controls remount and browser storage fails", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  const view = render(
    <ThemeProvider>
      <AppearanceChoices />
    </ThemeProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "杏桃" }));
  view.rerender(
    <ThemeProvider>
      <span>another page</span>
    </ThemeProvider>,
  );
  view.rerender(
    <ThemeProvider>
      <AppearanceChoices />
    </ThemeProvider>,
  );
  expect(screen.getByRole("status")).toHaveTextContent("system:bronze");
  expect(document.documentElement.dataset.accent).toBe("bronze");
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

it("keeps root session preference when page controls unmount and storage is unavailable", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("unavailable");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("unavailable");
  });
  const { ThemeProvider } = await import("../../ui/factory/src/Theme");
  const view = render(
    <ThemeProvider>
      <ThemeControl />
    </ThemeProvider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: /外观主题：跟随系统/ }),
  );
  view.rerender(
    <ThemeProvider>
      <span>another page</span>
    </ThemeProvider>,
  );
  view.rerender(
    <ThemeProvider>
      <ThemeControl />
    </ThemeProvider>,
  );
  expect(
    screen.getByRole("button", { name: /外观主题：浅色/ }),
  ).toBeInTheDocument();
  expect(document.documentElement.dataset.themePreference).toBe("light");
});
