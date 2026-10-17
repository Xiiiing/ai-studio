"""Video connection registry, independent of text model brands and roles."""
from __future__ import annotations
from .connection_secrets import secret_value

import copy
import os

from .providers import (ENV_RE, ID_RE, _canonical_host, _number, _reject_secret_fields,
                        _valid_text, redact_secrets, validate_endpoint)

VIDEO_PROVIDERS = {
    "comfyui_h3": {"label": "ComfyUI / MiniMax H3", "protocol": "comfyui",
                   "location": "local", "model_example": "MiniMax-H3"},
    "comfyui_wan": {"label": "ComfyUI / Wan", "protocol": "comfyui",
                    "location": "local", "model_example": "Wan2.2-TI2V-5B"},
    "minimax": {"label": "MiniMax 云端视频 API", "protocol": "minimax_video",
                "location": "remote", "base_url_example": "https://api.minimax.io/v1",
                "model_example": "MiniMax-Hailuo-2.3"},
}
VIDEO_ROLES = {"video_generator": "视频生成"}
LOCAL_PROFILE = {"provider": "comfyui_wan", "enabled": True, "model": "Wan2.2-TI2V-5B"}
H3_PROFILE = {"provider": "comfyui_h3", "enabled": True, "model": "MiniMax-H3",
              "revision": "wkp-dualgpu-turbo4-v1"}
H3_PRODUCTION_PROFILE = {**H3_PROFILE, "revision": "wkp-dualgpu-base-v1", "quality": "production",
                         "steps": 20, "shift_video": 12.0, "shift_audio": 3.0, "sampler": "euler"}
_COMMON = {"provider", "enabled", "model", "revision"}
_H3 = {"quality", "steps", "shift_video", "shift_audio", "sampler"}
_REMOTE = {"base_url", "network_mode", "allowed_hosts", "api_key_env", "download_hosts",
           "duration", "resolution", "prompt_optimizer", "request_timeout_seconds",
           "poll_interval_seconds", "max_wait_seconds", "max_download_bytes", "pricing"}


def normalize_video_profile(value):
    if not isinstance(value, dict):
        raise ValueError("Video profile must be an object")
    _reject_secret_fields(value)
    profile = copy.deepcopy(value)
    provider = profile.get("provider")
    if provider in ("comfyui_local", "comfyui_external"):
        from .media_profiles import normalize_media
        return normalize_media(profile, "video")
    if provider not in VIDEO_PROVIDERS:
        raise ValueError("Unsupported video provider")
    allowed = _COMMON | (_REMOTE if provider == "minimax" else _H3 if provider == "comfyui_h3" else set())
    if set(profile) - allowed:
        raise ValueError("Unknown video profile fields: " + ", ".join(sorted(set(profile) - allowed)))
    profile.setdefault("enabled", False if provider == "minimax" else True)
    if type(profile["enabled"]) is not bool:
        raise ValueError("Video enabled must be a boolean")
    profile["model"] = _valid_text(profile.get("model"), "video model", 128).strip()
    if "revision" in profile:
        _valid_text(profile["revision"], "video revision", 128)
    if provider in {"comfyui_wan", "comfyui_h3"}:
        expected = H3_PROFILE if provider == "comfyui_h3" else LOCAL_PROFILE
        if profile["model"] != expected["model"]:
            raise ValueError("The managed ComfyUI adapter requires " + expected["model"])
        if provider == "comfyui_h3":
            from studio.h3 import recipe
            selected = recipe(profile)
            # Historical snapshots retain their exact identity and turbo4 defaults.
            if set(profile) & _H3 or selected["quality"] == "production":
                profile.update(selected)
            else:
                profile.setdefault("revision", selected["revision"])
        return profile
    if "pricing" in profile:
        from .cloud_budget import pricing
        quote = pricing(profile["pricing"])
        profile["pricing"] = {key: quote[key] for key in ("unit_cost_micros", "currency", "source")}
    profile.setdefault("network_mode", "remote")
    profile.setdefault("duration", 6)
    profile.setdefault("resolution", "768P")
    profile.setdefault("prompt_optimizer", False)
    profile.setdefault("request_timeout_seconds", 60)
    profile.setdefault("poll_interval_seconds", 5)
    profile.setdefault("max_wait_seconds", 1800)
    profile.setdefault("max_download_bytes", 256 * 1024 * 1024)
    profile.setdefault("download_hosts", [])
    profile["base_url"] = validate_endpoint(profile)
    env = profile.get("api_key_env")
    if not isinstance(env, str) or not ENV_RE.fullmatch(env):
        raise ValueError("MiniMax requires an api_key_env name, not an inline API key")
    if type(profile["duration"]) is not int or profile["duration"] not in (6, 10):
        raise ValueError("MiniMax video duration must be 6 or 10 seconds")
    if profile["resolution"] not in ("512P", "768P", "1080P"):
        raise ValueError("Unsupported MiniMax video resolution")
    if profile["model"].lower().startswith("minimax-hailuo-2.3") and profile["resolution"] == "512P":
        raise ValueError("Hailuo 2.3 supports 768P or 1080P, not 512P")
    if profile["resolution"] == "1080P" and profile["duration"] != 6:
        raise ValueError("This MiniMax adapter requires 6s at 1080P; use 768P for 10s")
    if type(profile["prompt_optimizer"]) is not bool:
        raise ValueError("prompt_optimizer must be a boolean")
    _number(profile["request_timeout_seconds"], "video request timeout", 1, 300)
    _number(profile["poll_interval_seconds"], "video poll interval", 1, 60)
    _number(profile["max_wait_seconds"], "video wait limit", 1, 7200)
    _number(profile["max_download_bytes"], "video download limit", 1024, 512 * 1024 * 1024, integer=True)
    hosts = profile["download_hosts"]
    if not isinstance(hosts, list) or len(hosts) > 32:
        raise ValueError("download_hosts must be a list of at most 32 exact hosts")
    profile["download_hosts"] = [_canonical_host(host) for host in hosts]
    return profile


