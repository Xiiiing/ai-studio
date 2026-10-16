import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
  within,
} from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { MediaSettings } from "../../ui/factory/src/MediaSettings";

const mediaDraft = {
  image_profiles: {
    image: {
      provider: "comfyui_external",
      model: "image-model",
      enabled: true,
    },
  },
  image_roles: { image_generator: "image" },
};
const mediaInventory = {
  ...mediaDraft,
  media_presets: {
    image: { comfyui_external: mediaDraft.image_profiles.image },
  },
  media_providers: { comfyui_external: { label: "ComfyUI" } },
};
it("检测有进行中反馈且不能重复提交，结果留在对应连接卡片", async () => {
  let finish!: (response: Response) => void;
  const fetch = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MediaSettings
      draft={mediaDraft}
      inventory={mediaInventory}
      onChange={vi.fn()}
      notify={vi.fn()}
    />,
  );
  const card = screen.getByText("image").closest("article")!;
  const button = within(card).getByRole("button", { name: "检查图像配置" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(fetch).toHaveBeenCalledOnce();
  expect(within(card).getByRole("button", { name: "检测中…" })).toBeDisabled();
  await act(async () =>
    finish(
      new Response(JSON.stringify({ error: "制作连接无法访问" }), {
        status: 500,
      }),
    ),
  );
  expect(within(card).getByRole("status")).toHaveTextContent(
    "制作连接无法访问",
  );
  expect(
    within(card).getByRole("button", { name: "检查图像配置" }),
  ).toBeEnabled();
});

it("修改后忽略过时检测，保存中全部媒体编辑入口不可用", async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const props = {
    draft: mediaDraft,
    inventory: mediaInventory,
    onChange: vi.fn(),
    notify: vi.fn(),
  };
  const view = render(<MediaSettings {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "检查图像配置" }));
  view.rerender(<MediaSettings {...props} dirty generation={1} />);
  await act(async () =>
    finish(
      new Response(
        JSON.stringify({ status: "ready", message: "旧的媒体结果" }),
      ),
    ),
  );
  expect(screen.queryByText("旧的媒体结果")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "检查图像配置" })).toBeDisabled();
  view.rerender(<MediaSettings {...props} saving />);
  expect(screen.getByRole("button", { name: "添加图像连接" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "编辑图像连接" })).toBeDisabled();
  expect(screen.getByLabelText("图像使用的连接")).toBeDisabled();
  expect(screen.getByLabelText("工作流模板 JSON")).toBeDisabled();
});

it("停用的制作连接不能检测", () => {
  const draft = {
    ...mediaDraft,
    image_profiles: {
      image: { ...mediaDraft.image_profiles.image, enabled: false },
    },
  };
  render(
    <MediaSettings
      draft={draft}
      inventory={{ ...mediaInventory, ...draft }}
      onChange={vi.fn()}
      notify={vi.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: "检查图像配置" })).toBeDisabled();
  expect(screen.getByText("已停用")).toBeVisible();
});
it("adds a user ComfyUI connection and imports a mapped workflow without generation", async () => {
  const onChange = vi.fn();
  const fetch = vi.fn(
    async (_path: unknown, _init?: RequestInit) =>
      new Response(JSON.stringify({ id: "a".repeat(64) })),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <MediaSettings
      draft={{ image_profiles: {}, image_roles: { image_generator: "" } }}
      inventory={{
        media_presets: {
          image: {
            comfyui_external: {
              provider: "comfyui_external",
              model: "custom",
              enabled: true,
              base_url: "http://127.0.0.1:8188",
              network_mode: "loopback",
              allowed_hosts: [],
            },
          },
        },
        media_providers: { comfyui_external: { label: "自己的ComfyUI" } },
      }}
      onChange={onChange}
      notify={() => {}}
    />,
  );
  fireEvent.click(screen.getByText("添加图像连接"));
  fireEvent.change(screen.getByLabelText("制作连接编号"), {
    target: { value: "mine" },
  });
  fireEvent.click(screen.getByText("加入制作配置草稿"));
  expect(onChange).toHaveBeenCalledTimes(1);
  const d = onChange.mock.calls[0][0]({ image_profiles: {} });
  expect(d.image_profiles.mine.base_url).toBe("http://127.0.0.1:8188");
  fireEvent.click(screen.getByText("高级：ComfyUI 工作流导入／导出"));
  fireEvent.change(screen.getByLabelText("工作流模板 JSON"), {
    target: { value: '{"name":"test"}' },
  });
  fireEvent.click(screen.getByText("导入模板"));
  await waitFor(() =>
    expect(screen.getByLabelText("模板编号")).toHaveValue("a".repeat(64)),
  );
  expect(fetch.mock.calls[0][0]).toContain("/workflow-templates");
});
