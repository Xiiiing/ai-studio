import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  DocumentReader,
  DocumentValue,
  MaterialCard,
  documentUrl,
  readDocument,
  MAX_PREVIEW_BYTES,
} from "../../ui/factory/src/DocumentReader";
const id = "012345abcdef",
  url = `/media/production/factory-${id}/planning/script.md`;
function mount(item: any, material = false) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      {material ? (
        <MaterialCard item={item} projectId={id} />
      ) : (
        <DocumentReader item={item} projectId={id} />
      )}
    </QueryClientProvider>,
  );
}
describe("project document reading", () => {
  it("prepares Markdown inside a local boundary while surrounding controls and raw text stay available", async () => {
    render(
      <>
        <button>返回工作区</button>
        <DocumentValue value="未经转换的原文" raw />
        <DocumentValue value="# 延迟排版完成" />
      </>,
    );
    expect(screen.getByRole("button", { name: "返回工作区" })).toBeVisible();
    expect(screen.getByText("未经转换的原文")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("正在准备文档排版");
    expect(
      await screen.findByRole("heading", { name: "延迟排版完成" }),
    ).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("loads file-only inputs on demand and renders Markdown tables, lists and code", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(
          '# 故事目标\n\n**人物动机**\n\n- 履行承诺\n\n|镜头|动作|\n|---|---|\n|S01|开门|\n\n```json\n{"ok":true}\n```',
        ),
      );
    vi.stubGlobal("fetch", fetcher);
    mount({ label: "输入故事", artifact_url: url }, true);
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.queryByText("尚未生成")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "阅读文件" }));
    expect(
      await screen.findByRole("heading", { name: "故事目标" }),
    ).toBeVisible();
    expect(screen.getByRole("table")).toHaveTextContent("S01");
    expect(screen.getByRole("list")).toHaveTextContent("履行承诺");
    await userEvent.click(screen.getByRole("button", { name: "原文" }));
    expect(screen.getByText(/# 故事目标/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "放大阅读" }));
    expect(
      within(screen.getByRole("dialog")).getByText(/# 故事目标/),
    ).toBeVisible();
    expect(
      screen.getAllByRole("link", { name: "下载原文件" })[0],
    ).toHaveAttribute("href", expect.stringContaining(url));
  });
  it("retains prompts and messages in JSON execution originals", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            prompt: "完整系统提示",
            messages: [{ role: "user", content: "完整输入" }],
          }),
        ),
      ),
    );
    mount({
      label: "请求原文",
      artifact_url: url.replace("script.md", "request-01.json"),
    });
    expect(await screen.findByText("完整系统提示")).toBeVisible();
    expect(screen.getByText(/完整输入/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "原文" }));
    expect(screen.getByText(/"prompt"\s*:\s*"完整系统提示"/)).toBeVisible();
  });
  it("distinguishes missing files and retries without losing the file link", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response("", { status: 404 }))
        .mockResolvedValueOnce(new Response("# 已恢复")),
    );
    mount({ artifact_url: url });
    expect(await screen.findByRole("alert")).toHaveTextContent("文件缺失");
    await userEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(
      await screen.findByRole("heading", { name: "已恢复" }),
    ).toBeVisible();
  });
  it("limits streamed bytes even without Content-Length", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(new Response(new Uint8Array(MAX_PREVIEW_BYTES + 1))),
    );
    await expect(readDocument(url)).rejects.toThrow("2 MiB");
  });
  it("keeps oversized files downloadable and rejects their preview", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("unused", {
          headers: { "Content-Length": String(MAX_PREVIEW_BYTES + 1) },
        }),
      ),
    );
    mount({ artifact_url: url });
    expect(await screen.findByRole("alert")).toHaveTextContent("2 MiB");
    expect(screen.getByRole("link", { name: "下载原文件" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "重新读取" }),
    ).not.toBeInTheDocument();
  });
  it("skips HTML and prevents unowned image and document requests", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            "# 文档\n<script>alert(1)</script>\n\n![外部图片](https://other.invalid/image.png)\n\n[危险](javascript:alert(1))",
          ),
        ),
    );
    mount({ artifact_url: url });
    await screen.findByRole("heading", { name: "文档" });
    expect(document.querySelector("script")).toBeNull();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(documentUrl(id, "https://other.invalid" + url)).toBe("");
    expect(documentUrl(id, url.replace(id, "abcdef012345"))).toBe("");
    expect(documentUrl(id, url + "?secret=1")).toBe("");
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  });
});

