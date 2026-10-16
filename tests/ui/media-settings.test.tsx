import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { MediaSettings } from "../../ui/factory/src/MediaSettings";
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
