import { useState } from "react";
import { Network, Eye, EyeOff } from "lucide-react";
import { Field } from "./ui";
import type { RecordData } from "./api";

export const builtinProviders = new Set([
  "openai",
  "deepseek",
  "dashscope",
  "zhipu",
  "moonshot",
  "minimax_text",
  "siliconflow",
  "anthropic",
  "gemini",
]);
export const providerLinks: Record<
  string,
  { website: string; docs: string; keys: string }
> = {
  openai: {
    website: "https://openai.com",
    docs: "https://developers.openai.com/api/reference/overview",
    keys: "https://platform.openai.com/api-keys",
  },
  deepseek: {
    website: "https://www.deepseek.com",
    docs: "https://api-docs.deepseek.com",
    keys: "https://platform.deepseek.com/api_keys",
  },
  dashscope: {
    website: "https://qwen.ai",
    docs: "https://help.aliyun.com/zh/model-studio/",
    keys: "https://bailian.console.aliyun.com/",
  },
  zhipu: {
    website: "https://www.zhipuai.cn",
    docs: "https://docs.bigmodel.cn/",
    keys: "https://open.bigmodel.cn/",
  },
  moonshot: {
    website: "https://www.kimi.com",
    docs: "https://platform.moonshot.cn/docs",
    keys: "https://platform.moonshot.cn/console/api-keys",
  },
  minimax_text: {
    website: "https://www.minimax.io",
    docs: "https://platform.minimax.io/docs/api-reference/api-overview",
    keys: "https://platform.minimax.io/",
  },
  siliconflow: {
    website: "https://siliconflow.cn",
    docs: "https://docs.siliconflow.cn/",
    keys: "https://cloud.siliconflow.cn/",
  },
  anthropic: {
    website: "https://www.anthropic.com",
    docs: "https://platform.claude.com/docs/en/api/overview",
    keys: "https://platform.claude.com/settings/keys",
  },
  gemini: {
    website: "https://gemini.google.com",
    docs: "https://ai.google.dev/gemini-api/docs",
    keys: "https://aistudio.google.com/apikey",
  },
};
export function protocolName(provider: string) {
  if (provider === "anthropic") return "Anthropic · Messages";
  if (provider === "gemini") return "Gemini · generateContent";
  if (provider === "ollama_native") return "Ollama · 原生 API";
  if (provider?.startsWith("comfyui")) return "ComfyUI 工作流";
  if (provider === "minimax") return "MiniMax 视频 API";
  return "OpenAI 兼容 · Chat Completions";
}
export function endpointValues(values: RecordData, previous: RecordData = {}) {
  const url = new URL(String(values.base_url).trim());
  if (url.username || url.password || url.search || url.hash)
    throw new Error("URL 请只填写服务地址；Key 填在单独输入框。");
  let path = url.pathname.replace(/\/+$/, "");
  const provider = String(values.provider);
  if (provider === "ollama_native")
    path = path.replace(/\/api\/(chat|tags)$/, "");
  else if (provider === "gemini")
    path = path.replace(/\/models(?:\/[^/]+:generateContent)?$/, "");
  else if (provider === "anthropic") path = path.replace(/\/messages$/, "");
  else path = path.replace(/\/chat\/completions$/, "");
  if (
    !path &&
    !builtinProviders.has(provider) &&
    !provider.startsWith("comfyui")
  )
    path = provider === "ollama_native" ? "" : "/v1";
  const host = url.hostname.replace(/^\[|\]$/g, "");
  const local = ["localhost", "127.0.0.1", "::1"].includes(host);
  const lan =
    /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|f[cd][0-9a-f]{2}:)/i.test(
      host,
    ) ||
    host.endsWith(".local") ||
    (!host.includes(".") && !host.includes(":"));
  let mode = values.network_mode || previous.network_mode;
  if (!mode || mode === "auto")
    mode = local ? "loopback" : lan ? "lan" : "remote";
  const allowed = Array.isArray(values.allowed_hosts)
    ? values.allowed_hosts
    : String(values.allowed_hosts || "")
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean);
  return {
    base_url: url.origin + path,
    network_mode: mode,
    allowed_hosts: mode === "loopback" ? [] : [...new Set([...allowed, host])],
  };
}
export function CredentialField({
  saved = false,
  value = "",
  label = "API Key",
}: {
  saved?: boolean;
  value?: string;
  label?: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <Field
      label={label}
      hint={
        saved
          ? "已关联服务端凭据。留空保留关联；填写新 Key 可替换。"
          : "直接粘贴 Key；本地无鉴权服务可以留空。"
      }
    >
      <div className="credential-input">
        <input
          name="api_key"
          aria-label={label}
          type={visible ? "text" : "password"}
          autoComplete="new-password"
          defaultValue={value}
          placeholder={saved ? "已关联凭据 · 留空保留" : "粘贴 API Key"}
        />
        <button
          type="button"
          className="button quiet"
          aria-label={visible ? "隐藏 API Key" : "显示 API Key"}
          onClick={() => setVisible(!visible)}
        >
          {visible ? <EyeOff size={17} /> : <Eye size={17} />}
        </button>
      </div>
    </Field>
  );
}
export function ProviderSymbol({ provider }: { provider: string }) {
  const assets = import.meta.glob("./assets/providers/*.{png,svg}", {
    eager: true,
    query: "?url",
    import: "default",
  }) as Record<string, string>;
  const name =
    provider === "minimax_text"
      ? "minimax"
      : provider === "comfyui_h3" ||
          provider === "comfyui_external" ||
          provider === "comfyui_local"
        ? "comfyui"
        : provider === "ollama_native"
          ? "ollama"
          : provider === "dashscope_tts" || provider === "dashscope_image"
            ? "dashscope"
            : provider;
  const source =
    assets[`./assets/providers/${name}.png`] ||
    assets[`./assets/providers/${name}.svg`];
  return (
    <span className="model-symbol">
      {source ? (
        <img src={source} alt="" width="28" height="28" />
      ) : (
        <Network size={22} />
      )}
    </span>
  );
}
