import { kindLabels, type ModelKind } from "./ModelConfig";
export function ServiceCategories({
  kind,
  onChange,
}: {
  kind: ModelKind;
  onChange: (kind: ModelKind) => void;
}) {
  return (
    <div className="service-categories" role="group" aria-label="服务用途">
      {(["llm", "image", "video", "audio"] as ModelKind[]).map((value) => (
        <button
          type="button"
          key={value}
          aria-pressed={kind === value}
          onClick={() => onChange(value)}
        >
          {kindLabels[value]}
        </button>
      ))}
    </div>
  );
}
