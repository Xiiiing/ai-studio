import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { lifecycleFixture } from "./lifecycle-fixture.mjs";
import { paletteViolations, gradientSurfaces } from "./palette.mjs";
import { openWorkflow } from "./navigation.mjs";
import { manageService } from "./model-flow.mjs";

// Served production UI, synthetic API records and generated fixture media only.
// Read-only navigation/preferences must never submit a production operation.
const unitId = "012345abcdef", dramaId = "dd1122334455", episodeId = "ee1122334455";
const video = `/media/production/factory-${unitId}/release/v1-aaaaaaaaaaaa/episode.mp4`;
const frame = `production/factory-${unitId}/frames/frame.png`;
const palettes = [["slate", "深灰蓝"], ["graphite", "雾银"], ["teal", "薄荷青"], ["jade", "青柠绿"], ["iris", "雾紫"], ["bronze", "杏桃"]];
const title = "旧信与车站：在漫长归途中重新发现彼此的约定";
const asset = { asset_id: "mat_palette_lead", version: 2, library_key: "global", kind: "character", status: "approved", data: { document: { name: "林夏参考", description: "成年女性，短发，灰色外套，稳定身份。", identity_constraints: "成年女性，短发。" }, images: [{ path: frame, label: "正面参考", sha256: "synthetic-palette-frame" }] } };

