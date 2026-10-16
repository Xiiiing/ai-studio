/* Test-only scenarios. They never call a model or use a production database. */
const stages = [
  ["research", "选题研究", "researcher"],
  ["proposals", "故事提案", "creator"],
  ["story_decision", "故事决策", "producer"],
  ["screenplay", "剧本与分镜", "screenwriter"],
  ["visual_development", "视觉预览", "visual_designer"],
  ["style_decision", "视觉决策", "producer"],
  ["production", "镜头与后期", "editor"],
  ["final_decision", "终片审阅", "producer"],
  ["delivery", "正式交付", "producer"],
  ["revision", "定向返修", "story_editor"],
];
export function mockGraph() {
  return {
    nodes: stages.map(([id, label, owner]) => ({
      id,
      label,
      owner,
      members: [owner],
      status: "pending",
      input: "需求与上游审核记录",
      output: "版本绑定的交付物",
    })),
    edges: stages
      .slice(0, 8)
      .map((s, i) => ({ source: s[0], target: stages[i + 1][0] })),
    groups: [
      {
        id: "direction",
        label: "统筹与决策",
        roles: ["executive_producer", "producer"],
      },
      {
        id: "story",
        label: "故事开发",
        roles: ["researcher", "screenwriter", "storyboard_director"],
      },
      {
        id: "media",
        label: "视觉与声音",
        roles: [
          "visual_designer",
          "image_generator",
          "video_generator",
          "speech",
          "editor",
        ],
      },
      {
        id: "review",
        label: "独立质量审核",
        roles: ["independent_critic", "visual_critic"],
      },
    ],
  };
}
import { teamFlowFixture } from "./team-fixture.mjs";

