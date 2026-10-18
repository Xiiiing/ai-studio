export const ACCENT_STORAGE_KEY = "studio.accent";

// A theme is a light-to-color-to-mist tonal range, not a flat fill. Its
// small marks use a deeper readable shade; reading and actions stay neutral.
// Keep these three hex stops synchronized with styles/accents.css.
export const ACCENT_PALETTES = [
  {
    id: "slate",
    label: "深灰蓝",
    description: "冷白、蓝灰与薄雾",
    glow: "#e1e9f2",
    swatch: "#9fb3c8",
    mist: "#f1f5f9",
  },
  {
    id: "graphite",
    label: "雾银",
    description: "银白、冷灰与柔白",
    glow: "#eef2f3",
    swatch: "#bac3c8",
    mist: "#f7f8f8",
  },
  {
    id: "teal",
    label: "薄荷青",
    description: "薄荷、青绿与浅露",
    glow: "#c8faea",
    swatch: "#6be7cb",
    mist: "#edfcf6",
  },
  {
    id: "jade",
    label: "青柠绿",
    description: "嫩叶、青柠与淡白",
    glow: "#e8fbd3",
    swatch: "#a8ef75",
    mist: "#f5fce9",
  },
  {
    id: "iris",
    label: "雾紫",
    description: "浅丁香、雾紫与柔白",
    glow: "#eee7ff",
    swatch: "#c0aff0",
    mist: "#f9f5ff",
  },
  {
    id: "bronze",
    label: "杏桃",
    description: "浅杏、蜜桃与暖白",
    glow: "#ffeadc",
    swatch: "#f1bca0",
    mist: "#fff7ec",
  },
] as const;

export type AccentPreference = (typeof ACCENT_PALETTES)[number]["id"];

export function isAccent(value: unknown): value is AccentPreference {
  return ACCENT_PALETTES.some((palette) => palette.id === value);
}

export function readAccent(): AccentPreference {
  try {
    const saved = localStorage.getItem(ACCENT_STORAGE_KEY);
    if (isAccent(saved)) return saved;
  } catch {
    // Appearance remains usable when storage is blocked.
  }
  return "slate";
}