async function install(page, request) {
  const base = await (await request.get("/api/factory/console")).json();
  const delivery = { video_url: video, clean_url: video, subtitle_url: `/media/production/factory-${unitId}/release/v1-aaaaaaaaaaaa/subtitles.srt`, manifest_url: `/media/production/factory-${unitId}/release/v1-aaaaaaaaaaaa/manifest.json` };
  const document = { world: "旧城车站与修表铺。", storyline: "成年旧友通过一封旧信寻找多年以前的约定，最终在车站和解。", visual_rules: "统一水彩画风与人物身份。", relationships: ["林夏与陈默是成年旧友。"], characters: [{ character_id: "lead", name: "林夏", identity_constraints: "成年女性，短发，灰色外套。", default_wardrobe: "灰色外套", voice_id: "female" }], scenes: [{ entity_id: "station", name: "旧城车站", description: "暖色候车大厅与旧时钟。" }], props: [{ entity_id: "letter", name: "旧信", description: "多年以前寄出的信件。" }], uncertainties: [], episodes: [1, 2, 3].map(number => ({ number, title: `旧信${number}`, synopsis: `第${number}集沿着旧信寻找成年旧友之间的约定。` })) };
  const episodes = [
    { id: episodeId, episode_number: 1, title: "第1集 · 车站相遇", status: "awaiting_final", progress: 100, duration_seconds: 300, plan_version: 1, outline: { synopsis: "林夏在车站发现多年以前的旧信。" }, delivery },
    { id: "ee1122334456", episode_number: 2, title: "第2集 · 归途中的选择", status: "blocked", progress: 40, duration_seconds: 300, plan_version: 1, outline: { synopsis: "陈默解释过去的误会。" }, error: "跨场景连续性需返修，已保留成功素材。" },
    { id: "ee1122334457", episode_number: 3, title: "第3集 · 重新出发", status: "draft", progress: 0, duration_seconds: 300, plan_version: 1, outline: { synopsis: "旧友在新的一天重逢。" } },
  ];
  const drama = { id: dramaId, record_kind: "drama", title, revision: 8, episode_count: 3, duration_seconds: 300, format: "motion_comic", status: "ready", stage: "production", plan_version: 1, plan_review: { passed: true, issues: [] }, plans: [{ version: 1, document }], draft_plan: document, episodes, shared_assets: { "1": { lead: { kind: "character", entity_id: "lead", receipt: { path: frame } } } }, batches: [{ id: "bb1122334455", episode_numbers: [1, 2], plan_version: 1, status: "running", progress: 70, spent_gpu_minutes: 46, authorized_gpu_minutes: 240, members: episodes.slice(0, 2).map(e => ({ number: e.episode_number, status: e.status })) }], workflow: lifecycleFixture({ tasks: [{ id: "outline-1", title: "分集大纲", role: "screenwriter", status: "completed", input: "旧信与车站的连续剧情。", output: "已保存三集大纲，独立审核通过。" }, { id: "critic-1", title: "全剧规划审核", role: "independent_critic", status: "completed", input: "三集大纲与角色状态。", output: "全剧规划通过独立审核。" }] }, dramaId) };
  const episode = { ...episodes[0], record_kind: "episode", drama_id: dramaId, revision: 12, drama_revision: 8, budget: { spent_gpu_minutes: 22, max_attempts: 3 }, total_authorized_gpu_minutes: 120, units: [{ id: unitId, title: "E001-C01-U001 · 车站相遇", status: "unit_ready", progress: 100, duration_seconds: 75, global_prefix: "E001-C01-U001", outline: { synopsis: "林夏在候车大厅找到旧信。" } }], workflow: lifecycleFixture({ tasks: [{ id: "scene-1", title: "车站场景与制作单元", role: "screenwriter", status: "completed", input: "首集大纲与共享人物设定。", output: "车站场景拆为一段制作单元。", project_id: unitId }] }, episodeId, true) };
  await page.route("**/api/factory/console**", r => r.fulfill({ json: { ...base, projects: [drama, base.projects[0]], pagination: { offset: 0, limit: 100, total: 2, has_more: false, next_offset: null }, activity: [...base.activity, { project_id: dramaId, title, kind: "error", at: "2026-10-18T01:00:00Z", message: "第2集连续性审核退回作者，其他剧集继续。" }, { project_id: dramaId, title, kind: "completed", at: "2026-10-18T02:00:00Z", message: "第1集成片与字幕已生成。" }] } }));
  await page.route("**/api/factory/library**", r => r.fulfill({ json: new URL(r.request().url()).pathname.endsWith("/tasks") ? { tasks: [{ id: "material-check", name: "林夏参考图", model: "mock-image", status: "completed", operation: "generate", result: { asset_id: asset.asset_id, version: 2 } }] } : { assets: [asset], has_more: false, total: 1 } }));
  await page.route(`**/api/factory/projects/${unitId}/materials`, r => r.fulfill({ json: { revision: 7, selected: [{ binding: { asset_id: asset.asset_id, version: 2, kind: "character", role: "female" }, asset }], shots: [], package: { references: [{ asset_id: asset.asset_id, version: 2 }] } } }));
  await page.route(`**/api/factory/dramas/${dramaId}`, r => r.fulfill({ json: drama }));
  await page.route(`**/api/factory/dramas/${dramaId}/episodes/${episodeId}`, r => r.fulfill({ json: episode }));
  await page.route(`**/api/factory/projects/${dramaId}/workspace`, r => r.fulfill({ json: { project: drama, revision: drama.revision, operator: {}, workflow: {}, flow: {} } }));
  await page.route(`**/api/factory/projects/${episodeId}/workspace`, r => r.fulfill({ json: { project: episode, revision: episode.revision, operator: {}, workflow: {}, flow: {} } }));
  await page.route(`**/api/factory/projects/${unitId}/workspace`, async r => {
    const response = await r.fetch(), body = await response.json();
    body.project.title = "E001-C01-U001 · 车站相遇";
    body.project.record_kind = "unit";
    body.project.script.logline = "林夏在候车大厅找到旧信。";
    body.project.script.shots = body.project.script.shots.map(s => ({ ...s, dialogue: [{ character: "林夏", start: 0, end: 3, text: "这封信，终于回到了你手里。" }] }));
    body.project.shots = body.project.shots.map(s => ({ ...s, qa: { technical: { status: "pass" }, semantic: { status: "pass", summary: "角色身份与跨镜连续性通过审核。" } }, dialogue: [{ character: "林夏", start: 0, end: 3, text: "这封信，终于回到了你手里。" }] }));
    body.project.delivery.artifacts = [{ url: delivery.subtitle_url, name: "字幕文件 SRT" }, { url: video, name: "无字幕成片" }];
    body.project.delivery.manifest_url = delivery.manifest_url;
    await r.fulfill({ response, json: body });
  });
  const teamTask = { id: "video:S01", label: "车站相遇镜头视频", kind: "video", scope: "S01", shot_id: "S01", owner: "video_generator", department: "visual", status: "completed", generation: 1, attempt: 1, input_version: "分镜 v1", dependencies: [], inputs: [{ label: "制作要求", value: "保持林夏的人物身份与灰色外套。" }], artifacts: [{ label: "镜头视频候选", kind: "video", url: video }], review_status: "passed", adoption_status: "selected", locked: true, model: "mock-video-only", attempts: [{ attempt: 1, status: "completed", elapsed_seconds: 12 }] };
  await page.route(`**/api/factory/projects/${unitId}/team`, r => r.fulfill({ json: { schema: 1, enabled: true, control_revision: 1, departments: [{ id: "visual", label: "美术与影像", lead: "visual_designer", responsibility: "交付角色一致的镜头画面。" }], members: [{ id: "visual_designer", role: "visual_designer", label: "视觉设计", department: "visual", leader: true, status: "idle", current_tasks: [] }, { id: "video_generator", role: "video_generator", label: "视频制作", department: "visual", status: "completed", current_tasks: [teamTask.id] }], task_summary: { total: 1, running: 0, waiting: 0, blocked: 0, completed: 1 }, events: [{ id: "frame-check", at: "2026-10-18T01:00:00Z", message: "首帧审核通过，视频候选已采用。", task_id: teamTask.id }], instructions: [], locks: [], actions: [] } }));
  await page.route(`**/api/factory/projects/${unitId}/tasks**`, r => r.fulfill({ json: new URL(r.request().url()).pathname.endsWith("/tasks") ? { tasks: [teamTask], total: 1, offset: 0, limit: 30 } : { task: teamTask } }));
  await page.route(`**/api/factory/projects/${unitId}/cloud-budget`, r => r.fulfill({ json: { project_id: unitId, quote: null, authorizations: [], totals: { CNY: { confirmed_cost_micros: 1000000, estimated_cost_micros: 1000000, unconfirmed_tasks: 0 } }, tasks: [{ intent_id: "fixture-video-intent", shot_id: "S01", attempt: 1, model: "mock-video-only", task_id: "fixture-receipt-1", status: "completed", currency: "CNY", estimated_cost_micros: 1000000, confirmed_cost_micros: 1000000 }] } }));
  return { drama, episode };
}

