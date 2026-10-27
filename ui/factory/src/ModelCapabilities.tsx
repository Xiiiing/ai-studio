import type { RecordData } from "./api";

export function ModelCapabilities({ value }: { value?: RecordData }) {
  if (!value)
    return (
      <p className="muted">
        当前连接尚无能力记录；保存并刷新后检查，不能据此假定模型支持。
      </p>
    );
  const dimensions = value.dimensions;
  const duration = value.duration;
  const refs = Array.isArray(value.reference_counts)
    ? value.reference_counts
    : [];
  return (
    <section className="model-capabilities" aria-label="模型能力">
      <strong>当前适配器能力</strong>
      <p className="muted">
        配置和提交使用同一份能力定义；不代表在线状态或真实生成效果已验证。
      </p>
      <dl className="metadata-grid">
        <div>
          <dt>参考图数量</dt>
          <dd>{refs.length ? refs.join("、") : "尚未明确"}</dd>
        </div>
        {value.kind !== "audio" && (
          <div>
            <dt>首帧 / 尾帧</dt>
            <dd>
              {value.first_frame ? "支持首帧" : "无独立首帧接口"} /{" "}
              {value.last_frame ? "支持尾帧" : "不支持尾帧"}
            </dd>
          </div>
        )}
        {duration && (
          <div>
            <dt>镜头时长</dt>
            <dd>
              {duration.min}–{duration.max} 秒
            </dd>
          </div>
        )}
        {dimensions && (
          <div>
            <dt>画布尺寸</dt>
            <dd>
              {dimensions.min}–{dimensions.max} 像素，{dimensions.multiple}{" "}
              像素步长
              {dimensions.max_pixels
                ? `；最多 ${dimensions.max_pixels} 像素`
                : ""}
              {dimensions.min_pixels
                ? `；至少 ${dimensions.min_pixels} 像素`
                : ""}
            </dd>
          </div>
        )}
        {value.kind !== "audio" && (
          <div>
            <dt>原生口型 / 原生声音</dt>
            <dd>
              {value.lip_sync ? "支持" : "无原生口型同步"} /{" "}
              {value.native_audio ? "支持" : "无原生声音输出"}
            </dd>
          </div>
        )}
      </dl>
      {value.unknown?.length > 0 && (
        <p className="muted">
          部分能力尚未验证；超出已声明范围的功能不能据此启用。
        </p>
      )}
      <details>
        <summary>能力来源与版本</summary>
        <p style={{ overflowWrap: "anywhere" }}>
          {value.source} · {value.schema}
        </p>
      </details>
    </section>
  );
}
