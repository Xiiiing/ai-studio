import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request, page }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    return ["127.0.0.1", "localhost"].includes(url.hostname)
      ? route.continue()
      : route.abort("blockedbyclient");
  });
});
async function models(page) {
  await page.goto("/");
  await page.locator('.main-nav [data-action="models"]').click();
  await expect(page.locator("#workspace h1")).toHaveText("模型与连接");
}

test("cloud preset supplies native protocol defaults and only an environment key name", async ({
  page,
  request,
}) => {
  await models(page);
  await page.getByLabel("使用方式", { exact: true }).selectOption("api");
  await page.locator('[data-model-action="add"]').click();
  await page.locator('#model-dialog input[name="id"]').fill("my-gemini");
  await page
    .locator('#model-dialog select[name="provider"]')
    .selectOption("gemini");
  await expect(
    page.locator('#model-dialog input[name="base_url"]'),
  ).toHaveValue("https://generativelanguage.googleapis.com/v1beta");
  await expect(
    page.locator('#model-dialog input[name="api_key_env"]'),
  ).toHaveValue("GEMINI_API_KEY");
  await page.locator('#model-dialog button[type="submit"]').click();
  await page.locator('[data-model-action="save"]').click();
  await expect(
    page.getByRole("heading", { name: "my-gemini", exact: true }),
  ).toBeVisible();
  const calls = await (await request.get("/__test/calls")).json();
  const saved = calls.find(
    (c) => c.method === "POST" && c.path === "/api/factory/models",
  ).body;
  expect(saved.profiles["my-gemini"]).toMatchObject({
    provider: "gemini",
    response_format: "prompt",
    api_key_env: "GEMINI_API_KEY",
    vision: true,
  });
  expect(saved.profiles["my-gemini"]).not.toHaveProperty("api_key");
  expect(calls.some((c) => c.path.includes("generateContent"))).toBeFalsy();
});

test("API speech connection and custom workflow import save without generation", async ({
  page,
  request,
}) => {
  await models(page);
  await page.getByLabel("使用方式", { exact: true }).selectOption("api");
  await page.getByRole("button", { name: "添加配音连接", exact: true }).click();
  await page.getByLabel("制作连接编号", { exact: true }).fill("my-voice");
  await expect(page.getByLabel("制作模型标识", { exact: true })).toHaveValue(
    "qwen3-tts-flash",
  );
  await expect(
    page.getByLabel("制作密钥环境变量", { exact: true }),
  ).toHaveValue("DASHSCOPE_API_KEY");
  await page
    .getByRole("button", { name: "加入制作配置草稿", exact: true })
    .click();
  await page
    .getByLabel("配音使用的连接", { exact: true })
    .selectOption("my-voice");
  await page.locator('[data-model-action="save"]').click();
  await page.getByLabel("使用方式", { exact: true }).selectOption("mixed");
  await page
    .getByText("高级：ComfyUI 工作流导入／导出", { exact: true })
    .click();
  const template = {
    name: "Fixture API graph",
    kind: "image",
    graph: { 1: { class_type: "FixturePrompt", inputs: { text: "" } } },
    mappings: [{ node: "1", input: "text", source: "prompt" }],
    output: { node: "1", field: "images", index: 0 },
  };
  await page
    .getByLabel("工作流模板 JSON", { exact: true })
    .fill(JSON.stringify(template));
  await page.getByRole("button", { name: "导入模板", exact: true }).click();
  await expect(page.getByLabel("模板编号", { exact: true })).toHaveValue(
    /^[a-f0-9]{64}$/,
  );
  const calls = await (await request.get("/__test/calls")).json();
  const saved = calls.find(
    (c) => c.method === "POST" && c.path === "/api/factory/models",
  ).body;
  expect(saved.audio_profiles["my-voice"].provider).toBe("dashscope_tts");
  expect(saved.audio_roles.speech_generator).toBe("my-voice");
  expect(
    calls.some(
      (c) => c.path.endsWith("/workflow-templates") && c.method === "POST",
    ),
  ).toBeTruthy();
  expect(
    calls.some(
      (c) => c.path.includes("/prompt") || c.path.includes("/generation"),
    ),
  ).toBeFalsy();
});

test("video API presets provide a model ID and environment name", async ({
  page,
}) => {
  await models(page);
  await page.locator('[data-model-action="video-add"]').click();
  await expect(page.locator('#model-dialog input[name="model"]')).toHaveValue(
    "MiniMax-Hailuo-2.3",
  );
  await expect(
    page.locator('#model-dialog input[name="api_key_env"]'),
  ).toHaveValue("MINIMAX_API_KEY");
});

test("ComfyUI H3 saves as a server workflow without cloud API credentials", async ({ page, request }) => {
  await models(page);
  await page.locator('[data-model-action="video-add"]').click();
  await page.locator('#model-dialog input[name="id"]').fill("wkp_h3");
  await page.locator('#model-dialog select[name="provider"]').selectOption("comfyui_h3");
  await expect(page.locator('#model-dialog input[name="model"]')).toHaveValue("MiniMax-H3");
  await expect(page.locator('#model-dialog input[name="api_key_env"]')).toHaveCount(0);
  await page.locator('#model-dialog button[type="submit"]').click();
  await page.locator('#role-video-video_generator').selectOption("wkp_h3");
  await expect(page.getByText(/ComfyUI \/ MiniMax H3 双 GPU 工作流/)).toBeVisible();
  await page.locator('[data-model-action="save"]').click();
  const calls = await (await request.get("/__test/calls")).json();
  const saved = calls.find(c => c.path === "/api/factory/models" && c.method === "POST").body;
  expect(saved.video_roles.video_generator).toBe("wkp_h3");
  expect(saved.video_profiles.wkp_h3).toMatchObject({ provider: "comfyui_h3", model: "MiniMax-H3" });
  expect(saved.video_profiles.wkp_h3).not.toHaveProperty("api_key_env");
  expect(calls.some(c => c.path.includes("video_generation") || c.path.includes("/inference/jobs"))).toBeFalsy();
});
