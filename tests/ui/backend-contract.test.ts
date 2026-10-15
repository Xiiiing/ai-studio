import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { normalizeProfile } from "../../ui/factory/src/Models";

const root = process.cwd();
const localPython = path.join(root, ".venv/bin/python");
const python =
  process.env.FACTORY_TEST_PYTHON ||
  (existsSync(localPython) ? localPython : "python3");

function validateWithBackend(profile: Record<string, unknown>, video = false) {
  const source = video
    ? "from factory.video_providers import normalize_video_profile as normalize"
    : "from factory.providers import normalize_profile as normalize";
  const result = spawnSync(
    python,
    [
      "-c",
      `import json, sys\n${source}\nprint(json.dumps(normalize(json.load(sys.stdin))))`,
    ],
    {
      cwd: root,
      input: JSON.stringify(profile),
      encoding: "utf8",
      timeout: 10000,
    },
  );
  expect(result.error, result.stderr).toBeUndefined();
  expect(result.status, result.stderr).toBe(0);
  return JSON.parse(result.stdout);
}

function form(provider: string, extra: Record<string, unknown> = {}) {
  const remote = ["dashscope", "deepseek"].includes(provider);
  const endpoint =
    provider === "dashscope"
      ? "https://dashscope.aliyuncs.com/compatible-mode/v1"
      : provider === "deepseek"
        ? "https://api.deepseek.com"
        : provider === "ollama_native"
          ? "http://127.0.0.1:11434"
          : "http://127.0.0.1:8090/v1";
  return {
    provider,
    model: "fixture-model",
    enabled: "on",
    base_url: endpoint,
    network_mode: remote ? "remote" : "loopback",
    allowed_hosts: remote ? new URL(endpoint).hostname : "",
    api_key_env: remote ? "FIXTURE_MODEL_KEY" : "",
    context_tokens: "32768",
    vision: "on",
    request_timeout_seconds: "120",
    response_format: "json_object",
    model_discovery: provider === "dashscope" ? "skip" : "required",
    context_validation: "",
    ...extra,
  };
}

describe("model editor payloads accepted by the real Python validators", () => {
  it.each([
    "llama_cpp",
    "ollama",
    "ollama_native",
    "vllm",
    "lm_studio",
    "deepseek",
    "dashscope",
    "openai_compatible",
  ])("can save a new %s connection without network access", (provider) => {
    const result = validateWithBackend(normalizeProfile(form(provider)));
    expect(result.provider).toBe(provider);
    expect(result.context_validation).toBe(
      provider === "llama_cpp" ? "llama_tokenize" : "conservative_estimate",
    );
  });

  it.each(["ollama_native", "dashscope"])(
    "cleans incompatible template fields when switching to %s",
    (provider) => {
      const previous = {
        provider,
        chat_template_kwargs: { enable_thinking: false },
        context_validation: "llama_tokenize",
      };
      const profile = normalizeProfile(form(provider), previous);
      expect(profile.chat_template_kwargs).toBeUndefined();
      expect(validateWithBackend(profile).provider).toBe(provider);
    },
  );

  it("preserves compatible advanced settings while editing a connection", () => {
    const previous = {
      provider: "openai_compatible",
      top_p: 0.82,
      model_aliases: ["fixture-model-alias"],
      context_validation: "conservative_estimate",
      chat_template_kwargs: { custom: true },
    };
    const result = validateWithBackend(
      normalizeProfile(
        form("openai_compatible", {
          context_validation: "conservative_estimate",
          response_format: "prompt",
          temperature_override: "0.25",
        }),
        previous,
      ),
    );
    expect(result.response_format).toBe("prompt");
    expect(result.temperature_override).toBe(0.25);
    expect(result.top_p).toBe(0.82);
    expect(result.model_aliases).toEqual(["fixture-model-alias"]);
    expect(result.chat_template_kwargs).toEqual({ custom: true });
  });

  it("saves the managed video adapter using its exact model contract", () => {
    const profile = normalizeProfile(
      { provider: "comfyui_wan", model: "Wan2.2-TI2V-5B", enabled: "on" },
      {},
      true,
    );
    expect(validateWithBackend(profile, true).provider).toBe("comfyui_wan");
  });

  it("saves cloud video price as exact micros and keeps download policy", () => {
    const profile = normalizeProfile(
      {
        provider: "minimax",
        model: "MiniMax-Hailuo-2.3",
        enabled: "on",
        base_url: "https://api.minimax.io/v1",
        api_key_env: "FIXTURE_MINIMAX_KEY",
        allowed_hosts: "api.minimax.io",
        download_hosts: "cdn.minimax.io",
        duration: "6",
        resolution: "768P",
        poll_interval_seconds: "5",
        request_timeout_seconds: "60",
        max_wait_seconds: "1800",
        unit_cost: "0.125001",
        currency: "USD",
        price_source: "fixture price",
      },
      {},
      true,
    );
    const result = validateWithBackend(profile, true);
    expect(result.pricing.unit_cost_micros).toBe(125001);
    expect(result.pricing.currency).toBe("USD");
    expect(result.download_hosts).toEqual(["cdn.minimax.io"]);
  });
});
