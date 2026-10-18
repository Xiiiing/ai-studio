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
function colors(id: string, mode: string): Record<string, string> {
  const selector =
    mode === "dark"
      ? `:root[data-theme="dark"][data-accent="${id}"]`
      : `:root[data-accent="${id}"]`;
  const body = css
    .slice(css.indexOf(selector) + selector.length)
    .match(/\{([^}]+)\}/)![1];
  return { ...(mode === "dark" ? colors(id, "light") : {}), ...Object.fromEntries(
    [...body.matchAll(/--([a-z-]+):\s*(#[a-f\d]{6})/g)].map((m) => [
      m[1],
      m[2],
    ]),
  ) };
}

function interpolate(a: string, b: string, t: number) {
  const channels = (hex: string) => hex.slice(1).match(/../g)!.map(v => parseInt(v, 16));
  const start = channels(a), end = channels(b);
  return `#${start.map((v, i) => Math.round(v + (end[i] - v) * t).toString(16).padStart(2, "0")).join("")}`;
}

for (const palette of ACCENT_PALETTES) {
  for (const mode of ["light", "dark"]) {
    it(`${palette.id} ${mode} has readable feature surfaces, colored text, selection and visible focus`, () => {
      const c = colors(palette.id, mode);
      const surfaces =
        mode === "light"
          ? ["#f7f7f8", "#ffffff", "#f2f2f3"]
          : ["#171719", "#1e1e21", "#27272b"];
      expect(contrast(c["accent-feature"], c["on-accent-feature"])).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c["accent-hover"], c["on-accent"])).toBeGreaterThanOrEqual(4.5);
      expect(contrast(c.accent, c["on-accent"])).toBeGreaterThanOrEqual(4.5);
      for (const background of [...surfaces, c["accent-soft"], c.selection]) {
        expect(contrast(c["accent-text"], background)).toBeGreaterThanOrEqual(
          4.5,
        );
      }
      for (const background of surfaces) {
        expect(contrast(c.accent, background)).toBeGreaterThanOrEqual(3);
      }
      expect(c["accent-feature"]).toBe(palette.swatch);
      expect(c["accent-glow"]).toBe(palette.glow);
      expect(c["accent-mist"]).toBe(palette.mist);
      expect(c["on-accent-feature"]).toBe("#101113");
      const stops = [c["accent-glow"], c["accent-feature"], c["accent-mist"]];
      expect(new Set(stops).size).toBe(3);
      for (let interval = 0; interval < 2; interval++)
        for (let step = 0; step <= 100; step++)
          expect(contrast(interpolate(stops[interval], stops[interval + 1], step / 100), c["on-accent-feature"])).toBeGreaterThanOrEqual(4.5);
    });
  }
}

it("only overrides accent roles, leaving neutral surfaces and semantic statuses unchanged", () => {
  const allowed = new Set([
    "feature-strength", "feature-glow-strength", "feature-mist-strength",
    "feature-glow", "feature-color", "feature-mist", "feature-gradient",
    "feature-ink", "feature-border", "feature-sheen",
    "primary",
    "primary-hover",
    "focus",
    "status-running",
    "crop-stroke",
    "accent",
    "accent-feature",
    "accent-glow",
    "accent-mist",
    "accent-gradient",
    "on-accent-feature",
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
  expect(css).toContain("--accent-gradient: linear-gradient(145deg, var(--accent-glow) 0%, var(--accent-feature) 48%, var(--accent-mist) 100%)");
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
