export type StatusTone = "running" | "review" | "failed" | "success" | "idle";

const tones: Record<string, StatusTone> = {
  running: "running",
  producing: "running",
  creating: "running",
  researching: "running",
  scripting: "running",
  visual_development: "running",
  waiting_remote: "running",
  revising: "running",
  retrying: "running",
  rendering: "running",
  reviewing: "review",
  returned: "review",
  awaiting_plan: "review",
  repairing: "running",
  exhausted: "failed",
  unit_ready: "success",
  semantic_pending: "review",
  human: "review",
  human_required: "review",
  awaiting_story: "review",
  awaiting_style: "review",
  awaiting_final: "review",
  needs_attention: "review",
  advisory: "review",
  blocked: "failed",
  failed: "failed",
  fail: "failed",
  error: "failed",
  rejected: "failed",
  succeeded: "success",
  completed: "success",
  success: "success",
  approved: "success",
  passed: "success",
  pass: "success",
  reviewed: "success",
  generated: "success",
  selected: "success",
  resources_ready: "success",
  ready: "success",
  finished: "success",
  reused: "success",
};

/** Appearance follows the recorded status; an unknown or queued task stays idle. */
export function statusTone(status?: string): StatusTone {
  const key = status || "";
  return Object.hasOwn(tones, key) ? tones[key] : "idle";
}

/** Decorative only: the caller must keep its visible status text. */
export function StatusIndicator({ status }: { status?: string }) {
  return (
    <i
      className="status-indicator"
      data-status-indicator={statusTone(status)}
      aria-hidden="true"
    />
  );
}
