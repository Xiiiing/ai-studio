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
    steps: names.map(([id]) => ({
      id,
      inputs:
        id === "screenplay"
          ? [
              {
                key: "resolved_story_brief",
                label: "故事目标全文",
                artifact_url: `/media/production/factory-${project.id}/planning/story.md`,
                source_label: "当前项目材料",
                source_node: "story_resolution",
              },
            ]
          : [{ label: "创作需求", value: project.brief }],
      outputs:
        id === "screenplay"
          ? [
              {
                key: "script",
                label: "剧本正文",
                value: "本轮草稿已保存",
                artifact_url: `/media/production/factory-${project.id}/planning/script.md`,
                source_label: "当前项目材料",
              },
              {
                key: "missing",
                label: "缺失的旧稿",
                artifact_url: `/media/production/factory-${project.id}/planning/missing.md`,
              },
            ]
          : [{ label: "当前记录", value: "离线测试业务内容" }],
    })),
    nodes: [
      {
        id: "screenwriter",
        role: "screenwriter",
        label: "编剧",
        inputs: [
          {
            label: "故事目标全文",
            artifact_url: `/media/production/factory-${project.id}/planning/story.md`,
          },
        ],
        outputs: [
          {
            label: "剧本正文",
            artifact_url: `/media/production/factory-${project.id}/planning/script.md`,
          },
        ],
        executions: [
          {
            id: "writer-current",
            stage: "screenplay",
            run_id: "fixture-run",
            scope: "current_run",
            model: "mock-writer-current",
            context_manifest: {
              schema: "ai-studio.context-manifest.v1", delivery: "response_received",
              project_version: { script_generation: 2 }, prompt: { version: "1.0.0" },
              budget: { fits: true, prompt_tokens: 300, context_tokens: 32768 },
              materials: [{ field: "producer_feedback", label: "出品人反馈", state: "selected",
                summary: "合成验收要求：结尾保留悬念", reason: "包含在本次输入",
                sha256: "a".repeat(64), bytes: 39, version: { version: 1 } }],
            },
            status: "succeeded",
            started_at: "2026-10-16T04:00:00Z",
            artifacts: [
              {
                label: "解析结果",
                artifact_url: `/media/production/factory-${project.id}/planning/calls/current/parsed.json`,
              },
            ],
            attempts: [1, 2].map((attempt) => ({
              attempt,
              artifacts: ["request", "response"].map((kind) => ({
                label: kind === "request" ? "请求原文" : "响应原文",
                artifact_url: `/media/production/factory-${project.id}/planning/calls/current/${kind}-0${attempt}.json`,
              })),
            })),
          },
          {
            id: "writer-failed",
            stage: "screenplay",
            scope: "project_history",
            model: "mock-writer-failed",
            status: "failed",
            started_at: "2026-10-16T03:00:00Z",
            artifacts: [
              {
                label: "执行回执",
                artifact_url: `/media/production/factory-${project.id}/planning/calls/failed/receipt.json`,
              },
            ],
            attempts: [
              {
                attempt: 1,
                artifacts: [
                  {
                    label: "请求原文",
                    artifact_url: `/media/production/factory-${project.id}/planning/calls/current/request-01.json`,
                  },
                ],
              },
            ],
          },
          {
            id: "writer-legacy",
            scope: "project_history",
            model: "mock-writer-legacy",
            status: "succeeded",
            started_at: "2026-10-15T04:00:00Z",
          },
        ],
      },
    ],
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

export function fixtureDocument(pathname, projectId) {
  const prefix = `/media/production/factory-${projectId}/planning/`;
  if (!pathname.startsWith(prefix)) return null;
  const file = pathname.slice(prefix.length);
  if (file === "story.md")
    return {
      mime: "text/markdown; charset=utf-8",
      body: "# 故事目标全文\n\n成年修表师履行承诺，与邻居解开旧时钟的误会。\n\n- 人物动机：履行承诺\n- 结局：共同修复时钟\n",
    };
  if (file === "script.md")
    return {
      mime: "text/markdown; charset=utf-8",
      body:
        '# 灯下的约定\n\n## 第一场：修表铺\n\n**人物动机**：为了完成对邻居的承诺。\n\n| 镜头 | 动作 | 台词 |\n| --- | --- | --- |\n| S01 | 修表师打开门 | 我答应过你。 |\n| S02 | 邻居拿出时钟 | 我们一起修好它。 |\n\n> 本文件仅用于离线浏览器测试。\n\n```json\n{"scene": "S01", "prompt": "保留完整创作材料"}\n```\n\n' +
        "长文本阅读验证。".repeat(80),
    };
  if (file === "calls/current/parsed.json")
    return {
      mime: "application/json",
      body: JSON.stringify({
        title: "灯下的约定",
        prompt: "保留提示词字段",
        shots: [{ id: "S01", action: "履行承诺" }],
      }),
    };
  if (/calls\/current\/request-0[12]\.json/.test(file))
    return {
      mime: "application/json",
      body: JSON.stringify({
        model: "mock-writer-current",
        messages: [
          { role: "system", content: "你是编剧，遵守人物动机与故事目标。" },
          { role: "user", content: "这是当次保存的完整输入。" },
        ],
        prompt: "请求原文中的提示词",
      }),
    };
  if (/calls\/current\/response-0[12]\.json/.test(file))
    return {
      mime: "application/json",
      body: JSON.stringify({
        model: "mock-writer-current",
        choices: [
          {
            message: {
              content: file.includes("01")
                ? "第一次输出结构不合格"
                : "第二次输出已完成",
            },
          },
        ],
      }),
    };
  if (file === "calls/failed/receipt.json")
    return {
      mime: "application/json",
      body: JSON.stringify({
        status: "failed",
        error: "模拟模型超时，未保存响应",
      }),
    };
  if (file === "large.md")
    return { mime: "text/markdown", body: "a".repeat(2 * 1024 * 1024 + 1) };
  return null;
}