async function inspect(page, { feature = false } = {}) {
  await expect(page.locator("#workspace h1")).toHaveCount(1);
  expect(await paletteViolations(page)).toEqual([]);
  const colors = await page.evaluate(() => {
    const rgb = value => { const n = value.match(/[\d.]+/g)?.map(Number) || []; return n.slice(0, 3).map(v => Math.round(value.startsWith("color(srgb ") ? v * 255 : v)); };
    const neutral = value => { const c = rgb(value); return c.length < 3 || Math.max(...c) - Math.min(...c) <= 14; };
    const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== "hidden";
    const probe = document.createElement("span"); document.body.append(probe);
    const tokens = Object.fromEntries(["ink", "muted", "panel", "accent", "primary", "on-primary"].map(t => { probe.style.color = `var(--${t})`; return [t, getComputedStyle(probe).color]; })); probe.remove();
    const lum = value => rgb(value).map(c => { const v = c / 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((a, v, i) => a + v * [.2126, .7152, .0722][i], 0);
    const contrast = (a, b) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
    const gradients = [...document.querySelectorAll("body *")].filter(visible).flatMap(e => [null, "::before", "::after"].flatMap(p => { const s = getComputedStyle(e, p); return (p && ["none", "normal"].includes(s.content)) || !/gradient\(/.test(s.backgroundImage) ? [] : [`${e.tagName}.${e.className?.baseVal ?? e.className}${p || ""}`]; }));
    const tintedReading = [...document.querySelectorAll("#workspace .panel,#workspace .drama-card,#workspace .model-service-card,#workspace .candidate-card,#workspace .shot-story-card,#workspace input:not([type=checkbox]):not([type=radio]),#workspace textarea,dialog[open],dialog[open] .panel,dialog[open] input:not([type=checkbox]):not([type=radio]),dialog[open] textarea")].filter(visible).filter(e => !e.closest(".page-color-focus,.dashboard-focus-feature,.appearance-swatch,[data-flow-state]"))
      .flatMap(e => { const s = getComputedStyle(e); return neutral(s.backgroundColor) ? [] : [`${e.tagName}.${e.className}:${s.backgroundColor}`]; });
    const primary = [...document.querySelectorAll("#workspace .button.primary,dialog[open] .button.primary")].filter(visible).filter(e => !e.disabled && !e.matches('[aria-pressed="true"]') && !e.closest(".page-color-focus,.project-navigation,.drama-tabs"))
      .map(e => { const s = getComputedStyle(e); return { text: e.textContent.trim(), neutral: neutral(s.backgroundColor), contrast: contrast(s.color, s.backgroundColor) }; });
    const features = [...document.querySelectorAll(".page-summary-value,.project-context > article")].filter(visible).map(e => { const s = getComputedStyle(e); return { background: rgb(s.backgroundColor), color: rgb(s.color), expectedBackground: rgb(tokens.panel), expectedColor: rgb(tokens.ink), mutedColor: rgb(tokens.muted), contrast: contrast(s.color, tokens.panel), image: s.backgroundImage, highlight: s.boxShadow, expectedHighlight: tokens.accent, height: e.getBoundingClientRect().height, viewport: innerWidth, text: [...e.querySelectorAll("*")].filter(n => !n.closest(".badge,[aria-hidden=true]") && [...n.childNodes].some(t => t.nodeType === Node.TEXT_NODE && t.textContent.trim())).map(n => ({ label: n.textContent.trim(), color: rgb(getComputedStyle(n).color), contrast: contrast(getComputedStyle(n).color, tokens.panel) })) }; });
    const statuses = [...document.querySelectorAll("#workspace [data-status-indicator]")].filter(visible).map(e => { const tone = e.dataset.statusIndicator; probe.style.color = `var(--status-${tone})`; document.body.append(probe); const expected = getComputedStyle(probe).color; probe.remove(); return { tone, color: rgb(getComputedStyle(e).backgroundColor), expected: rgb(expected), width: parseFloat(getComputedStyle(e).width) }; });
    const badges = [...document.querySelectorAll("#workspace .badge")].filter(visible).map(e => ({ text: e.textContent.trim(), height: e.getBoundingClientRect().height }));
    return { gradients, tintedReading, primary, features, badges, statuses, overflow: document.documentElement.scrollWidth > innerWidth + 1 };
  });
  const gradients = await gradientSurfaces(page);
  expect(gradients.unexpected).toEqual([]);
  for (const surface of gradients.surfaces) {
    expect(surface.image, surface.element).toMatch(/^linear-gradient\(145deg,/);
    expect(surface.image, surface.element).toMatch(/0%,.*48%,.*100%/);
    expect(surface.stops, surface.element).toHaveLength(3);
    expect(surface.distinctStops, surface.element).toBe(3);
    if (surface.expectedStops) expect(surface.stops, surface.element).toEqual(surface.expectedStops);
    if (!surface.swatch) {
      expect(surface.surfaceContrast, surface.element).toBeLessThanOrEqual(3);
      expect(surface.contrast, surface.element).toBeGreaterThanOrEqual(4.5);
      for (const text of surface.text) expect(text.contrast, text.label).toBeGreaterThanOrEqual(4.5);
    }
  }
  expect(colors.tintedReading).toEqual([]);
  expect(colors.overflow).toBe(false);
  if (feature) expect(colors.features.length).toBeGreaterThan(0);
  for (const f of colors.features) {
    expect(Math.max(...f.background) - Math.min(...f.background)).toBeLessThanOrEqual(14);
    expect(f.image).toBe("none"); expect(f.contrast).toBeGreaterThanOrEqual(4.5);
    expect(f.height).toBeLessThan(f.viewport <= 700 ? 360 : 240);
    for (const text of f.text) { expect([f.expectedColor, f.mutedColor], text.label).toContainEqual(text.color); expect(text.contrast, text.label).toBeGreaterThanOrEqual(4.5); }
  }
  for (const button of colors.primary) { expect(button.neutral, button.text).toBe(true); expect(button.contrast, button.text).toBeGreaterThanOrEqual(4.5); }
  for (const dot of colors.statuses) { expect(dot.color, dot.tone).toEqual(dot.expected); expect(dot.width, dot.tone).toBe(6); }
  for (const b of colors.badges) expect(b.height, b.text).toBeLessThan(48);
}

async function capture(page, group, label, width, theme) {
  const selected = theme === "light" || ["models", "workflow", "shots"].includes(label);
  if (!selected) return;
  // Modal dismissal restores keyboard focus and may leave the page scrolled.
  // Return to an ordinary reading position without changing product focus styles.
  await page.locator("#workspace h1").click();
  await page.evaluate(() => {
    document.querySelector(".main-shell").scrollTop = 0;
    window.scrollTo(0, 0);
  });
  await page.mouse.move(0, 0);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
  await mkdir("docs/images/all-pages-color", { recursive: true });
  await page.screenshot({ path: `docs/images/all-pages-color/${group}-${label}-${theme}-${width}.png`, fullPage: true });
}

async function verifyReadOnly(page, writes, errors) {
  expect(writes).toEqual([]); expect(errors).toEqual([]);
}

test.beforeEach(async ({ page, request }) => {
  await request.post("/__test/reset");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/*", r => ["127.0.0.1", "localhost"].includes(new URL(r.request().url()).hostname) ? r.continue() : r.abort());
});

for (const width of [1440, 390]) for (const theme of ["light", "dark"]) {
  const configure = async page => { await page.setViewportSize({ width, height: 1000 }); await page.emulateMedia({ colorScheme: theme }); await page.addInitScript(a => { if (!sessionStorage.getItem("palette-fixture-seeded")) { localStorage.setItem("studio.accent", a); sessionStorage.setItem("palette-fixture-seeded", "1"); } }, width === 390 ? "iris" : "jade"); };
  test(`current work and its action stay together above supporting counts at ${width} ${theme}`, async ({ page, request }) => {
    await configure(page); const { drama } = await install(page, request);
    const writes = []; page.on("request", r => { if (r.url().includes("/api/") && !["GET", "HEAD"].includes(r.method())) writes.push(r.url()); });
    await page.goto(`/#project=${dramaId}&tab=overview`);
    const work = page.getByRole("region", { name: "当前工作区", exact: true });
    const summary = page.getByRole("region", { name: "已出片剧集摘要", exact: true });
    const current = drama.workflow.graph.nodes.find(n => n.id === drama.workflow.graph.active_node);
    await expect(work.getByRole("heading")).toHaveText(current.label);
    await expect(work.getByRole("button", { name: "打开制作流程", exact: true })).toBeVisible();
    await expect(work).not.toContainText("已出片剧集");
    await expect(summary).toContainText("1 / 3 集");
    const progress = summary.getByRole("progressbar");
    await expect(progress).toHaveAttribute("aria-valuenow", "1");
    await expect(progress).toHaveAttribute("aria-valuemax", "3");
    const a = await work.boundingBox(), b = await summary.boundingBox();
    expect(b.y).toBeGreaterThanOrEqual(a.y + a.height);
    await expect(summary).toHaveCSS("background-image", "none");
    await work.getByRole("button", { name: "打开制作流程", exact: true }).click();
    await expect(page.locator("[data-stage]")).toHaveCount(5);
    await expect(page).toHaveURL(new RegExp(`project=${dramaId}&tab=workflow`));
    await page.goBack(); await expect(work).toBeVisible();
    await work.getByRole("button", { name: "查看全剧规划", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`project=${dramaId}&tab=plan`));
    expect(writes).toEqual([]);
  });

  test(`all global pages keep the same color roles at ${width} ${theme}`, async ({ page, request }) => {
    test.setTimeout(60000);
    await configure(page); await install(page, request);
    const errors = [], writes = []; page.on("pageerror", e => errors.push(e.message)); page.on("request", r => { if (r.url().includes("/api/") && !["GET", "HEAD"].includes(r.method())) writes.push(r.url()); });
    for (const [view, content] of [["projects", title], ["library", "林夏参考 · v2"], ["activity", "第2集连续性审核退回作者，其他剧集继续。"], ["models", "mock-writer"]]) {
      await page.goto(`/#view=${view}`);
      await expect(page.locator("#workspace")).toContainText(content);
      if (view === "models") {
        await expect(page.locator(".model-service-card").first()).toBeVisible();
        for (const label of ["制作分工", "高级工具", "服务连接"]) {
          await page.getByRole("tab", { name: label, exact: true }).click();
          await expect(page.getByRole("tab", { name: label, exact: true })).toHaveAttribute("aria-selected", "true");
          await expect(page.getByRole("tabpanel")).toBeVisible();
          await inspect(page, { feature: true });
        }
        await manageService(page);
        await expect(page.locator("#service-dialog")).toBeVisible();
        await inspect(page, { feature: true });
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toHaveCount(0);
      }
      if (view === "library") {
        await page.getByRole("button", { name: "新建素材", exact: true }).click();
        await expect(page.locator(".material-editor")).toBeVisible();
        await expect(page.getByLabel("素材名称", { exact: true })).toBeVisible();
        await inspect(page, { feature: true });
        await page.locator(".material-editor").getByRole("button", { name: "取消", exact: true }).click();
        await expect(page.locator(".material-editor")).toHaveCount(0);
        await page.getByRole("button", { name: "放大查看 林夏参考 · 正面参考", exact: true }).click();
        await expect(page.locator("#material-preview-dialog")).toBeVisible();
        await inspect(page, { feature: true });
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toHaveCount(0);
      }
      await inspect(page, { feature: true });
      if (view === "activity") {
        await page.getByRole("button", { name: "问题与待办", exact: true }).click();
        await expect(page.locator(".activity-list")).toContainText("第2集连续性审核退回作者");
        await expect(page.getByRole("button", { name: "问题与待办", exact: true })).toHaveClass(/selected/);
        await inspect(page, { feature: true });
      }
      await capture(page, "global", view, width, theme);
    }
    await verifyReadOnly(page, writes, errors);
  });

  test(`all drama tabs use neutral reading surfaces and explicit selection at ${width} ${theme}`, async ({ page, request }) => {
    test.setTimeout(90000);
    await configure(page); const { drama } = await install(page, request);
    const errors = [], writes = []; page.on("pageerror", e => errors.push(e.message)); page.on("request", r => { if (r.url().includes("/api/") && !["GET", "HEAD"].includes(r.method())) writes.push(r.url()); });
    for (const [tab, label, content] of [["overview", "项目总览", "已建立3 / 3集"], ["plan", "全剧规划", "全剧规划已通过独立审核"], ["assets", "共享设定", "灰色外套"], ["episodes", "剧集", "第3集 · 重新出发"], ["workflow", "制作流程与团队", "全剧规划独立审核"], ["batches", "生成批次", "第1、2集 · 设定v1"], ["results", "成果", "无字幕成片"]]) {
      await page.goto(`/#project=${dramaId}&tab=${tab}`);
      await expect(page.locator("#workspace h1")).toHaveText(drama.title);
      await expect(page.locator("#workspace")).toContainText(content);
      const nav = page.getByRole("navigation", { name: "整剧工作区", exact: true });
      if (["plan", "assets", "batches"].includes(tab)) {
        await nav.getByRole("button", { name: "项目资料", exact: true }).click();
        await expect(page.getByRole("menuitem", { name: label, exact: true })).toHaveAttribute("data-selected", "true");
        await page.keyboard.press("Escape");
      } else await expect(nav.getByRole("button", { name: label, exact: true })).toHaveAttribute("aria-pressed", "true");
      if (tab === "plan") await expect(page.getByLabel("全剧主线", { exact: true })).toHaveValue(drama.draft_plan.storyline);
      if (tab === "workflow") { await expect(page.locator("[data-stage]")).toHaveCount(5); await expect(page.locator("[data-flow-edge]")).not.toHaveCount(0); }
      if (tab === "episodes") { await page.getByRole("checkbox", { name: "选择第3集", exact: true }).check(); await expect(page.getByRole("button", { name: "生成所选 1 集", exact: true })).toBeEnabled(); }
      if (tab === "results") await expect(page.locator(".drama-video")).toHaveCount(1);
      await inspect(page, { feature: tab === "overview" });
      if (["overview", "workflow"].includes(tab)) await capture(page, "drama", tab, width, theme);
    }
    await verifyReadOnly(page, writes, errors);
  });

  test(`episode and production-unit workbenches share the palette at ${width} ${theme}`, async ({ page, request }) => {
    test.setTimeout(90000);
    await configure(page); await install(page, request);
    const errors = [], writes = []; page.on("pageerror", e => errors.push(e.message)); page.on("request", r => { if (r.url().includes("/api/") && !["GET", "HEAD"].includes(r.method())) writes.push(r.url()); });
    for (const tab of ["overview", "workflow"]) {
      await page.goto(`/#project=${episodeId}&tab=${tab}`);
      await expect(page.locator("#workspace h1")).toHaveText("第1集 · 车站相遇");
      await expect(page.locator("#workspace")).toContainText("E001-C01-U001 · 车站相遇");
      if (tab === "overview") { await expect(page.getByRole("link", { name: "字幕", exact: true })).toHaveAttribute("href", /subtitles\.srt$/); await expect(page.getByLabel("局部修改或重新剪辑意见", { exact: true })).toBeVisible(); }
      else await expect(page.locator("[data-stage]")).toHaveCount(4);
      await inspect(page, { feature: tab === "overview" });
    }
    for (const [tab, selector] of [["overview", ".project-focus"], ["script", ".shot-story-card"], ["materials", ".material-library-toolbar"], ["shots", ".shot-workbench"], ["delivery", ".delivery-video"], ["decisions", 'form[data-form="final"]'], ["quality", ".quality-shot"], ["team", ".team-task-detail"], ["costs", ".cost-totals"]]) {
      await page.goto(`/#project=${unitId}&tab=${tab}`);
      await expect(page.locator(selector).first()).toBeVisible();
      if (tab === "script") await expect(page.locator(".shot-story-grid")).toContainText("这封信，终于回到了你手里。");
      if (tab === "shots") await expect(page.locator(".candidate-card video")).toBeVisible();
      if (tab === "delivery") await expect(page.getByRole("link", { name: "字幕文件 SRT", exact: true })).toHaveAttribute("href", /subtitles\.srt$/);
      if (tab === "team") await expect(page.locator(".team-task-detail")).toContainText("车站相遇镜头视频");
      if (tab === "quality") await expect(page.locator(".quality-shot")).toContainText("角色身份与跨镜连续性通过审核。");
      if (tab === "costs") await expect(page.locator("#workspace")).toContainText("fixture-receipt-1");
      await inspect(page, { feature: tab === "overview" });
      if (tab === "overview") { await openWorkflow(page); await expect(page.locator("[data-stage]")).not.toHaveCount(0); await inspect(page); }
      if (["shots", "delivery"].includes(tab)) await capture(page, "unit", tab, width, theme);
    }
    await verifyReadOnly(page, writes, errors);
  });

  test(`all six palette choices reach global, drama and unit pages at ${width} ${theme}`, async ({ page, request }) => {
    test.setTimeout(120000);
    await configure(page); await install(page, request);
    const errors = [], writes = []; page.on("pageerror", e => errors.push(e.message)); page.on("request", r => { if (r.url().includes("/api/") && !["GET", "HEAD"].includes(r.method())) writes.push(r.url()); });
    const observed = new Set();
    for (const [id, label] of palettes) {
      await page.goto("/#view=settings");
      await page.getByRole("group", { name: "主题色", exact: true }).getByText(label, { exact: true }).click();
      await expect(page.locator("html")).toHaveAttribute("data-accent", id);
      for (const hash of ["view=models", `project=${dramaId}&tab=overview`, `project=${unitId}&tab=shots`]) {
        await page.goto(`/#${hash}`);
        await expect(page.locator("html")).toHaveAttribute("data-accent", id);
        await expect(page.locator(hash.includes("tab=shots") ? ".shot-workbench" : ".page-summary").first()).toBeVisible();
        await inspect(page, { feature: !hash.includes("tab=shots") });
      }
      observed.add(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--accent-feature").trim()));
    }
    expect(observed.size).toBe(6);
    await verifyReadOnly(page, writes, errors);
  });
}
