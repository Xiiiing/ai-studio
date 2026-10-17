import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Monitor, Moon, Sun } from "lucide-react";

export type ThemePreference = "system" | "light" | "dark";
export const THEME_STORAGE_KEY = "studio.theme";
const cycle: ThemePreference[] = ["system", "light", "dark"];
const labels = { system: "跟随系统", light: "浅色", dark: "深色" };

export function readTheme(): ThemePreference {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    if (value === "light" || value === "dark") return value;
  } catch {
    /* Theme remains usable when browser storage is unavailable. */
  }
  return "system";
}

export function applyTheme(preference: ThemePreference, systemDark: boolean) {
  const theme =
    preference === "system" ? (systemDark ? "dark" : "light") : preference;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.style.colorScheme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? "#171719" : "#f7f7f8");
}

function useThemeState() {
  const [preference, setPreference] = useState<ThemePreference>(readTheme);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => applyTheme(preference, media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [preference]);
  useEffect(() => {
    const update = (event: StorageEvent) => {
      if (event.key === THEME_STORAGE_KEY || event.key === null)
        setPreference(readTheme());
    };
    window.addEventListener("storage", update);
    return () => window.removeEventListener("storage", update);
  }, []);
  return {
    preference,
    choose: (next: ThemePreference) => {
      try {
        localStorage.setItem(THEME_STORAGE_KEY, next);
      } catch {
        /* Keep session choice. */
      }
      setPreference(next);
    },
  };
}
const ThemeContext = createContext<ReturnType<typeof useThemeState> | null>(
  null,
);
export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useThemeState();
  return (
    <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>
  );
}
function StandaloneThemeControl() {
  const theme = useThemeState();
  return <ThemeButton theme={theme} />;
}
export function ThemeControl() {
  const theme = useContext(ThemeContext);
  return theme ? <ThemeButton theme={theme} /> : <StandaloneThemeControl />;
}
function ThemeButton({
  theme: { preference, choose },
}: {
  theme: ReturnType<typeof useThemeState>;
}) {
  const Icon =
    preference === "system" ? Monitor : preference === "dark" ? Moon : Sun;
  const next = cycle[(cycle.indexOf(preference) + 1) % cycle.length];
  const description = `外观主题：${labels[preference]}；切换为${labels[next]}`;
  return (
    <button
      type="button"
      className="theme-control icon-button"
      aria-label={description}
      title={description}
      data-tooltip={description}
      onClick={() => choose(next)}
    >
      <Icon size={20} aria-hidden="true" />
    </button>
  );
}
