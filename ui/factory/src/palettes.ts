export const ACCENT_STORAGE_KEY = "studio.accent";

// The swatch represents the feature surface; small marks use a readable shade
// of the same hue. Primary actions and reading surfaces remain neutral.
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
    swatch: "#b9bac1",
  },
  { id: "teal", label: "青瓷", description: "清透的青色焦点", swatch: "#5ddbc7" },
  { id: "jade", label: "松绿", description: "鲜明的绿色焦点", swatch: "#77ed94" },
  { id: "iris", label: "暮紫", description: "清晰的紫色焦点", swatch: "#b8a0ed" },
  {
    id: "bronze",
    label: "暖铜",
    description: "温暖的铜色焦点",
    swatch: "#e3b786",
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
