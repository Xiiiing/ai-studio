import { SelectionGroup } from "./SelectionGroup";
import { Monitor, Moon, Sun } from "lucide-react";
import { useAppearance, type ThemePreference } from "./Theme";
import { ACCENT_PALETTES } from "./palettes";
import { Badge, Panel } from "./ui";
import "./appearance-settings.css";

const modes = [
  { id: "system", label: "跟随系统", Icon: Monitor },
  { id: "light", label: "浅色", Icon: Sun },
  { id: "dark", label: "深色", Icon: Moon },
] as const;

export function AppearanceSettings() {
  const { preference, choose, accent, chooseAccent } = useAppearance();
  const selected = ACCENT_PALETTES.find((palette) => palette.id === accent)!;
  return (
    <div className="appearance-settings">
      <Panel title="外观">
        <fieldset className="appearance-fieldset">
          <legend>显示模式</legend>
          <SelectionGroup className="appearance-modes" value={preference}>
            {modes.map(({ id, label, Icon }) => (
              <label
                className="appearance-mode"
                data-selected={preference === id}
                key={id}
              >
                <input
                  type="radio"
                  name="appearance-mode"
                  value={id}
                  checked={preference === id}
                  onChange={() => choose(id as ThemePreference)}
                />
                <Icon size={20} aria-hidden="true" />
                <span>{label}</span>
              </label>
            ))}
          </SelectionGroup>
        </fieldset>
        <fieldset className="appearance-fieldset">
          <legend>主题色</legend>
          <SelectionGroup className="appearance-palettes" value={accent}>
            {ACCENT_PALETTES.map((palette) => (
              <label
                className="appearance-palette"
                data-selected={accent === palette.id}
                key={palette.id}
              >
                <input
                  type="radio"
                  name="appearance-accent"
                  value={palette.id}
                  checked={accent === palette.id}
                  onChange={() => chooseAccent(palette.id)}
                />
                <span
                  className="appearance-swatch"
                  style={{
                    backgroundColor: palette.swatch,
                    backgroundImage: `linear-gradient(145deg, ${palette.glow} 0%, ${palette.swatch} 48%, ${palette.mist} 100%)`,
                  }}
                  aria-hidden="true"
                />
                <span>{palette.label}</span>
              </label>
            ))}
          </SelectionGroup>
        </fieldset>
        <p className="appearance-storage">选择后即时生效，保存在当前浏览器。</p>
        <section className="appearance-preview" aria-label="主题效果预览">
          <div className="appearance-preview-head">
            <h3>效果预览</h3>
            <span>{selected.label}</span>
          </div>
          <div className="appearance-preview-feature">
            <span>重点信息</span>
            <strong>当前制作</strong>
          </div>
          <div className="appearance-preview-items">
            <span className="appearance-preview-action">主操作</span>
            <span className="appearance-preview-selection">选中状态</span>
            <Badge status="running" label="执行中" />
            <Badge status="blocked" label="需处理" />
            <Badge status="succeeded" label="已通过" />
          </div>
        </section>
      </Panel>
    </div>
  );
}
