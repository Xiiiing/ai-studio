import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  MaterialLibrary,
  readMaterialFile,
} from "../../ui/factory/src/MaterialLibrary";

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});
function mount(project?: Record<string, any>) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <MaterialLibrary project={project} notify={vi.fn()} />
    </QueryClientProvider>,
  );
}
it("creates a named material in the unified library without requiring a project", async () => {
  const posts: any[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path, init) => {
      if (init.method === "POST") posts.push(JSON.parse(init.body));
      return new Response(
        JSON.stringify(
          init.method === "POST"
            ? { asset: { asset_id: "mat_one", version: 1 } }
            : { assets: [] },
        ),
      );
    }),
  );
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "新建素材" }));
  await user.type(screen.getByLabelText("素材名称"), "林夏");
  await user.click(screen.getByRole("button", { name: "保存素材" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    action: "create",
    scope: "global",
    kind: "character",
    document: { name: "林夏" },
  });
  expect(posts[0].project_id).toBeUndefined();
});
it("uses stable role IDs when selecting silent characters into a project", async () => {
  const posts: any[] = [];
  const project = {
    id: "012345abcdef",
    production_profile: {
      characters: [{ character_id: "char_lin", name: "林夏" }],
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path, init) => {
      if (init.method === "POST") posts.push(JSON.parse(init.body));
      const data = String(path).includes("/materials")
        ? { revision: 9, selected: [], shots: [] }
        : {
            assets: [
              {
                asset_id: "mat_lin",
                version: 3,
                kind: "character",
                library_key: "global",
                status: "approved",
                data: { document: { name: "林夏" }, images: [] },
              },
            ],
          };
      return new Response(JSON.stringify(data));
    }),
  );
  const user = userEvent.setup();
  mount(project);
  await user.click(await screen.findByRole("button", { name: "选入本剧" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    action: "select",
    expected_revision: 9,
    references: [{ asset_id: "mat_lin", version: 3, role: "char_lin" }],
  });
});
it("rejects unsupported files before making an upload request", async () => {
  await expect(
    readMaterialFile(new File(["bad"], "bad.svg", { type: "image/svg+xml" })),
  ).rejects.toThrow("PNG/JPEG/WebP");
});

it("preserves unfinished uploads and retries only the failed image after a partial save", async () => {
  const posts: any[] = [];
  let version = 0;
  let failed = false;
  const data = {
    document: { name: "林夏", description: "完整草稿" },
    images: [] as any[],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path, init) => {
      if (init.method !== "POST")
        return new Response(JSON.stringify({ assets: [], tasks: [] }));
      const body = JSON.parse(init.body);
      posts.push(body);
      if (body.label === "side.png" && !failed) {
        failed = true;
        return new Response(JSON.stringify({ error: "第二张上传失败" }), {
          status: 503,
        });
      }
      version++;
      if (body.action === "upload") data.images.push({ path: body.label });
      return new Response(
        JSON.stringify({
          asset: { asset_id: "mat_partial", version, kind: "character", data },
        }),
      );
    }),
  );
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "新建素材" }));
  await user.type(screen.getByLabelText("素材名称"), "林夏");
  await user.type(screen.getByLabelText("素材说明"), "完整草稿");
  await user.upload(screen.getByLabelText("添加参考图片"), [
    new File(["front"], "front.png", { type: "image/png" }),
    new File(["side"], "side.png", { type: "image/png" }),
  ]);
  await user.click(screen.getByRole("button", { name: "保存素材" }));
  await screen.findByText("第二张上传失败");
  expect(screen.getByLabelText("素材说明")).toHaveValue("完整草稿");
  expect(
    screen.queryByRole("button", { name: "移除待上传图片 front.png" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "移除待上传图片 side.png" }),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "保存素材" }));
  await waitFor(() => expect(screen.queryByLabelText("素材名称")).toBeNull());
  expect(posts.map((p) => p.action)).toEqual([
    "create",
    "upload",
    "upload",
    "upload",
  ]);
  expect(posts[3]).toMatchObject({
    asset_id: "mat_partial",
    expected_version: 2,
    label: "side.png",
  });
});

it("validates all selected files before creating a material and lets the user remove an invalid file", async () => {
  const posts: any[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path, init) => {
      if (init.method === "POST") posts.push(JSON.parse(init.body));
      return new Response(JSON.stringify({ assets: [], tasks: [] }));
    }),
  );
  const user = userEvent.setup({ applyAccept: false });
  mount();
  await user.click(screen.getByRole("button", { name: "新建素材" }));
  await user.type(screen.getByLabelText("素材名称"), "林夏");
  await user.upload(screen.getByLabelText("添加参考图片"), [
    new File(["front"], "front.png", { type: "image/png" }),
    new File(["bad"], "bad.svg", { type: "image/svg+xml" }),
  ]);
  await user.click(screen.getByRole("button", { name: "保存素材" }));
  await screen.findByText("请选择不超过8 MiB的PNG/JPEG/WebP图片。");
  expect(posts).toEqual([]);
  expect(screen.getByLabelText("素材名称")).toHaveValue("林夏");
  await user.click(
    screen.getByRole("button", { name: "移除待上传图片 bad.svg" }),
  );
  expect(
    screen.queryByRole("button", { name: "移除待上传图片 bad.svg" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "移除待上传图片 front.png" }),
  ).toBeVisible();
});

