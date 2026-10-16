/* Test-only loopback server. No proxy, model call, database or production write. */
import http from "node:http";
import { createHash } from "node:crypto";
import { readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { teamFlowFixture } from "./team-fixture.mjs";
import {
  overviewFor,
  workspaceFor,
  scenario,
  mockGraph,
} from "./console-fixture.mjs";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const catalog = JSON.parse(await readFile(path.join(root,"runtime/browser-fixtures/model-catalog.json"),"utf8"));
const templateStore = new Map();
const port = Number(process.env.TEST_PORT || 18787);
const id = "012345abcdef";
const shot = {
  id: "S01",
  seconds: 6,
  dramatic_beat: "镜头制作与播放验证",
  prompt: "An adult holds a book",
  motion_prompt: "A slow camera push",
  camera_motion: "hold",
  characters: ["char_lead"],
  status: "passed",
  shot_revision: 1,
  candidates: [
    {
      id: "video:take_001",
      take: "take_001",
      stage: "video",
      attempt: 1,
      status: "passed",
      model: "mock-video-only",
      parameters: { duration: 6 },
      qa: { technical: { status: "pass" }, score: 82 },
      selectable: true,
      plan_current: true,
      media_url:
        "/media/production/factory-" +
        id +
        "/release/v1-aaaaaaaaaaaa/episode.mp4",
      cost: {
        currency: "CNY",
        estimated_cost_micros: 1000000,
        confirmed_cost_micros: null,
      },
    },
  ],
};
const initial = {
  id,
  slug: "factory-" + id,
  title: "浏览器模拟样片",
  brief: "仅用于离线工作台冒烟测试，不代表模型生成质量。",
  format: "motion_comic",
  duration_seconds: 6,
  status: "awaiting_final",
  stage: "final_decision",
  revision: 7,
  production_revision: 1,
  progress: 95,
  budget: {
    gpu_minutes: 120,
    spent_gpu_minutes: 0,
    wall_minutes: 180,
    max_attempts: 3,
  },
  production: { elapsed_seconds: 3 },
  selected_story: "A",
  selected_style: "style-A",
  script_review: { passed: true },
  script: {
    title: "浏览器测试",
    cast: { char_lead: "Fictional adult" },
    shots: [shot],
  },
  shots: [
    {
      ...shot,
      image_url: "/media/production/factory-" + id + "/frames/frame.png",
      video_url:
        "/media/production/factory-" +
        id +
        "/release/v1-aaaaaaaaaaaa/episode.mp4",
    },
  ],
  delivery: {
    video_url:
      "/media/production/factory-" +
      id +
      "/release/v1-aaaaaaaaaaaa/episode.mp4",
    files: [],
  },
  decisions: [
    {
      id: id + ":final:1",
      type: "final",
      status: "pending",
      options: [{ id: "approve" }, { id: "revise" }],
    },
  ],
  events: [],
};
const profiles = {
  writer: {
    provider: "llama_cpp",
    network_mode: "loopback",
    enabled: true,
    base_url: "http://127.0.0.1:1/v1",
    model: "mock-writer",
    context_tokens: 32768,
    vision: true,
    response_format: "llama_json_schema",
    context_validation: "llama_tokenize",
    allowed_hosts: [],
  },
};
let project, board, inventory, fail, calls;
function reset() {
  project = structuredClone(initial);
  board = {
    project_id: id,
    revision: 7,
    production_revision: 1,
    editable: true,
    shots: [structuredClone(shot)],
    errors: [],
  };
  templateStore.clear();
  inventory = {
    ...catalog,
    readiness: {missing: [], generation_verified: false},
    revision: "config-1",
    editable_profiles: structuredClone(profiles),
    profiles: Object.entries(profiles).map(([id, value]) => ({ id, ...value })),
    role_assignments: { screenwriter: "writer", visual_critic: "writer" },
    roles: [
      { role: "screenwriter", label: "编剧", profile: "writer" },
      { role: "visual_critic", label: "画面质检", profile: "writer" },
      {
        role: "executive_producer",
        label: "总制片代理",
        profile: null,
        optional: true,
        configured: false,
        enabled: false,
      },
    ],
    video_profiles: {
      local_wan: {
        provider: "comfyui_wan",
        enabled: true,
        model: "Wan2.2-TI2V-5B",
      },
    },
    video_roles: { video_generator: "local_wan" },
  };
  fail = "";
  calls = [];
}
reset();
function json(response, value, status = 200) {
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify(value));
}
const server = http.createServer(async (request, response) => {
  try {
    if (
      !["127.0.0.1:" + port, "localhost:" + port].includes(request.headers.host)
    )
      return json(response, { error: "Test host rejected" }, 403);
    const url = new URL(request.url, "http://127.0.0.1:" + port);
    let body = "";
    for await (const part of request) {
      body += part;
      if (body.length > 65536) throw new Error("Fixture body too large");
    }
    const value = body ? JSON.parse(body) : {};
    if (url.pathname === "/__test/reset" && request.method === "POST") {
      reset();
      return json(response, { reset: true });
    }
    if (url.pathname === "/__test/scenario" && request.method === "POST") {
      scenario(project, value.kind);
      board.revision = project.revision;
      return json(response, { scenario: value.kind });
    }
    if (url.pathname === "/__test/progress" && request.method === "POST") {
      project.revision += 1;
      project.title = "制作进度已更新";
      project.progress = 98;
      board.revision = project.revision;
      board.shots[0].dramatic_beat = "其他区域已更新，视频继续播放";
      return json(response, { revision: project.revision });
    }
    if (url.pathname === "/__test/failure" && request.method === "POST") {
      fail = value.route || "";
      return json(response, { failure: fail });
    }
    if (url.pathname === "/__test/calls") return json(response, calls);
    if (url.pathname.startsWith("/api/")) {
      calls.push({ method: request.method, path: url.pathname, body: value });
      if (fail && url.pathname.endsWith(fail))
        return json(response, { error: "模拟服务暂时不可用" }, 503);
      if (
        ["/api/factory/overview", "/api/factory/console"].includes(url.pathname)
      )
        return json(response, overviewFor(project));
      if (url.pathname === "/api/factory/projects/" + id + "/workspace")
        return json(response, workspaceFor(project));
      if (url.pathname === "/api/factory/workflow")
        return json(response, {
          production_graph: mockGraph(),
          teams: [],
          nodes: [],
          edges: [],
          roles: [],
        });
      if (url.pathname === "/api/factory/production-profiles")
        return json(response, {
          presets: {
            legacy15: { label: "经典短剧 · 15 镜" },
            compact6: { label: "紧凑短片 · 6 镜 / 30 秒" },
          },
        });
      if (url.pathname === "/api/factory/workflow-templates" && request.method === "POST") {
        const ident=createHash("sha256").update(JSON.stringify(value)).digest("hex");
        templateStore.set(ident,value);return json(response,{id:ident,...value});
      }
      if (url.pathname.startsWith("/api/factory/workflow-templates/")) {
        const saved=templateStore.get(url.pathname.split("/").at(-1));
        return json(response,saved || {error:"Unknown fixture template"},saved?200:404);
      }
      if (url.pathname === "/api/factory/workflow-templates") return json(response,{templates:[]});
      if (url.pathname === "/api/factory/models/test")
        return json(response, {
          status: "unverified",
          message: "模拟预检，不调用模型",
          elapsed_ms: 1,
        });
      if (url.pathname === "/api/factory/models") {
        if (request.method === "POST") {
          if (value.expected_revision !== inventory.revision)
            return json(response, { error: "配置已更新" }, 409);
          inventory = {
            ...inventory,
            revision: "config-2",
            ...Object.fromEntries(["image_profiles","image_roles","audio_profiles","audio_roles","video_profiles","video_roles"].filter(k=>k in value).map(k=>[k,value[k]])),
            editable_profiles: value.profiles,
            role_assignments: value.roles,
            profiles: Object.entries(value.profiles).map(([id, v]) => ({
              id,
              ...v,
            })),
          };
          inventory.roles = inventory.roles.map((row) =>
            row.role !== "executive_producer"
              ? row
              : {
                  ...row,
                  profile: value.roles.executive_producer || null,
                  configured: !!value.roles.executive_producer,
                  enabled:
                    !!value.profiles[value.roles.executive_producer]?.enabled,
                },
          );
        }
        return json(response, inventory);
      }
      if (
        url.pathname === "/api/factory/projects" &&
        request.method === "POST"
      ) {
        if (
          !["guided", "delegated"].includes(value.team_mode) ||
          typeof value.brief !== "string"
        )
          return json(
            response,
            { error: "Invalid mock creation payload" },
            400,
          );
        if (
          value.team_mode === "delegated" &&
          !inventory.role_assignments.executive_producer
        )
          return json(
            response,
            { error: "Mock supervisor must be explicitly bound" },
            400,
          );
        project.team_mode = value.team_mode;
        project.brief = value.brief;
        project.revision += 1;
        return json(response, { project }, 201);
      }
      if (url.pathname === "/api/factory/projects/" + id)
        return json(response, project);
      if (url.pathname.endsWith("/shots")) return json(response, board);
      if (url.pathname.endsWith("/flow"))
        return json(response, teamFlowFixture(project));
      if (url.pathname.endsWith("/series"))
        return json(response, {
          series_key: "",
          assets: [],
          references: {},
          sources: [],
          bible: null,
        });
      if (url.pathname.endsWith("/cloud-budget"))
        return json(response, {
          project_id: id,
          tasks: [],
          authorizations: [],
          quote: null,
          totals: {},
        });
      if (url.pathname.endsWith("/action") && request.method === "POST") {
        if (value.expected_revision !== project.revision)
          return json(
            response,
            { error: "项目已更新，已保留你的意见；请刷新当前状态后再提交" },
            409,
          );
        if (value.action === "take_control") {
          project.mockRunning = false;
          project.team_mode = "guided";
          project.status = "paused";
          project.revision += 1;
        }
        if (["retry", "provide_feedback"].includes(value.action)) {
          project.needs_decision = null;
          project.status = "queued";
          project.decisions = [];
          project.revision += 1;
        }
        return json(response, { project }, 202);
      }
      if (url.pathname.endsWith("/decision") && request.method === "POST") {
        if (
          value.decision_id !== project.decisions[0].id ||
          project.decisions[0].status !== "pending"
        )
          return json(response, { error: "决策已处理" }, 409);
        if (
          value.expected_revision !== undefined &&
          value.expected_revision !== project.revision
        )
          return json(response, { error: "项目已更新，请刷新后提交" }, 409);
        const current = project.decisions[0];
        if (!current.options.some((o) => o.id === value.choice))
          return json(response, { error: "方案不属于当前决策" }, 409);
        current.status = "answered";
        if (current.type === "story") {
          project.selected_story = value.choice;
          scenario(project, "style");
        } else if (current.type === "style") {
          project.selected_style = value.choice;
          scenario(project, "final");
        } else {
          project.status = value.choice === "approve" ? "approved" : "queued";
          project.revision += 1;
        }
        return json(response, { project }, 202);
      }
      if (url.pathname.endsWith("/shots/S01") && request.method === "POST") {
        if (value.expected_revision !== project.revision)
          return json(response, { error: "项目已更新，请刷新" }, 409);
        project.revision += 1;
        board.revision = project.revision;
        if (value.action === "edit")
          Object.assign(board.shots[0], value.changes, { shot_revision: 2 });
        else board.shots[0].candidates[0].producer_selected = true;
        return json(response, { project }, 202);
      }
      return json(response, { error: "Unexpected fixture API" }, 404);
    }
    let file;
    if (url.pathname === "/")
      file = path.join(root, "ui/factory/dist/index.html");
    else if (url.pathname.startsWith("/factory/static/"))
      file = path.join(
        root,
        "ui/factory/dist",
        decodeURIComponent(url.pathname.slice("/factory/static/".length)),
      );
    else if (
      url.pathname ===
      "/media/production/factory-" + id + "/release/v1-aaaaaaaaaaaa/episode.mp4"
    )
      file = path.join(root, "runtime/browser-fixtures/clip.mp4");
    else if (
      url.pathname.startsWith(
        "/media/production/factory-" + id + "/style_previews/",
      )
    )
      file = path.join(root, "runtime/browser-fixtures/frame.png");
    else if (
      url.pathname ===
      "/media/production/factory-" + id + "/frames/frame.png"
    )
      file = path.join(root, "runtime/browser-fixtures/frame.png");
    else return json(response, { error: "Not found" }, 404);
    file = await realpath(file);
    if (
      ![
        path.join(root, "ui/factory/"),
        path.join(root, "runtime/browser-fixtures/"),
      ].some((base) => file.startsWith(base))
    )
      return json(response, { error: "Path rejected" }, 403);
    const mime = {
      ".html": "text/html",
      ".js": "text/javascript",
      ".css": "text/css",
      ".mp4": "video/mp4",
      ".png": "image/png",
    }[path.extname(file)];
    const content = await readFile(file),
      range = request.headers.range?.match(/^bytes=(\d+)-(\d*)$/);
    if (range) {
      const start = Number(range[1]),
        end = range[2]
          ? Math.min(Number(range[2]), content.length - 1)
          : content.length - 1;
      if (start > end) {
        response.writeHead(416);
        return response.end();
      }
      response.writeHead(206, {
        "Content-Type": mime,
        "Content-Length": end - start + 1,
        "Content-Range": `bytes ${start}-${end}/${content.length}`,
        "Accept-Ranges": "bytes",
      });
      return response.end(content.subarray(start, end + 1));
    }
    response.writeHead(200, {
      "Content-Type": mime,
      "Content-Length": content.length,
      "Cache-Control": "no-store",
      "Accept-Ranges": "bytes",
    });
    response.end(content);
  } catch (error) {
    json(response, { error: "Fixture request failed: " + error.message }, 500);
  }
});
server.listen(port, "127.0.0.1", () =>
  console.log("BROWSER_FIXTURE_READY " + port),
);
for (const signal of ["SIGTERM", "SIGINT"])
  process.on(signal, () => server.close(() => process.exit(0)));
