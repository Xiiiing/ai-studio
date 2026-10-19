import { useMutation, useQueryClient } from "@tanstack/react-query";
// Persisted project and provider payloads have extensible server-owned fields.
export type RecordData = Record<string, any>;
export interface ActionInput {
  name: string;
  label: string;
  type: "text" | "textarea" | "number" | "select" | "multiselect" | "checkbox";
  required?: boolean;
  value?: any;
  maxlength?: number;
  min?: number;
  max?: number;
  options?: { id: string; label: string }[];
  description?: string;
}
export interface ActionDescriptor {
  id: string;
  label: string;
  inputs: ActionInput[];
  impact?: string;
  enabled: boolean;
  disabled_reason?: string;
}
export interface WorkflowNode extends RecordData {
  id: string;
  label: string;
  owner: string;
  members: string[];
  status: string;
  input_keys?: string[];
  output_keys?: string[];
  description?: string;
  conditional?: boolean;
  run_id?: string;
}
export interface WorkflowStage {
  id: string;
  label: string;
  node_ids: string[];
}
export interface WorkflowPresentation {
  stages: WorkflowStage[];
  branches: (WorkflowStage & { return_to: string })[];
  workflow_version: number;
}
export interface WorkflowMaterial extends RecordData {
  label: string;
  artifact_url?: string;
  value?: unknown;
  source_node?: string;
  source_label?: string;
  state?: "available" | "not_recorded" | "not_generated" | "too_large";
  artifact_state?: "missing";
}
export interface Workspace extends RecordData {
  project: RecordData;
  revision: number;
  operator: RecordData & { actions?: ActionDescriptor[] };
  workflow: RecordData;
  flow?: RecordData;
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    public code = "",
  ) {
    super(message);
  }
}
export async function request<T = RecordData>(
  path: string,
  body?: unknown,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch("/api/factory" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new ApiError("服务返回了无法识别的数据。", response.status);
    }
    if (!response.ok)
      throw new ApiError(
        typeof data.error === "string"
          ? data.error
          : data.error?.message || "请求未完成",
        response.status,
        data.code,
      );
    return data;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError")
      throw new ApiError("请求超时，请同步实际状态后再操作。");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
export const keys = {
  overview: ["overview"],
  models: ["models"],
  workflow: ["workflow"],
  profiles: ["profiles"],
  workspace: (id: string) => ["workspace", id],
  flow: (id: string) => ["flow", id],
  shots: (id: string) => ["shots", id],
  series: (id: string) => ["series", id],
  costs: (id: string) => ["costs", id],
};
export function useOperation(
  path: string,
  onSuccess?: (result: RecordData) => void,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RecordData) => request(path, body),
    onSuccess: async (result) => {
      onSuccess?.(result);
      await client.invalidateQueries();
    },
  });
}
export const rows = (value: any): RecordData[] =>
  Array.isArray(value) ? value : [];
export const text = (value: any): string =>
  value == null
    ? ""
    : typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : String(value);
