import { addService, openServiceDetails } from "./model-flow.mjs";
import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request, page }) => {
  await request.post("/__test/reset");
  await page.route("**/*", (route) =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort("blockedbyclient"),
  );
});
async function models(page) {
  await page.goto("/#view=models");
  await expect(page.locator("#workspace h1")).toHaveText("系统设置");
  await expect(page.locator(".model-service-card").first()).toBeVisible();
}
async function saveCalls(request) {
  return (await (await request.get("/__test/calls")).json()).filter(
    (c) => c.method === "POST" && c.path === "/api/factory/models",
  );
}
async function noOverflow(page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
}
for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`cloud Key and multi-model custom service save once on ${viewport.name}`, async ({
    page,
    request,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await models(page);
    await noOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`services-${viewport.name}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "添加服务", exact: true }).click();
    await expect
      .poll(() =>
        page
          .locator("#service-dialog img")
          .evaluateAll(
            (imgs) =>
              imgs.length > 0 &&
              imgs.every((img) => img.complete && img.naturalWidth > 0),
          ),
      )
      .toBe(true);
    await page.locator("#service-dialog").screenshot({
      path: testInfo.outputPath(`providers-${viewport.name}.png`),
      animations: "disabled",
    });
    await page.getByRole("button", { name: "DeepSeek", exact: true }).click();
    const dialog = page.locator("#service-dialog");
    await expect(dialog.locator('input[name="base_url"]')).toBeHidden();
    await expect(dialog.getByLabel("服务端密钥变量")).toBeHidden();
    await dialog
      .getByLabel("API Key", { exact: true })
      .fill("fixture-cloud-token");
    await expect(dialog.getByLabel("API Key", { exact: true })).toHaveAttribute(
      "type",
      "password",
    );
    expect(
      await dialog
        .locator(".service-editor-brand img")
        .evaluate((img) => img.complete && img.naturalWidth > 0),
    ).toBeTruthy();
    await noOverflow(page);
    await expect(
      dialog.getByRole("button", { name: "保存连接" }),
    ).toBeInViewport();
    await dialog.screenshot({
      path: testInfo.outputPath(`cloud-key-${viewport.name}.png`),
    });
    await dialog.getByRole("button", { name: "保存连接" }).click();
    await expect(dialog).toHaveCount(0);
    let saves = await saveCalls(request);
    expect(saves).toHaveLength(1);
    const cloud = Object.entries(saves[0].body.profiles).find(
      ([, p]) => p.provider === "deepseek",
    );
    expect(cloud[1]).toMatchObject({
      model: "deepseek-chat",
      base_url: "https://api.deepseek.com",
      response_format: "json_object",
    });
    expect(saves[0].body.secrets).toEqual({
      ["llm:" + cloud[0]]: "fixture-cloud-token",
    });
    const cloudDetails = await openServiceDetails(page, "llm:" + cloud[0]);
    await expect(cloudDetails.locator(".model-name")).toHaveText(
      "deepseek-chat",
    );
    await page.keyboard.press("Escape");
    await page.reload();
    await expect(page.locator(".service-card-grid")).toBeVisible();
    await expect(page.locator("#model-service-detail-dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "添加服务", exact: true }).click();
    await page
      .getByRole("button", { name: "OpenAI-compatible API", exact: true })
      .click();
    await dialog
      .getByLabel("服务 URL", { exact: true })
      .fill("http://127.0.0.1:8090/gateway/v1/chat/completions");
    await dialog
      .getByLabel("API Key", { exact: true })
      .fill("fixture-local-token");
    await dialog.getByRole("button", { name: "获取模型", exact: true }).click();
    await expect(dialog.getByText(/读取到 2 个模型/)).toBeVisible();
    await dialog
      .getByLabel("fixture-discovered-model", { exact: true })
      .check();
    await dialog.getByLabel("fixture-image-model", { exact: true }).check();
    await expect(
      dialog.getByText("已选 2 个模型", { exact: true }),
    ).toBeVisible();
    await noOverflow(page);
    await expect(
      dialog.getByRole("button", { name: "保存连接" }),
    ).toBeInViewport();
    await dialog.screenshot({
      path: testInfo.outputPath(`custom-url-${viewport.name}.png`),
    });
    await dialog.getByRole("button", { name: "保存连接" }).click();
    await expect(dialog).toHaveCount(0);
    saves = await saveCalls(request);
    expect(saves).toHaveLength(2);
    const custom = Object.entries(saves[1].body.profiles).filter(
      ([, p]) => p.base_url === "http://127.0.0.1:8090/gateway/v1",
    );
    expect(custom).toHaveLength(2);
    for (const [id, p] of custom) {
      expect(p).toMatchObject({ network_mode: "loopback", allowed_hosts: [] });
      expect(saves[1].body.secrets["llm:" + id]).toBe("fixture-local-token");
    }
    expect(
      custom.find(([, p]) => p.model === "fixture-image-model")[1].vision,
    ).toBe(true);
    expect(
      custom.find(([, p]) => p.model === "fixture-discovered-model")[1].vision,
    ).toBe(false);
    await page.reload();
    const customDetails = await openServiceDetails(page, "llm:" + custom[0][0]);
    await expect(customDetails.locator(".model-name")).toHaveCount(2);
    await page.getByRole("button", { name: "管理服务", exact: true }).click();
    await expect(dialog.getByLabel("API Key", { exact: true })).toHaveValue("");
    await dialog
      .getByLabel("API Key", { exact: true })
      .fill("fixture-rotated-token");
    await dialog.getByRole("button", { name: "保存连接" }).click();
    await expect(dialog).toHaveCount(0);
    await page.getByRole("tab", { name: "制作分工" }).click();
    await page
      .getByLabel("文本创作模型", { exact: true })
      .selectOption(custom[0][0]);
    await page
      .getByLabel("视觉审核模型", { exact: true })
      .selectOption(custom.find(([, p]) => p.vision)[0]);
    await expect(
      page.getByRole("button", { name: "保存分工" }),
    ).toBeInViewport();
    await page.screenshot({
      path: testInfo.outputPath(`assignments-${viewport.name}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "保存分工" }).click();
    await expect(page.getByRole("button", { name: "保存分工" })).toBeDisabled();
    const publicConfig = await (
      await request.get("/api/factory/models")
    ).json();
    expect(JSON.stringify(publicConfig)).not.toMatch(
      /fixture-(cloud|local|rotated)-token/,
    );
    const storage = await page.evaluate(() =>
      JSON.stringify([
        Object.entries(localStorage),
        Object.entries(sessionStorage),
      ]),
    );
    expect(storage).not.toMatch(/fixture-(cloud|local|rotated)-token/);
    const calls = await (await request.get("/__test/calls")).json();
    expect(
      calls.some((c) =>
        /generateContent|chat\/completions$|\/inference\/jobs|video_generation/.test(
          c.path,
        ),
      ),
    ).toBe(false);
    await noOverflow(page);
  });
  test(`H3 media recipe and assignments work on ${viewport.name}`, async ({
    page,
    request,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await models(page);
    await addService(page, "视频");
    await page
      .getByRole("button", { name: "ComfyUI / MiniMax H3", exact: true })
      .click();
    const dialog = page.locator("#media-dialog");
    await expect(dialog.getByLabel("模型 ID", { exact: true })).toHaveValue(
      "MiniMax-H3",
    );
    await expect(dialog.getByLabel("API Key", { exact: true })).toHaveCount(0);
    await dialog
      .getByLabel("H3 质量档", { exact: true })
      .selectOption("preview");
    await expect(dialog.getByLabel("采样步数", { exact: true })).toHaveCount(0);
    await dialog
      .getByLabel("H3 质量档", { exact: true })
      .selectOption("production");
    await dialog.getByLabel("采样步数", { exact: true }).fill("28");
    await dialog.getByText("采样参数", { exact: true }).click();
    await dialog.getByLabel("采样器", { exact: true }).selectOption("heun");
    await dialog.getByLabel("视频采样偏移", { exact: true }).fill("10.5");
    await noOverflow(page);
    await expect(
      dialog.getByRole("button", { name: "保存连接" }),
    ).toBeInViewport();
    await dialog.screenshot({
      path: testInfo.outputPath(`h3-${viewport.name}.png`),
    });
    await dialog.getByRole("button", { name: "保存连接" }).click();
    await expect(dialog).toHaveCount(0);
    const saved = (await saveCalls(request))[0].body;
    const [id, p] = Object.entries(saved.video_profiles).find(
      ([, p]) => p.provider === "comfyui_h3",
    );
    expect(p).toMatchObject({
      steps: 28,
      shift_video: 10.5,
      shift_audio: 3,
      sampler: "heun",
      quality: "production",
      revision: "wkp-dualgpu-base-v1",
    });
    await page.getByRole("tab", { name: "制作分工" }).click();
    await page.getByLabel("视频制作模型", { exact: true }).selectOption(id);
    await page.getByRole("button", { name: "保存分工" }).click();
    await expect(page.getByRole("button", { name: "保存分工" })).toBeDisabled();
    expect(
      (await saveCalls(request)).at(-1).body.video_roles.video_generator,
    ).toBe(id);
  });
}
test("native Gemini defaults remain under the same one-save flow", async ({
  page,
  request,
}) => {
  await models(page);
  await page.getByRole("button", { name: "添加服务", exact: true }).click();
  await page.getByRole("button", { name: "Gemini", exact: true }).click();
  const dialog = page.locator("#service-dialog");
  await expect(
    dialog.getByText("Gemini · generateContent", { exact: true }),
  ).toBeVisible();
  await dialog
    .getByLabel("API Key", { exact: true })
    .fill("fixture-gemini-token");
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).toHaveCount(0);
  const profiles = (await saveCalls(request))[0].body.profiles;
  expect(
    Object.values(profiles).find((p) => p.provider === "gemini"),
  ).toMatchObject({
    response_format: "prompt",
    vision: true,
    base_url: "https://generativelanguage.googleapis.com/v1beta",
  });
});
test("speech Key, ComfyUI URL and template tools retain their adapters", async ({
  page,
  request,
}) => {
  await models(page);
  await addService(page, "配音");
  await page.getByRole("button", { name: "百炼语音 API", exact: true }).click();
  let dialog = page.locator("#media-dialog");
  await expect(dialog.getByLabel("模型 ID", { exact: true })).toHaveValue(
    "qwen3-tts-flash",
  );
  await dialog
    .getByLabel("API Key", { exact: true })
    .fill("fixture-speech-token");
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).toHaveCount(0);
  await addService(page, "图像");
  await page
    .getByRole("button", { name: "自己的 ComfyUI 服务", exact: true })
    .click();
  await dialog
    .getByLabel("服务 URL", { exact: true })
    .fill("https://172.20.114.10:8188");
  await dialog
    .getByLabel("API Key", { exact: true })
    .fill("fixture-comfy-token");
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).toHaveCount(0);
  const saves = await saveCalls(request),
    profile = Object.values(saves[1].body.image_profiles).find(
      (p) => p.provider === "comfyui_external",
    );
  expect(profile).toMatchObject({
    base_url: "https://172.20.114.10:8188",
    network_mode: "lan",
    allowed_hosts: ["172.20.114.10"],
  });
  await page.getByRole("tab", { name: "高级工具" }).click();
  await page.getByLabel("工作流模板 JSON", { exact: true }).fill(
    JSON.stringify({
      name: "Fixture API graph",
      kind: "image",
      graph: { 1: { class_type: "FixturePrompt", inputs: { text: "" } } },
      mappings: [{ node: "1", input: "text", source: "prompt" }],
      output: { node: "1", field: "images", index: 0 },
    }),
  );
  await page.getByRole("button", { name: "导入模板", exact: true }).click();
  await expect(page.getByLabel("模板编号", { exact: true })).toHaveValue(
    /^[a-f0-9]{64}$/,
  );
});
test("directory failure keeps a manual model path and preserves the typed key", async ({
  page,
}) => {
  await models(page);
  await page.route("**/models/discover", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ error: "目录接口不可用" }),
    }),
  );
  await page.getByRole("button", { name: "添加服务", exact: true }).click();
  await page
    .getByRole("button", { name: "OpenAI-compatible API", exact: true })
    .click();
  const dialog = page.locator("#service-dialog");
  await dialog
    .getByLabel("服务 URL", { exact: true })
    .fill("http://127.0.0.1:8000/v1");
  await dialog
    .getByLabel("API Key", { exact: true })
    .fill("fixture-manual-key");
  await dialog.getByRole("button", { name: "获取模型" }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "可继续手动添加模型 ID",
  );
  await dialog
    .getByLabel("手动添加模型 ID", { exact: true })
    .fill("manual-text");
  await dialog.getByRole("button", { name: "添加", exact: true }).click();
  await expect(dialog.getByLabel("API Key", { exact: true })).toHaveValue(
    "fixture-manual-key",
  );
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).toHaveCount(0);
});
test("small mobile service picker reaches media adapters and keeps cloud actions visible", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 320, height: 760 });
  await page.emulateMedia({ colorScheme: "dark" });
  await models(page);
  await page.getByRole("button", { name: "添加服务", exact: true }).click();
  await page
    .getByRole("group", { name: "服务用途" })
    .getByRole("button", { name: "视频", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "ComfyUI / MiniMax H3", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("group", { name: "服务用途" })
    .getByRole("button", { name: "文本与视觉", exact: true })
    .click();
  await page.getByRole("button", { name: "DeepSeek", exact: true }).click();
  const dialog = page.locator("#service-dialog");
  await dialog.getByText("API Key", { exact: true }).click();
  await expect(dialog.getByLabel("API Key", { exact: true })).toBeFocused();
  await expect(
    dialog.getByRole("button", { name: "保存连接" }),
  ).toBeInViewport();
  expect((await dialog.boundingBox()).width).toBe(320);
  await noOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("cloud-small-dark.png") });
});
