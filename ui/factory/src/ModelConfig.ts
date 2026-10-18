import { endpointValues } from "./ConnectionFields";
import { rows, parseMoney, type RecordData } from "./api";
const split = (v: any) =>
  String(v || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
const h3Defaults = {
  preview: {
    steps: 4,
    shift_video: 12,
    shift_audio: 3,
    sampler: "euler",
    revision: "wkp-dualgpu-turbo4-v1",
  },
  production: {
    steps: 20,
    shift_video: 12,
    shift_audio: 3,
    sampler: "euler",
    revision: "wkp-dualgpu-base-v1",
  },
};
export function normalizeProfile(
  values: RecordData,
  previous: RecordData = {},
  video = false,
) {
  const provider = values.provider;
  const out: RecordData = {
    ...(previous.provider === provider ? previous : {}),
    provider,
    model: values.model.trim(),
    enabled: values.enabled === "on",
  };
  delete out.discovered_models;
  delete out.pending_key;
  delete out.id;
  if (video) {
    if (provider === "comfyui_h3" && values.quality) {
      const quality = values.quality as keyof typeof h3Defaults;
      const defaults = h3Defaults[quality];
      if (!defaults) throw new Error("请选择有效的 H3 质量档。");
      // Keeping an unchanged legacy preview profile preserves its cache identity.
      if (
        previous.quality ||
        quality !== "preview" ||
        previous.provider !== provider
      ) {
        Object.assign(out, defaults, {
          quality,
          steps: quality === "preview" ? 4 : Number(values.steps),
          shift_video: quality === "preview" ? 12 : Number(values.shift_video),
          shift_audio: quality === "preview" ? 3 : Number(values.shift_audio),
          sampler: quality === "preview" ? "euler" : values.sampler,
        });
      }
    }
    if (provider === "minimax")
      Object.assign(out, {
        network_mode: "remote",
        base_url: values.base_url.trim(),
        api_key_env: values.api_key_env.trim(),
        allowed_hosts: split(values.allowed_hosts),
        download_hosts: split(values.download_hosts),
        duration: Number(values.duration),
        resolution: values.resolution,
        poll_interval_seconds: Number(values.poll_interval_seconds),
        request_timeout_seconds: Number(values.request_timeout_seconds),
        max_wait_seconds: Number(values.max_wait_seconds),
        pricing: {
          unit_cost_micros: values.unit_cost?.trim()
            ? parseMoney(values.unit_cost)
            : null,
          currency: values.currency || "CNY",
          source: values.price_source || "",
        },
      });
    return out;
  }
  Object.assign(out, {
    ...endpointValues(values, previous),
    api_key_env: values.api_key_env?.trim() || "",
    context_tokens: Number(values.context_tokens),
    vision: values.vision === "on",
    request_timeout_seconds: Number(values.request_timeout_seconds),
    response_format: values.response_format,
    model_discovery: values.model_discovery,
    context_validation:
      values.context_validation ||
      (provider === "llama_cpp" ? "llama_tokenize" : "conservative_estimate"),
  });
  try {
    if (new URL(previous.base_url).origin !== new URL(out.base_url).origin)
      for (const key of [
        "tls_ca_file_env",
        "tls_client_cert_env",
        "tls_client_key_env",
      ])
        delete out[key];
  } catch {
    for (const key of [
      "tls_ca_file_env",
      "tls_client_cert_env",
      "tls_client_key_env",
    ])
      delete out[key];
  }
  if (values.thinking === "true" || values.thinking === "false")
    out.thinking = values.thinking === "true";
  else delete out.thinking;
  if (String(values.temperature_override ?? "").trim())
    out.temperature_override = Number(values.temperature_override);
  else delete out.temperature_override;
  if (out.context_validation === "llama_tokenize" && provider !== "llama_cpp")
    out.context_validation = "conservative_estimate";
  if (["ollama_native", "dashscope"].includes(provider))
    delete out.chat_template_kwargs;
  if (!["ollama_native", "dashscope"].includes(provider)) delete out.thinking;
  return out;
}
export function makeDraft(data: RecordData): RecordData {
  return {
    profiles: structuredClone(data.editable_profiles || {}),
    ...(data.image_profiles
      ? {
          image_profiles: structuredClone(data.image_profiles),
          image_roles: { ...data.image_roles },
        }
      : {}),
    ...(data.audio_profiles
      ? {
          audio_profiles: structuredClone(data.audio_profiles),
          audio_roles: { ...data.audio_roles },
        }
      : {}),
    roles: {
      ...Object.fromEntries(
        rows(data.roles).map((r) => [r.role, r.profile || ""]),
      ),
      ...data.role_assignments,
      ...(rows(data.roles).some((r) => r.role === "executive_producer") &&
      !data.role_assignments?.executive_producer
        ? { executive_producer: "" }
        : {}),
    },
    ...(data.video_profiles
      ? {
          video_profiles: structuredClone(data.video_profiles),
          video_roles: { ...data.video_roles },
        }
      : {}),
  };
}

export type ModelKind = "llm" | "image" | "audio" | "video";
export type Service = {
  id: string;
  kind: ModelKind;
  provider: string;
  profiles: [string, RecordData][];
};
export const kindLabels = {
  llm: "文本与视觉",
  image: "图像",
  audio: "配音",
  video: "视频",
};
export const mediaRoles = {
  image: "image_generator",
  audio: "speech_generator",
  video: "video_generator",
};
export function profileField(kind: ModelKind) {
  return kind === "llm" ? "profiles" : kind + "_profiles";
}
const connectionFields = [
  "provider",
  "base_url",
  "api_key_env",
  "api_key_credential",
  "tls_ca_file_env",
  "tls_client_cert_env",
  "tls_client_key_env",
  "network_mode",
  "allowed_hosts",
];
export function services(draft: RecordData): Service[] {
  const result: Service[] = [];
  for (const kind of ["llm", "image", "video", "audio"] as ModelKind[]) {
    const groups = new Map<string, Service>();
    for (const [id, profile] of Object.entries(
      draft[profileField(kind)] || {},
    ) as [string, RecordData][]) {
      // Media workflows carry their own lifecycle/parameters; never coalesce them.
      const key =
        kind === "llm"
          ? JSON.stringify(
              connectionFields.map((k) =>
                k === "allowed_hosts"
                  ? [...(profile[k] || [])].sort()
                  : (profile[k] ?? null),
              ),
            )
          : id;
      let group = groups.get(key);
      if (!group) {
        group = {
          id: kind + ":" + id,
          kind,
          provider: profile.provider,
          profiles: [],
        };
        groups.set(key, group);
        result.push(group);
      }
      group.profiles.push([id, profile]);
    }
  }
  return result;
}
export function providerLabel(inventory: RecordData, provider: string) {
  return (
    rows(inventory.providers).find((p) => p.id === provider)?.label ||
    rows(inventory.video_providers).find((p) => p.id === provider)?.label ||
    inventory.media_providers?.[provider]?.label ||
    provider
  );
}
export function serviceHost(profile: RecordData) {
  try {
    const url = new URL(profile.base_url);
    return url.host + url.pathname.replace(/\/$/, "");
  } catch {
    return "现有制作环境";
  }
}
export function newProfileId(profiles: RecordData, prefix = "model") {
  let id;
  do {
    id = prefix + "-" + Math.random().toString(36).slice(2, 10);
  } while (Object.hasOwn(profiles, id));
  return id;
}
export function roleAssignments(draft: RecordData, kind: ModelKind) {
  return kind === "llm" ? draft.roles : draft[kind + "_roles"] || {};
}
export function modelUses(draft: RecordData, kind: ModelKind, id: string) {
  return Object.entries(roleAssignments(draft, kind))
    .filter(([, value]) => value === id)
    .map(([role]) => role);
}
