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

it("retains a successfully uploaded view when a later file fails, without creating another material on retry", async () => {
  const posts: any[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_path, init) => {
      if (init.method !== "POST")
        return new Response(JSON.stringify({ assets: [], tasks: [] }));
      const body = JSON.parse(init.body);
      posts.push(body);
      return new Response(
        JSON.stringify({
          asset: {
            asset_id: "mat_partial",
            version: body.action === "create" ? 1 : 2,
            kind: "character",
            data: { document: { name: "林夏" }, images: [] },
          },
        }),
      );
    }),
  );
  const user = userEvent.setup({ applyAccept: false });
  mount();
  await user.click(screen.getByRole("button", { name: "新建素材" }));
  await user.type(screen.getByLabelText("素材名称"), "林夏");
  await user.upload(screen.getByLabelText("添加参考图片"), [
    new File(["fixture"], "front.png", { type: "image/png" }),
    new File(["bad"], "bad.svg", { type: "image/svg+xml" }),
  ]);
  await user.click(screen.getByRole("button", { name: "保存素材" }));
  await screen.findByText("请选择不超过8 MiB的PNG/JPEG/WebP图片。");
  expect(posts.map((post) => post.action)).toEqual(["create", "upload"]);
  await user.click(screen.getByRole("button", { name: "保存素材" }));
  await waitFor(() => expect(posts).toHaveLength(3));
  expect(posts[2]).toMatchObject({
    action: "revise",
    asset_id: "mat_partial",
    expected_version: 2,
  });
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