it("keeps the open document visible while the same file refreshes at a new project revision", async () => {
  let resolveRefresh!: (response: Response) => void;
  const refresh = new Promise<Response>((resolve) => {
    resolveRefresh = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(new Response("# 正在阅读的文档"))
      .mockReturnValueOnce(refresh),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const view = (revision: number) => (
    <QueryClientProvider client={client}>
      <DocumentReader
        item={{ label: "剧本", artifact_url: url, revision }}
        projectId={id}
      />
    </QueryClientProvider>
  );
  const result = render(view(1));
  expect(
    await screen.findByRole("heading", { name: "正在阅读的文档" }),
  ).toBeVisible();
  result.rerender(view(2));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("heading", { name: "正在阅读的文档" })).toBeVisible();
  expect(screen.queryByText("正在读取文件…")).not.toBeInTheDocument();
  resolveRefresh(new Response("# 刷新后的文档"));
  expect(
    await screen.findByRole("heading", { name: "刷新后的文档" }),
  ).toBeVisible();
});

it("shows saved JSON bytes as text without reserializing numbers or whitespace", async () => {
  const source =
    '{\n\t"prompt": "原始输入",\n\t"counter": 9007199254740993\n}\n';
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(source)));
  const result = mount({
    artifact_url: url.replace("script.md", "request-01.json"),
  });
  await screen.findByText("原始输入");
  await userEvent.click(screen.getByRole("button", { name: "原文" }));
  expect(result.container.querySelector(".document-raw")?.textContent).toBe(
    source,
  );
});

it("retains raw and expanded reading state while the current document refreshes", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response("# 已打开文档"))
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (revision: number) => (
    <QueryClientProvider client={client}>
      <DocumentReader
        projectId={id}
        item={{ label: "阅读中的文件", artifact_url: url, revision }}
      />
    </QueryClientProvider>
  );
  const result = render(view(1));
  await screen.findByRole("heading", { name: "已打开文档" });
  await userEvent.click(screen.getByRole("button", { name: "原文" }));
  await userEvent.click(screen.getByRole("button", { name: "放大阅读" }));
  result.rerender(view(2));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("# 已打开文档")).toBeVisible();
  expect(screen.queryByText("正在读取文件…")).not.toBeInTheDocument();
  finish(new Response("# 文件更新完成"));
  expect(await within(dialog).findByText("# 文件更新完成")).toBeVisible();
  expect(
    within(dialog).queryByRole("heading", { name: "文件更新完成" }),
  ).not.toBeInTheDocument();
});

it("resolves Markdown images and links against the saved file directory while rejecting another project's images", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          "# 相对附件\n\n![场景参考](../frames/frame.png)\n\n[原始分镜](storyboard.json)\n\n![其他项目](../../factory-abcdef012345/frames/frame.png)",
        ),
      ),
  );
  mount({ artifact_url: url });
  await screen.findByRole("heading", { name: "相对附件" });
  expect(screen.getByRole("img", { name: "场景参考" })).toHaveAttribute(
    "src",
    window.location.origin + `/media/production/factory-${id}/frames/frame.png`,
  );
  expect(screen.queryByRole("img", { name: "其他项目" })).toBeNull();
  expect(screen.getByRole("link", { name: "原始分镜" })).toHaveAttribute(
    "href",
    window.location.origin +
      `/media/production/factory-${id}/planning/storyboard.json`,
  );
  expect(documentUrl(id, `production/factory-${id}/planning/script.md`)).toBe(
    window.location.origin + url,
  );
  expect(
    documentUrl(id, `production/factory-abcdef012345/planning/script.md`),
  ).toBe("");
});

it("reports missing preview media and permits a fresh request without losing the original download", async () => {
  const imageUrl = url.replace("planning/script.md", "frames/missing.png");
  mount({ artifact_url: imageUrl, label: "参考图片" });
  fireEvent.error(screen.getByRole("img", { name: "参考图片" }));
  expect(screen.getByRole("alert")).toHaveTextContent("媒体读取失败");
  expect(screen.getByRole("link", { name: "下载原文件" })).toHaveAttribute(
    "href",
    window.location.origin + imageUrl,
  );
  await userEvent.click(screen.getByRole("button", { name: "重新读取" }));
  expect(screen.getByRole("img", { name: "参考图片" })).toHaveAttribute(
    "src",
    window.location.origin + imageUrl + "?reader_retry=1",
  );
});
