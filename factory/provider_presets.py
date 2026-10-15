"""Public connection defaults. Keys belong only in the operator's environment."""
from __future__ import annotations

from urllib.parse import urlsplit

# Conservative, editable defaults; availability is determined by the user's account.
CLOUD = {
    "openai": ("OpenAI", "https://api.openai.com/v1", "gpt-5.5", "OPENAI_API_KEY", True, "openai"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com", "deepseek-chat", "DEEPSEEK_API_KEY", False, "openai"),
    "dashscope": ("千问 / 百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3-vl-plus", "DASHSCOPE_API_KEY", True, "openai"),
    "zhipu": ("智谱", "https://open.bigmodel.cn/api/paas/v4", "glm-4.6v", "ZHIPU_API_KEY", True, "openai"),
    "moonshot": ("Moonshot / Kimi", "https://api.moonshot.cn/v1", "kimi-k2.5", "MOONSHOT_API_KEY", True, "openai"),
    "minimax_text": ("MiniMax 文本", "https://api.minimax.io/v1", "MiniMax-M2.5", "MINIMAX_API_KEY", False, "openai"),
    "siliconflow": ("硅基流动", "https://api.siliconflow.cn/v1", "Qwen/Qwen3-VL-32B-Instruct", "SILICONFLOW_API_KEY", True, "openai"),
    "anthropic": ("Claude", "https://api.anthropic.com/v1", "claude-sonnet-4-6", "ANTHROPIC_API_KEY", True, "anthropic"),
    "gemini": ("Gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash", "GEMINI_API_KEY", True, "gemini"),
}


def cloud_providers():
    return {ident: {"label": label, "protocol": protocol, "base_url_example": base,
                    "response_format": "prompt" if protocol != "openai" else "json_object",
                    "context_validation": "conservative_estimate", "model_discovery": "skip"}
            for ident, (label, base, model, env, vision, protocol) in CLOUD.items()}


def presets(providers):
    result = {}
    for ident, properties in providers.items():
        base = properties["base_url_example"]
        value = {"provider": ident, "base_url": base, "enabled": True,
                 "network_mode": "loopback", "allowed_hosts": [], "model": "",
                 "context_tokens": 32768, "vision": False,
                 "response_format": properties["response_format"],
                 "context_validation": properties["context_validation"],
                 "model_discovery": properties.get("model_discovery", "required")}
        if ident in CLOUD:
            label, base, model, env, vision, protocol = CLOUD[ident]
            value.update(model=model, api_key_env=env, vision=vision, network_mode="remote",
                         allowed_hosts=[urlsplit(base).hostname], context_tokens=65536)
        if ident == "llama_cpp":
            value["chat_template_kwargs"] = {"enable_thinking": False}
        if ident == "dashscope":
            value["thinking"] = False
        result[ident] = value
    return result