it("clears composition-only fields when changing a new material to a character", async () => {
  const posts: any[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path, init) => {
      if (init.method === "POST") posts.push(JSON.parse(init.body));
      return new Response(
        JSON.stringify({
          assets: [
            {
              asset_id: "mat_lin",
              version: 1,
              kind: "character",
              data: { document: { name: "林夏" } },
            },
          ],
          tasks: [],
          asset: {
            asset_id: "mat_new",
            version: 1,
            data: { document: { name: "新参考" } },
          },
        }),
      );
    }),
  );
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "新建素材" }));
  await user.type(screen.getByLabelText("素材名称"), "新参考");
  await user.selectOptions(screen.getByLabelText("新素材类型"), "composition");
  await user.click(await screen.findByRole("button", { name: "添加同框人物" }));
  await user.selectOptions(screen.getByLabelText("新素材类型"), "character");
  expect(screen.getByLabelText("素材名称")).toHaveValue("新参考");
  await user.click(screen.getByRole("button", { name: "保存素材" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].kind).toBe("character");
  expect(posts[0].document.members).toBeUndefined();
});

it("requires acknowledgement of an unpriced material batch before authorizing it", async () => {
  const posts: any[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path, init) => {
      if (init.method === "POST") posts.push(JSON.parse(init.body));
      const data = String(path).endsWith("/tasks")
        ? {
            tasks: [
              {
                id: "mt_test",
                name: "林夏",
                status: "waiting_authorization",
                lease: 4,
                signature: "fixed-inputs",
                result: {},
                quote: {
                  submissions: 2,
                  estimated_total_micros: null,
                  currency: "CNY",
                },
              },
            ],
          }
        : { assets: [] };
      return new Response(JSON.stringify(data));
    }),
  );
  const user = userEvent.setup();
  mount();
  const authorize = await screen.findByRole("button", {
    name: "授权此批素材生成",
  });
  expect(authorize).toBeDisabled();
  await user.click(
    screen.getByRole("checkbox", { name: /我接受本批价格未知/ }),
  );
  await user.click(authorize);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    action: "authorize",
    expected_lease: 4,
    signature: "fixed-inputs",
    allow_unpriced: true,
    confirmed: true,
  });
});

it("looks up character references independently of the composition-only gallery filter", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path) => {
      const filtered = String(path).includes("kind=composition");
      return new Response(
        JSON.stringify({
          assets: filtered
            ? []
            : [
                {
                  asset_id: "mat_lin",
                  version: 2,
                  kind: "character",
                  library_key: "global",
                  status: "approved",
                  data: { document: { name: "林夏" }, images: [] },
                },
              ],
        }),
      );
    }),
  );
  const user = userEvent.setup();
  mount();
  await user.selectOptions(screen.getByLabelText("素材分类"), "composition");
  await user.click(screen.getByRole("button", { name: "新建素材" }));
  const add = await screen.findByRole("button", { name: "添加同框人物" });
  await waitFor(() => expect(add).toBeEnabled());
  await user.click(add);
  expect(screen.getByLabelText("同框人物1")).toHaveValue("mat_lin:2");
});

it("uses canonical server character IDs for episode-owned profiles", async () => {
  const posts: any[] = [];
  let resolveSelection!: (response: Response) => void;
  const pendingSelection = new Promise<Response>((resolve) => {
    resolveSelection = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path, init) => {
      if (init.method === "POST") posts.push(JSON.parse(init.body));
      if (String(path).includes("/materials")) return pendingSelection;
      const data = String(path).includes("/materials")
        ? {
            revision: 11,
            characters: [{ character_id: "char_witness", name: "见证者" }],
            selected: [],
          }
        : {
            assets: [
              {
                asset_id: "mat_lin",
                version: 2,
                kind: "character",
                data: { document: { name: "林夏" }, images: [] },
              },
            ],
            tasks: [],
          };
      return new Response(JSON.stringify(data));
    }),
  );
  mount({ id: "012345abcdef" });
  await screen.findByRole("heading", { name: "林夏 · v2" });
  resolveSelection(
    new Response(
      JSON.stringify({
        revision: 11,
        characters: [{ character_id: "char_witness", name: "见证者" }],
        selected: [],
      }),
    ),
  );
  const user = userEvent.setup();
  expect(
    await screen.findByRole("option", { name: "见证者（char_witness）" }),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "选入本剧" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].references[0].role).toBe("char_witness");
});

it("offers separate retries and never describes failed queries as empty libraries or empty task lists", async () => {
  let recover = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      recover
        ? new Response(JSON.stringify({ assets: [], tasks: [] }))
        : new Response(JSON.stringify({ error: "暂时无法读取" }), {
            status: 503,
          }),
    ),
  );
  mount();
  await screen.findByRole("button", { name: "重新读取素材库" });
  expect(screen.queryByText("还没有素材")).toBeNull();
  expect(screen.queryByText("尚无素材制作任务。")).toBeNull();
  recover = true;
  await userEvent.click(screen.getByRole("button", { name: "重新读取素材库" }));
  expect(await screen.findByText("还没有素材")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "重新读取素材任务" }),
  );
  expect(await screen.findByText("尚无素材制作任务。")).toBeVisible();
});