export function operatorFor(project) {
  const gate = project.decisions.find((d) => d.status === "pending");
  const issues = project.needs_decision?.issues || [];
  const automatic =
    project.mockRunning &&
    project.team_mode === "delegated" &&
    ["story", "style"].includes(gate?.type);
  let attention = null;
  if (project.status === "blocked")
    attention = {
      kind:
        project.needs_decision?.kind === "script_review"
          ? "creative"
          : "runtime",
      title: "审核未通过，需要明确修改意见",
      summary: project.needs_decision?.message,
      blocking: true,
      issues,
      action_label: "查看问题并修改",
      target_tab: "decisions",
      suggested_feedback: "请在 S03 补齐救人的原因，并保留已确认的人物设定。",
      feedback_choices: issues.length
        ? [
            {
              id: "all_review_issues",
              label: "按全部审核建议修改",
              kind: "all_issues",
              feedback: "请在 S03 补齐救人的原因，并保留已确认的人物设定。",
            },
            {
              id: "issue_motivation",
              label: "S03 · 补齐人物动机",
              kind: "issue",
              feedback:
                "S03 补充人物此前的承诺，让救助行为有明确动机，修改后重新独立审核。",
            },
          ]
        : [],
    };
  else if (gate && !automatic && project.status !== "approved")
    attention = {
      kind: gate.type,
      title: {
        story: "选择故事方向",
        style: "选择人物与画风",
        final: "审看并确认终片",
      }[gate.type],
      summary: gate.title || "模拟关卡",
      blocking: true,
      gate,
      issues: [],
      action_label: "处理待办",
      target_tab: "decisions",
      feedback_choices:
        gate.type === "final"
          ? [
              {
                id: "final_pacing",
                label: "调整剪辑节奏",
                kind: "direction",
                feedback: "缩短重复空镜，保留完整对白，调整镜头衔接。",
              },
            ]
          : [],
    };
  if (attention)
    attention.actions =
      project.status === "blocked"
        ? [
            {
              id: "provide_feedback",
              label: "提交修改并重新审核",
              enabled: true,
              impact: "保留现有素材，重新检查当前剧本。",
              inputs: [
                {
                  name: "feedback",
                  label: "你的处理意见",
                  type: "textarea",
                  required: true,
                  maxlength: 3900,
                },
              ],
            },
          ]
        : [];
  if (attention && project.mockAdviceSupported) {
    attention.review_advice = project.review_advice;
    attention.actions.push({
      id: "request_review_advice",
      label: "请 AI 分析并推荐",
      enabled: !project.review_advice,
      inputs: [],
    });
  }
  const actions = project.mockRunning
    ? [
        {
          id: "take_control",
          label: "人工介入",
          enabled: true,
          inputs: [],
          impact: "在安全边界暂停，等待人工接管",
        },
      ]
    : [
        {
          id: "delegate_control",
          label: "交给 AI 接续",
          enabled: project.status !== "awaiting_final",
          disabled_reason: "终片需人工审看",
          inputs: [],
        },
      ];
  return {
    attention,
    actions,
    delegation: {
      mode: project.team_mode === "delegated" ? "delegated" : "human",
    },
    job_running: !!project.mockRunning,
    current_activity: automatic
      ? "总制片正在比较合格故事"
      : project.status === "blocked"
        ? "独立审查剧本"
        : "等待出品人审片",
  };
}
export function workspaceFor(project) {
  const operator = operatorFor(project),
    history = teamFlowFixture(project),
    graph = mockGraph();
  graph.schema = "ai-studio.production-graph.v1";
  graph.project_id = project.id;
  graph.revision = project.revision;
  graph.active_node =
    project.status === "blocked"
      ? "screenplay"
      : project.status === "approved"
        ? "delivery"
        : project.stage;
  graph.current_activity = operator.current_activity;
  graph.nodes = graph.nodes.map((n) =>
    n.id === graph.active_node
      ? {
          ...n,
          status:
            project.status === "blocked"
              ? "blocked"
              : project.status === "approved"
                ? "succeeded"
                : operator.attention
                  ? "human"
                  : "running",
          evidence: "current_project_state",
        }
      : n,
  );
  return {
    schema: "ai-studio.workspace.v1",
    project: {
      ...project,
      supervision: history.supervision.records.reduce(
        (a, r) => ({ ...a, [r.kind]: r }),
        {},
      ),
    },
    operator,
    workflow: graph,
    flow: history,
    revision: project.revision,
  };
}
export function overviewFor(project) {
  const operator = operatorFor(project);
  const summary = {
    ...project,
    attention: operator.attention,
    pending_decisions: Number(!!operator.attention?.blocking),
    job_running: operator.job_running,
    current_activity: operator.current_activity,
    shot_total: project.shots.length,
    shot_images: 1,
    shot_videos: 1,
  };
  return {
    schema: "ai-studio.console.v1",
    projects: [summary],
    attention_projects: operator.attention ? [summary] : [],
    metrics: {
      projects: 1,
      running: Number(operator.job_running),
      attention: summary.pending_decisions,
      delivered: Number(project.status === "approved"),
      gpu_minutes: 4.2,
    },
    activity: [
      {
        project_id: project.id,
        title: project.title,
        kind: "decision_required",
        at: "2026-10-15T11:00:00Z",
        message: "独立审核完成，等待出品人处理当前关卡。",
      },
    ],
    capabilities: {
      gpus: Array.from({ length: 8 }, (_, i) => ({
        index: i,
        free_mb: i < 4 ? 3200 : 17000,
        total_mb: 24576,
      })),
    },
    queue: { running: Number(operator.job_running), queued: 0 },
    version: "browser-fixture",
  };
}
export function scenario(project, kind) {
  project.revision += 1;
  project.mockRunning = false;
  project.team_mode = "guided";
  project.needs_decision = null;
  project.review_advice = null;
  project.review_automation = null;
  project.mockAdviceSupported = false;
  if (kind === "generated_anime") {
    project.format = "animation";
    project.creative_contract = {
      schema_version: 1,
      visual_style: "anime_2d",
      story_mode: "standalone",
      pacing: "short_drama",
      motion: "generated_video",
    };
    project.delivery.motion_provenance = {
      requested: "generated_video",
      generated_video_shots: 1,
      still_camera_shots: 0,
      unknown_shots: 0,
      lip_sync: false,
    };
    project.shots = project.shots.map((shot) => ({
      ...shot,
      video: {
        motion_method: "generated_video",
        generation_kind: "local_diffusion_video",
      },
    }));
  }
  if (kind === "story" || kind === "automatic") {
    project.status = "awaiting_story";
    project.stage = "story_decision";
    project.proposal_generation = 1;
    project.proposals = [
      {
        id: "A",
        title: "钟表谜题",
        logline: "成年人合作揭开旧时钟的秘密。",
        synopsis: "修表师与邻居通过一封旧信解开误会。",
        hook: "停止的钟突然鸣响。",
        ending: "两人修复时钟，也修复信任。",
        eligible: true,
      },
      { id: "B", title: "未通过的方案", eligible: false },
      {
        id: "C",
        title: "雨夜来信",
        logline: "一封错投的信引出邻里旧事。",
        eligible: true,
      },
    ];
    project.decisions = [
      {
        id: project.id + ":story:1",
        type: "story",
        status: "pending",
        options: [{ id: "A" }, { id: "C" }],
      },
    ];
    if (kind === "automatic") {
      project.mockRunning = true;
      project.team_mode = "delegated";
    }
  } else if (kind === "style") {
    project.status = "awaiting_style";
    project.stage = "style_decision";
    project.script_generation = 1;
    project.styles = [
      {
        id: "ink",
        name: "暖色手绘",
        description: "柔和手绘、稳定人物轮廓。",
        preview: {
          path:
            "production/" + project.slug + "/style_previews/ink/approved.png",
        },
      },
      {
        id: "film",
        name: "自然写实",
        description: "写实光线与电影构图。",
        preview: {
          path:
            "production/" + project.slug + "/style_previews/film/approved.png",
        },
      },
    ];
    project.decisions = [
      {
        id: project.id + ":style:1",
        type: "style",
        status: "pending",
        options: [{ id: "ink" }, { id: "film" }],
      },
    ];
  } else if (["creative", "review_assist", "repairing"].includes(kind)) {
    project.status = "blocked";
    project.stage = "script_review";
    project.decisions = [];
    project.selected_story = "A";
    project.selected_style = null;
    project.needs_decision = {
      type: "review",
      kind: "script_review",
      message: "分镜 S03 缺少关键人物动机。",
      issues: [
        {
          severity: "blocking",
          code: "motivation_gap",
          description: "S03 中人物突然选择救助对方，但没有说明原因。",
          suggestion:
            "补充人物此前承诺或目睹危险的镜头，使救助行为有明确动机。",
          evidence: "S02 到 S03 缺少人物判断的过渡。",
          shot_ids: ["S03"],
        },
      ],
    };
    if (kind === "review_assist") {
      project.mockAdviceSupported = true;
      project.review_automation = {
        phase: "script",
        status: "blocked",
        round: 4,
        max_rounds: 4,
        automatic_repairs: 3,
      };
    }
    if (kind === "repairing") {
      project.status = "scripting";
      project.stage = "screenplay";
      project.needs_decision = null;
      project.review_automation = {
        phase: "script",
        status: "writing",
        round: 3,
        max_rounds: 4,
        automatic_repairs: 2,
      };
    }
  } else if (kind === "final") {
    project.status = "awaiting_final";
    project.stage = "final_decision";
    project.decisions = [
      {
        id: project.id + ":final:1",
        type: "final",
        status: "pending",
        options: [{ id: "approve" }, { id: "revise" }, { id: "reedit" }],
      },
    ];
  }
}

export function mockReviewAdvice() {
  return {
    status: "ready",
    current: true,
    requested_model: "mock-producer",
    proposal: {
      recommendation: "repair",
      rationale: "缺少救助动机，建议保留故事方向补齐过渡。",
      feedback: "在 S03 前增加人物发现危险的动作，保留其他镜头，再交独立审核。",
      risks: ["补充过渡时保持原镜头时长"],
      decisions: [],
    },
  };
}
