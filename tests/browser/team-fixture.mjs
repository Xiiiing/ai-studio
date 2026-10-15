/* Deliberately simulated team records. Never used by application code. */
export function teamFlowFixture(project) {
  const names = [
    ["research", "选题研究", "researcher", "story_editor"],
    ["proposals", "故事提案", "creator", "independent_critic"],
    ["screenplay", "剧本与分镜", "screenwriter", "independent_critic"],
    ["visual_development", "视觉预览", "image_generator", "producer"],
    ["production", "制作与后期", "editor", "visual_critic"],
    ["revision", "定向返修", "story_editor", "independent_critic"],
  ];
  return {
    project_id: project.id,
    revision: project.revision,
    teams: [],
    nodes: [],
    edges: [],
    evidence: { scope: "mock" },
    assignment_semantics: "浏览器模拟记录，不调用真实模型。",
    task_board: {
      schema: "ai-studio.team-tasks.v1",
      tasks: names.map(([id, label, owner, reviewer], i) => ({
        id,
        label,
        owner,
        reviewer,
        inputs: ["需求与上游审核记录"],
        outputs: ["版本绑定的交付物"],
        depends_on: i ? [names[i - 1][0]] : [],
        current: i < 5,
        status: i < 5 ? "succeeded" : "pending",
        record:
          i < 5 ? { attempt: 1, started_at: "2026-10-15T06:00:00Z" } : null,
      })),
    },
    supervision: {
      mode: "delegated",
      records: [
        {
          kind: "story",
          status: "selected",
          current_gate_version: true,
          requested_model: "mock-producer",
          proposal: {
            choice: "A",
            rationale: "保留人物动机和明确结局；本条仅为模拟数据。",
            risks: ["需要核验原创素材授权"],
          },
        },
        {
          kind: "final",
          status: "advisory",
          current_gate_version: true,
          requested_model: "mock-producer",
          proposal: {
            choice: "",
            rationale: "请出品人观看最终视频，重点核对人物一致性、对白与字幕。",
            risks: ["文字记录不等于看过成片"],
          },
        },
      ],
    },
    delivery_readiness: {
      schema: "ai-studio.delivery-readiness.v1",
      project_id: project.id,
      project_revision: project.revision,
      checks: [
        {
          id: "current_release",
          label: "交付物属于当前版本",
          status: "recorded",
        },
        {
          id: "producer_final",
          label: "出品人人工终审",
          status: "human_required",
        },
        {
          id: "fresh_acceptance",
          label: "重新验收文件和音画",
          status: "not_run",
        },
      ],
      artifacts: [
        {
          name: "episode.mp4",
          label: "带字幕成片",
          url:
            "/media/production/factory-" +
            project.id +
            "/release/v1-aaaaaaaaaaaa/episode.mp4",
        },
      ],
      platform_checklist: [
        {
          id: "account",
          label: "核对红果官方账号及提交规格",
          status: "human_required",
        },
        {
          id: "rights",
          label: "核验音乐、字体、人物与声音授权",
          status: "human_required",
        },
        {
          id: "labels",
          label: "核验 AI 标识和上传声明",
          status: "human_required",
        },
      ],
    },
  };
}
