import { test, expect } from "@playwright/test";

const pid = "012345abcdef";
test("upload named references, select stable character and confirm the drama package", async ({
  page,
  request,
}) => {
  await request.post("/__test/reset");
  const assets = [];
  let selected = [],
    confirmed = false,
    revision = 7;
  const posts = [];
  await page.route("**/api/factory/library**", async (route) => {
    const req = route.request(),
      url = new URL(req.url());
    if (url.pathname.endsWith("/tasks"))
      return route.fulfill({ json: { tasks: [] } });
    if (req.method() === "GET")
      return route.fulfill({ json: { assets, has_more: false } });
    const body = req.postDataJSON();
    posts.push(body);
    let asset;
    if (body.action === "create") {
      asset = {
        asset_id: "mat_browser",
        version: 1,
        library_key: "global",
        kind: body.kind,
        status: "candidate",
        data: { document: body.document, images: [] },
      };
      assets.push(asset);
    } else {
      asset = assets.find((a) => a.asset_id === body.asset_id);
      expect(body.expected_version).toBe(asset.version);
      if (body.action === "upload") {
        expect(body.image_base64.length).toBeGreaterThan(100);
        asset.version++;
        asset.data.images.push({
          path: "production/factory-" + pid + "/frames/frame.png",
          label: body.label,
          sha256: "synthetic-browser-checksum",
        });
      }
    }
    return route.fulfill({ json: { asset } });
  });
  await page.route(
    "**/api/factory/projects/" + pid + "/materials",
    async (route) => {
      const req = route.request();
      if (req.method() === "POST") {
        const body = req.postDataJSON();
        posts.push(body);
        expect(body.expected_revision).toBe(revision);
        if (body.action === "select") selected = body.references;
        else if (body.action === "confirm") {
          expect(body.confirmed).toBe(true);
          confirmed = true;
        }
        revision++;
        return route.fulfill({ json: { project: { id: pid, revision } } });
      }
      return route.fulfill({
        json: {
          revision,
          selected: selected.map((binding) => ({
            binding,
            asset: assets.find((a) => a.asset_id === binding.asset_id),
          })),
          shots: [],
          package: confirmed ? { references: selected } : null,
        },
      });
    },
  );
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/#view=library");
  await expect(page.locator("#workspace h1")).toHaveText("统一素材库");
  await page.getByRole("button", { name: "新建素材", exact: true }).click();
  await page.getByLabel("素材名称", { exact: true }).fill("林夏参考");
  await page
    .getByLabel("不变的身份与外观")
    .fill("成年，短发，圆脸，固定身份。");
  await page
    .getByLabel("添加参考图片")
    .setInputFiles("runtime/browser-fixtures/frame.png");
  await page.getByRole("button", { name: "保存素材", exact: true }).click();
  await expect(page.locator("#workspace")).toContainText("林夏参考 · v2");
  await expect(page.getByRole("img", { name: /林夏参考/ })).toBeVisible();
  await page.goto("/#project=" + pid + "&tab=materials");
  await expect(page.locator("#workspace h1").last()).toHaveText(
    "本剧素材与定稿",
  );
  await page.getByRole("button", { name: "选入本剧", exact: true }).click();
  await expect(page.locator("#workspace")).toContainText(
    "林夏参考 · v2 · female",
  );
  await page
    .getByRole("button", { name: "确认本批视觉定稿", exact: true })
    .click();
  await expect(page.locator("#workspace")).toContainText("已有定稿");
  expect(
    posts.some(
      (body) =>
        body.action === "select" && body.references[0].role === "female",
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
  await page.screenshot({
    path: "runtime/browser-report/material-library.png",
    fullPage: true,
  });
});
