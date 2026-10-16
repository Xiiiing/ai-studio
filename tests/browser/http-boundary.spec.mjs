import { test, expect } from "@playwright/test";
import { startRealServer } from "./real-server.mjs";
let server, endpoint, projectId, serverResponses;
test.beforeAll(async () => {
  server = await startRealServer();
  ({ endpoint, projectId, responses: serverResponses } = server);
});
test.afterAll(async () => {
  await server?.stop();
});
test.beforeEach(async ({ page }) => {
  await page.route("**/*", (route) => {
    const host = new URL(route.request().url()).hostname;
    return ["127.0.0.1", "localhost"].includes(host)
      ? route.continue()
      : route.abort("blockedbyclient");
  });
});

test("real Python boundary admits the new console and rejects the retired interface", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  expect((await page.goto(endpoint + "/")).status()).toBe(200);
  await expect(page.locator("#project-list")).toContainText(
    "HTTP 边界隔离测试",
  );
  await page.locator('#project-list [data-id="' + projectId + '"]').click();
  await expect(page.locator("#workspace h1")).toHaveText("HTTP 边界隔离测试");
  await page.locator('.main-nav [data-action="models"]').click();
  await expect(page.locator("#workspace h1")).toHaveText("模型与连接");
  expect(errors).toEqual([]);
  expect((await page.goto(endpoint + "/legacy")).status()).toBe(410);
});

test("real Python media response supports browser decoding and range playback", async ({
  page,
}) => {
  await page.goto(endpoint + "/");
  await page.evaluate(async (url) => {
    const video = document.createElement("video");
    video.id = "http-media-smoke";
    video.muted = true;
    video.controls = true;
    video.src = url;
    document.body.append(video);
    await video.play();
  }, endpoint + "/media/production/http-fixture/clip.mp4");
  await page.waitForFunction(
    () => document.querySelector("#http-media-smoke").currentTime > 0.2,
  );
  expect(
    await page
      .locator("#http-media-smoke")
      .evaluate((v) => v.readyState >= 2 && !v.paused),
  ).toBeTruthy();
});

test("cross-origin browser POST cannot reach the legacy production launcher", async ({
  page,
}) => {
  await page.goto("/"); // Different loopback port: deliberately a different origin.
  const url = endpoint + "/api/start";
  const outcome = await page.evaluate(
    (url) =>
      fetch(url, {
        method: "POST",
        mode: "no-cors",
        headers: { "Content-Type": "text/plain" },
        body: "{}",
      }).then(
        (response) => response.type,
        () => "blocked",
      ),
    url,
  );
  // Require both browser isolation and actual server rejection. A mere network
  // failure, browser-only block, 409 from the launcher, or opaque 2xx cannot pass.
  expect(outcome).toBe("blocked");
  await expect
    .poll(() =>
      serverResponses.findLast(
        (response) =>
          response.method === "POST" && response.path === "/api/start",
      ),
    )
    .toEqual({
      method: "POST",
      path: "/api/start",
      status: 403,
      launch_attempts: 0,
    });
});
