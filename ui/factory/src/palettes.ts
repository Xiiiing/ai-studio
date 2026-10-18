export const ACCENT_STORAGE_KEY = "studio.accent";

// Custom muted scales, composed using Radix Colors' background / solid / text
// roles. These are application colors, not the unmodified Radix color scales.
export const ACCENT_PALETTES = [
  {
    id: "slate",
    label: "深灰蓝",
    description: "沉静的蓝灰色",
    swatch: "#46566e",
  },
  {
    id: "graphite",
    label: "石墨",
    description: "克制的中性灰",
    swatch: "#53545b",
  },
  { id: "teal", label: "青瓷", description: "柔和的灰青色", swatch: "#3e686b" },
  { id: "jade", label: "松绿", description: "沉稳的灰绿色", swatch: "#4b6656" },
  { id: "iris", label: "暮紫", description: "柔和的灰紫色", swatch: "#675b7b" },
  {
    id: "bronze",
    label: "暖铜",
    description: "温润的棕铜色",
    swatch: "#785c47",
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
