import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DocumentReader, MaterialCard, documentUrl, readDocument, MAX_PREVIEW_BYTES } from "../../ui/factory/src/DocumentReader";
const id = "012345abcdef", url = `/media/production/factory-${id}/planning/script.md`;
function mount(item: any, material = false) {
  return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})}>{material ? <MaterialCard item={item} projectId={id} /> : <DocumentReader item={item} projectId={id} />}</QueryClientProvider>);
}
describe("project document reading", () => {
  it("loads file-only inputs on demand and renders Markdown tables, lists and code", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("# 故事目标\n\n**人物动机**\n\n- 履行承诺\n\n|镜头|动作|\n|---|---|\n|S01|开门|\n\n```json\n{\"ok\":true}\n```"));
    vi.stubGlobal("fetch", fetcher);
    mount({label:"输入故事",artifact_url:url}, true);
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.queryByText("尚未生成")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button",{name:"阅读文件"}));
    expect(await screen.findByRole("heading",{name:"故事目标"})).toBeVisible();
    expect(screen.getByRole("table")).toHaveTextContent("S01");
    expect(screen.getByRole("list")).toHaveTextContent("履行承诺");
    await userEvent.click(screen.getByRole("button",{name:"原文"}));
    expect(screen.getByText(/# 故事目标/)).toBeVisible();
    await userEvent.click(screen.getByRole("button",{name:"放大阅读"}));
    expect(within(screen.getByRole("dialog")).getByText(/# 故事目标/)).toBeVisible();
    expect(screen.getAllByRole("link",{name:"下载原文件"})[0]).toHaveAttribute("href",expect.stringContaining(url));
  });
  it("retains prompts and messages in JSON execution originals", async () => {
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response(JSON.stringify({prompt:"完整系统提示",messages:[{role:"user",content:"完整输入"}]}))));
    mount({label:"请求原文",artifact_url:url.replace("script.md","request-01.json")});
    expect(await screen.findByText("完整系统提示")).toBeVisible();
    expect(screen.getByText(/完整输入/)).toBeVisible();
    await userEvent.click(screen.getByRole("button",{name:"原文"}));
    expect(screen.getByText(/"prompt": "完整系统提示"/)).toBeVisible();
  });
  it("distinguishes missing files and retries without losing the file link", async () => {
    vi.stubGlobal("fetch",vi.fn().mockResolvedValueOnce(new Response("",{status:404})).mockResolvedValueOnce(new Response("# 已恢复")));
    mount({artifact_url:url});
    expect(await screen.findByRole("alert")).toHaveTextContent("文件缺失");
    await userEvent.click(screen.getByRole("button",{name:"重新读取"}));
    expect(await screen.findByRole("heading",{name:"已恢复"})).toBeVisible();
  });
  it("limits streamed bytes even without Content-Length", async () => {
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response(new Uint8Array(MAX_PREVIEW_BYTES+1))));
    await expect(readDocument(url)).rejects.toThrow("2 MiB");
  });
  it("keeps oversized files downloadable and rejects their preview", async () => {
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response("unused",{headers:{"Content-Length":String(MAX_PREVIEW_BYTES+1)}})));
    mount({artifact_url:url});
    expect(await screen.findByRole("alert")).toHaveTextContent("2 MiB");
    expect(screen.getByRole("link",{name:"下载原文件"})).toBeVisible();
    expect(screen.queryByRole("button",{name:"重新读取"})).not.toBeInTheDocument();
  });
  it("skips HTML and prevents unowned image and document requests", async () => {
    vi.stubGlobal("fetch",vi.fn().mockResolvedValue(new Response('# 文档\n<script>alert(1)</script>\n\n![外部图片](https://other.invalid/image.png)\n\n[危险](javascript:alert(1))')));
    mount({artifact_url:url});
    await screen.findByRole("heading",{name:"文档"});
    expect(document.querySelector("script")).toBeNull();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(documentUrl(id,"https://other.invalid"+url)).toBe("");
    expect(documentUrl(id,url.replace(id,"abcdef012345"))).toBe("");
    expect(documentUrl(id,url+"?secret=1")).toBe("");
    await waitFor(()=>expect(fetch).toHaveBeenCalledOnce());
  });
});
