import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { expect, it } from "vitest";
import {
  ACCENT_PALETTES,
  ACCENT_STORAGE_KEY,
} from "../../ui/factory/src/palettes";

const css = readFileSync("ui/factory/src/styles/accents.css", "utf8");
const initialScript = readFileSync("ui/factory/public/theme-init.js", "utf8");

function luminance(hex: string) {
  const rgb = hex
    .slice(1)
    .match(/../g)!
    .map((part) => parseInt(part, 16) / 255);
  const [r, g, b] = rgb.map((v) =>
    v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string) {
  const l = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l[0] + 0.05) / (l[1] + 0.05);
}
function colors(id: string, mode: string) {
  const selector =
    mode === "dark"
      ? `:root[data-theme="dark"][data-accent="${id}"]`
      : `:root[data-accent="${id}"]`;
  const body = css
    .slice(css.indexOf(selector) + selector.length)
    .match(/\{([^}]+)\}/)![1];
  return Object.fromEntries(
    [...body.matchAll(/--([a-z-]+):\s*(#[a-f\d]{6})/g)].map((m) => [
      m[1],
      m[2],
    ]),
  );
}

for (const palette of ACCENT_PALETTES) {
  for (const mode of ["light", "dark"]) {
    it(`${palette.id} ${mode} has readable actions, colored text, selection and visible focus`, () => {
      const c = colors(palette.id, mode);
      const surfaces =
        mode === "light"
          ? ["#f7f7f8", "#ffffff", "#f2f2f3"]
          : ["#171719", "#1e1e21", "#27272b"];
      expect(contrast(c.accent, c["on-primary"])).toBeGreaterThanOrEqual(4.5);
      expect(
        contrast(c["accent-hover"], c["on-primary"]),
      ).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c.accent, c["on-accent"])).toBeGreaterThanOrEqual(4.5);
      for (const background of [...surfaces, c["accent-soft"], c.selection]) {
        expect(contrast(c["accent-text"], background)).toBeGreaterThanOrEqual(
          4.5,
        );
      }
      for (const background of surfaces) {
        expect(contrast(c.accent, background)).toBeGreaterThanOrEqual(3);
      }
      if (mode === "light") expect(c.accent).toBe(palette.swatch);
    });
  }
}

it("only overrides accent roles, leaving neutral surfaces and semantic statuses unchanged", () => {
  const allowed = new Set([
    "primary",
    "primary-hover",
    "focus",
    "status-running",
    "crop-stroke",
    "accent",
    "accent-hover",
    "accent-text",
    "accent-soft",
    "selection",
    "on-primary",
    "on-accent",
    "crop-fill",
  ]);
  for (const match of css.matchAll(/--([a-z-]+):/g))
    expect(allowed.has(match[1])).toBe(true);
  const styles = readFileSync("ui/factory/src/styles.css", "utf8");
  expect(styles.indexOf('"./styles/accents.css"')).toBeGreaterThan(
    styles.indexOf('"./styles/tokens.css"'),
  );
  expect(css).toContain("--focus: var(--accent)");
  expect(css).toContain("--status-running: var(--accent)");
});

function initialize(savedAccent: string | null, dark = false, blocked = false) {
  const document = {
    documentElement: {
      dataset: {} as Record<string, string>,
      style: {} as Record<string, string>,
    },
    querySelector: () => null,
  };
  runInNewContext(initialScript, {
    document,
    localStorage: {
      getItem: (key: string) => {
        if (blocked) throw new Error("blocked");
        return key === ACCENT_STORAGE_KEY ? savedAccent : "system";
      },
    },
    matchMedia: () => ({ matches: dark }),
  });
  return document.documentElement;
}

it("applies every saved palette before render in both color modes", () => {
  for (const palette of ACCENT_PALETTES)
    for (const dark of [false, true]) {
      const root = initialize(palette.id, dark);
      expect(root.dataset.accent).toBe(palette.id);
      expect(root.dataset.theme).toBe(dark ? "dark" : "light");
      expect(root.dataset.themePreference).toBe("system");
    }
});

it("limits first-paint palette values to the same whitelist and survives blocked storage", () => {
  for (const invalid of [null, "", "blue", "TEAL", "__proto__", "<script>"]) {
    expect(initialize(invalid).dataset.accent).toBe("slate");
  }
  expect(initialize("jade", true, true).dataset.accent).toBe("slate");
  expect(initialize("jade", true, true).dataset.theme).toBe("dark");
});
