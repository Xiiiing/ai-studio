// Synthetic API contracts for testing the shared diagram, never model execution.
export function lifecycleFixture(workflow, projectId, episode = false) {
  const definitions = episode
    ? [
        ["scenes", "单集场景创作与独立审核", "screenwriter"],
        ["units", "分镜与制作单元", "orchestrator"],
        ["assembly", "连续性检查与单集合成", "editor"],
        ["acceptance", "出品人验收", "producer"],
      ]
    : [
        ["planning", "全剧设定与分集大纲", "story_editor"],
        ["review", "全剧规划独立审核", "story_critic"],
        ["confirmation", "出品人确认全剧规划", "producer"],
        ["production", "按集制作与团队协作", "orchestrator"],
        ["acceptance", "逐集验收与成果", "producer"],
      ];
  const tasks = workflow.tasks || [];
  for (const t of tasks)
    t.stage_id = episode
      ? "units"
      : /^(episode|batch):/.test(t.id)
        ? "production"
        : /critic/.test(t.role)
          ? "review"
          : "planning";
  const nodes = definitions.map(([id, label, role]) => ({
    id,
    label,
    owner: role,
    members: [
      role,
      ...tasks.filter((t) => t.stage_id === id).map((t) => t.role),
    ],
    status: id === (episode ? "units" : "review") ? "blocked" : "pending",
    input: "已保存的工作依据",
    output: "经审核的交付内容",
  }));
  const edges = nodes
    .slice(1)
    .map((n, i) => ({ source: nodes[i].id, target: n.id }));
  edges.push({
    source: episode ? "assembly" : "review",
    target: episode ? "units" : "planning",
    loop: true,
    label: "审核退回",
  });
  return {
    ...workflow,
    graph: {
      project_id: projectId,
      revision: 8,
      active_node: episode ? "units" : "review",
      departments: [
        {
          id: "direction",
          label: "制片统筹",
          lead: "producer",
          roles: ["producer"],
          responsibility: "确认规划并验收成果。",
        },
        {
          id: "story",
          label: "故事编剧",
          lead: "story_editor",
          roles: ["story_editor", "screenwriter"],
          responsibility: "整理故事设定并创作分集大纲。",
        },
        {
          id: "post",
          label: "后期制作",
          lead: "editor",
          roles: ["editor"],
          responsibility: "剪辑与合成交付。",
        },
        {
          id: "review",
          label: "独立质检",
          lead: "independent_critic",
          roles: ["story_critic", "independent_critic", "visual_critic"],
          responsibility: "独立审核与有依据的退回。",
        },
        {
          id: "operations",
          label: "流程编排",
          lead: "orchestrator",
          roles: ["orchestrator"],
          responsibility: "协调剧集和制作单元。",
        },
      ],
      nodes,
      edges,
      presentation: {
        stages: nodes.map((n) => ({
          id: n.id,
          label: n.label,
          node_ids: [n.id],
        })),
      },
    },
    flow: {
      project_id: projectId,
      revision: 8,
      nodes: [],
      edges: episode
        ? [
            {
              source: "screenwriter",
              target: "independent_critic",
              label: "场景方案",
            },
          ]
        : [
            {
              source: "story_editor",
              target: "screenwriter",
              label: "全剧设定与角色约束",
            },
          ],
      steps: nodes.map((n) => ({ id: n.id, inputs: [], outputs: [] })),
    },
  };
}