export function mediaUrl(value: any) {
  if (typeof value !== "string" || !value) return "";
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}
export function ownedMedia(id: string, value: any) {
  if (!/^[a-f0-9]{12}$/.test(id)) return "";
  const raw = typeof value === "string" ? value : value?.path;
  if (
    typeof raw !== "string" ||
    /[\\%?#:]/.test(raw) ||
    raw.includes("\0") ||
    raw.includes("//") ||
    raw.split("/").some((x) => x === "." || x === "..")
  )
    return "";
  const prefix = "production/factory-" + id + "/";
  const relative = raw.startsWith("/media/") ? raw.slice(7) : raw;
  return relative.startsWith(prefix) ? mediaUrl("/media/" + relative) : "";
}

export function pendingGate(p: RecordData) {
  if (["approved", "cancelled"].includes(p.status)) return null;
  if (p.operator && Object.hasOwn(p.operator, "attention"))
    return p.operator.attention?.blocking
      ? p.operator.attention.gate ||
          p.needs_decision || { type: p.operator.attention.kind }
      : null;
  return (
    rows(p.decisions).find((g) => g.status === "pending") ||
    p.needs_decision ||
    null
  );
}
export function number(value: any) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}
export function money(value: any, currency = "CNY") {
  return value == null
    ? "未确认"
    : (number(value) / 1e6).toLocaleString("zh-CN", {
        maximumFractionDigits: 6,
      }) +
        " " +
        currency;
}
export function parseMoney(value: any) {
  if (!/^\d{1,9}(\.\d{1,6})?$/.test(String(value)))
    throw new Error("金额请输入非负数，最多六位小数。");
  const [whole, fraction = ""] = String(value).split(".");
  const result = Number(whole) * 1e6 + Number(fraction.padEnd(6, "0"));
  if (!Number.isSafeInteger(result)) throw new Error("金额超出范围。");
  return result;
}
export const names: Record<string, string> = {
  plan_creation: "全剧规划创作",
  plan_review: "全剧规划审核",
  plan_repair: "全剧规划返修",
  continuity_repair: "连续性自动返修",
  returned: "已退回返修",
  repairing: "自动返修中",
  exhausted: "返修额度用尽",
  plan_confirmation: "待确认全剧规划",
  episodes: "剧集制作",
  dependency: "等待前集结果",
  scene_planning: "场景编排",
  scene_review: "场景审核",
  scene_repair: "场景返修",
  scene_units: "制作场景镜头",
  shared_references: "制作共享参考素材",
  assembly: "合成单集",
  final_decision: "待验收成片",
  script_creation: "编写剧本与分镜",
  script_review: "剧本审核",
  storyboard_direction: "分镜编排",
  story_creation: "故事创作",
  story_review: "故事审核",
  story_resolution: "落实创作修改意见",
  story_resolution_review: "审核故事修改结果",
  storyboard: "分镜编排",
  story_decision: "待确认故事方向",
  visual_design: "画风设计",
  style_decision: "待确认视觉方向",
  remote_submit: "提交生成任务",
  remote_poll: "等待生成结果",
  remote_download: "下载生成素材",
  delivery_ready: "待验收成片",
  delivered: "成片已交付",
  unit_ready: "单元已完成",
  awaiting_plan: "待确认全剧规划",
  waiting_dependency: "等待前集结果",
  draft: "未生成",
  skipped: "无需执行",
  finished: "已交接",
  pending: "待执行",
  running: "执行中",
  succeeded: "已完成",
  human: "等待你处理",
  blocked: "需要处理",
  failed: "执行失败",
  paused: "已暂停",
  queued: "排队中",
  waiting_remote: "云端生成中",
  creating: "创意开发",
  researching: "选题研究",
  scripting: "剧本创作",
  visual_development: "视觉开发",
  producing: "镜头制作",
  revising: "定向返修",
  awaiting_story: "等待选故事",
  awaiting_style: "等待定视觉",
  awaiting_final: "等待审成片",
  approved: "已通过",
  cancelled: "已取消",
  passed: "审核通过",
  pass: "通过",
  reviewed: "已技术验证",
  generated: "已生成",
  planned: "已计划",
  reviewing: "审核中",
  retrying: "返修中",
  rendering: "生成中",
  semantic_pending: "等待画面审核",
  rejected: "未通过",
  fail: "未通过",
  not_run: "未检查",
  human_required: "人工核验",
  needs_attention: "需要处理",
  production_repair: "定向返修",
  advisory: "建议",
  selected: "已采纳",
  waiting_resources: "等待资源",
  resources_ready: "资源已就绪",
  ready: "可用",
  configured: "已配置",
  unverified: "未验证",
  error: "异常",
};
export const activityName = (value?: string, fallback = "等待调度") =>
  names[value || ""] || value || fallback;
export const roles: Record<string, string> = {
  researcher: "选题研究",
  creator: "故事策划",
  story_editor: "故事统筹",
  story_critic: "提案审核",
  screenwriter: "编剧",
  independent_critic: "独立审稿",
  storyboard_director: "分镜导演",
  visual_designer: "视觉设计",
  prompt_style_compiler: "提示词编译",
  visual_critic: "视觉质检",
  executive_producer: "总制片代理",
  producer: "出品人",
  image_generator: "图像制作",
  video_generator: "视频制作",
  speech: "配音",
  dialogue_editor: "对白编辑",
  editor: "剪辑",
  edit_director: "剪辑导演",
  orchestrator: "工作流总控",
};
export const roleName = (role: string) => roles[role] || role;
export const date = (v: any) =>
  v
    ? new Date(typeof v === "number" && v < 1e12 ? v * 1000 : v).toLocaleString(
        "zh-CN",
        {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        },
      )
    : "未记录";