def video_registry(config, *, require_enabled=True):
    profiles = config.get("video_profiles", {"local_wan": LOCAL_PROFILE})
    roles = config.get("video_roles", {"video_generator": "local_wan"})
    if not isinstance(profiles, dict) or not 1 <= len(profiles) <= 32:
        raise ValueError("Configure between one and 32 video profiles")
    result = {}
    for name, profile in profiles.items():
        if not isinstance(name, str) or not ID_RE.fullmatch(name):
            raise ValueError("Invalid video profile identifier")
        result[name] = normalize_video_profile(profile)
    if not isinstance(roles, dict) or set(roles) != set(VIDEO_ROLES):
        raise ValueError("Video roles must assign video_generator")
    for role, name in roles.items():
        if not isinstance(name, str) or name not in result:
            raise ValueError("Unknown video profile for " + role)
        if require_enabled and not result[name]["enabled"]:
            raise ValueError("The assigned video profile must be enabled")
    return result, copy.deepcopy(roles)


def validate_video_registry(profiles, roles, config):
    candidate = copy.deepcopy(config)
    candidate.update(video_profiles=profiles, video_roles=roles)
    normalized, assignments = video_registry(candidate)
    candidate.update(video_profiles=normalized, video_roles=assignments)
    return candidate


def resolve_video(config, *, require_enabled=True):
    profiles, roles = video_registry(config, require_enabled=require_enabled)
    ident = roles["video_generator"]
    return {"profile": ident, **copy.deepcopy(profiles[ident])}


def video_presets():
    """Editable defaults shared by the UI and configuration examples."""
    properties=VIDEO_PROVIDERS["minimax"]
    return {"comfyui_h3": copy.deepcopy(H3_PRODUCTION_PROFILE), "comfyui_wan":copy.deepcopy(LOCAL_PROFILE),
            "minimax":normalize_video_profile({"provider":"minimax","enabled":True,
                "model":properties["model_example"],"base_url":properties["base_url_example"],
                "network_mode":"remote","allowed_hosts":["api.minimax.io"],"api_key_env":"MINIMAX_API_KEY"})}


def public_video_inventory(config):
    profiles, roles = video_registry(config, require_enabled=False)
    return {
        "video_presets": video_presets(),
        "video_profiles": {name: redact_secrets(value, value) for name, value in profiles.items()},
        "video_roles": roles,
        "video_providers": [{"id": name, **value} for name, value in VIDEO_PROVIDERS.items()],
        "video_role_options": [{"id": role, "role": role, "label": label}
                               for role, label in VIDEO_ROLES.items()],
    }


def probe_video_profile(profile_id, config):
    """Configuration preflight only. No network, generation, or paid API calls."""
    profiles, _ = video_registry(config, require_enabled=False)
    if not isinstance(profile_id, str) or profile_id not in profiles:
        raise ValueError("Unknown video profile")
    profile = profiles[profile_id]
    authenticated = profile["provider"] != "minimax" or bool(secret_value(profile["api_key_env"]))
    if profile["provider"] in ("comfyui_local", "comfyui_external"):
        from .media_profiles import probe
        return {"profile": profile_id, **probe(profile, "video")}
    result = {"profile": profile_id, "kind": "video", "generation": False,
              "network_checked": False, "configured_model": profile["model"],
              "model_match": False, "models": [], "auth_configured": authenticated,
              "status": "configured" if authenticated else "error",
              "message": "配置预检完成；尚未验证在线状态、模型可用性或生成质量。"}
    if not profile["enabled"]:
        result["status"] = "disabled"
    elif not authenticated:
        result["error"] = {"code": "missing_auth", "message": "视频服务缺少可用 API Key"}
    return result
