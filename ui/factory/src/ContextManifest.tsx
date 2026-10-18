import { rows, type RecordData } from "./api";

export interface InputMaterial {
  field: string;
  label: string;
  sha256: string;
  bytes: number;
  state: "selected" | "empty";
  reason: string;
  summary: string;
  version?: Record<string, string | number>;
}

export interface CallContextManifest {
  schema: "ai-studio.context-manifest.v1";
  materials: InputMaterial[];
  project_version?: Record<string, string | number>;
  prompt?: { version?: string; status?: string };
  source_checks?: RecordData[];
  budget?: {
    fits: boolean;
    prompt_tokens?: number;
    context_tokens?: number;
    method?: string;
  };
  delivery?: "not_submitted" | "attempted" | "response_received";
  truncated?: boolean;
}

export function ContextManifest({
  value,
  reused = false,
  originCall,
}: {
  value?: CallContextManifest | null;
  reused?: boolean;
  originCall?: string;
}) {
  if (!value || value.schema !== "ai-studio.context-manifest.v1")
    return (
      <p className="muted">
        {reused
          ? "复用已完成结果；原始调用来源未知，未发起新的模型调用。"
          : "此记录未保存输入清单；历史材料来源未知。"}
      </p>
    );
  const versions = Object.entries(value.project_version || {});
  const labels: Record<string, string> = {
    revision: "项目修订",
    workflow_version: "执行图",
    script_generation: "剧本代数",
    proposal_generation: "故事代数",
    style_generation: "画风代数",
    production_revision: "制作修订",
    plan_version: "规划版本",
    plan_cycle: "规划周期",
    plan_attempt: "规划轮次",
    creative_round: "创作轮次",
  };
  return (
    <details className="context-manifest">
      <summary>
        {reused ? "本次复用材料" : "本次收到的材料"} ·{" "}
        {rows(value.materials).filter((m) => m.state === "selected").length} 项
      </summary>
      <p className="muted">
        {reused
          ? `未发起新的模型调用；以下为原始调用保存的输入。来源：${originCall || "未知"}。`
          : value.delivery === "not_submitted"
            ? "调用准备时选入的材料；生成请求尚未提交。"
            : value.delivery === "attempted"
              ? "已尝试提交生成请求；未取得响应，不能确认模型已读取。"
              : "以下材料包含在当次请求中；不代表模型已正确理解或落实。"}
      </p>
      {versions.length > 0 && (
        <p className="muted">
          当次项目快照：
          {versions.map(([k, v]) => `${labels[k] || k} ${v}`).join(" · ")}
          （各材料版本以其自身记录为准）
        </p>
      )}
      <p className="muted">
        提示词版本：{value.prompt?.version || "未注册 / 未记录"}
      </p>
      {value.budget && (
        <p className="muted">
          窗口检查：{value.budget.fits ? "通过" : "超限，已阻止本次提交"} · 输入{" "}
          {value.budget.prompt_tokens ?? "未知"} / 窗口{" "}
          {value.budget.context_tokens ?? "未知"} token
          {value.budget.method === "conservative_utf8_estimate"
            ? "（保守估算）"
            : ""}
        </p>
      )}
      {rows(value.materials).map((m) => (
        <article className="context-material" key={m.field}>
          <strong>{m.label}</strong>
          <span className="muted">
            {" "}
            · {m.state === "selected" ? "已选入" : "未提供"}
          </span>
          <p>{m.summary}</p>
          <details>
            <summary>来源与版本</summary>
            <p className="muted">
              输入字段：{m.field} · {m.bytes} 字节
            </p>
            <p className="muted">{m.reason}</p>
            <p className="muted">
              材料版本：
              {Object.entries(m.version || {})
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ") || "未记录"}
            </p>
            <p className="context-hash">SHA-256：{m.sha256}</p>
          </details>
        </article>
      ))}
      {rows(value.source_checks).length > 0 && (
        <details>
          <summary>项目材料核对</summary>
          {rows(value.source_checks).map((m) => (
            <p key={m.field}>
              <strong>{m.label}</strong>：{m.reason}
            </p>
          ))}
        </details>
      )}
      {value.truncated && (
        <p className="muted">
          展示清单达到条数上限；完整输入以请求原文为准，未裁剪实际请求。
        </p>
      )}
    </details>
  );
}
