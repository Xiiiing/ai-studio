"""Configuration snapshots and role identities for the workflow."""
from __future__ import annotations

import copy
import json
import math
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from studio.core import ROOT

DATA = Path(os.environ.get("DRAMA_FACTORY_DATA", str(ROOT / "runtime/factory")))
DATABASE = DATA / "studio.sqlite3"
CHECKPOINTS = DATA / "checkpoints.sqlite3"
_CONFIG_SNAPSHOT = ContextVar("drama_factory_config_snapshot", default=None)

ROLE_LABELS = {
    "researcher": "选题研究", "creator": "故事提案", "story_editor": "故事策划",
    "story_critic": "故事审稿", "screenwriter": "编剧", "storyboard_director": "分镜导演",
    "independent_critic": "独立审稿", "visual_designer": "美术方案",
    "prompt_style_compiler": "画风提示词", "visual_critic": "画面质检",
    "dialogue_editor": "对白编辑", "edit_director": "剪辑建议",
    "executive_producer": "总制片代理",
}
OPTIONAL_ROLES = frozenset({"executive_producer"})
TEXT_CRITIC_ROLES = frozenset({"independent_critic", "story_critic"})


def resolve_llm(config, role, *, require_vision=False):
    """Resolve exactly one profile; unrouted legacy callers keep their default."""
    from .providers import normalize_profile
    routes = config.get("llm_roles", {})
    profiles = config.get("llm_profiles", {})
    if not isinstance(routes, dict) or not isinstance(profiles, dict):
        raise ValueError("llm_roles and llm_profiles must be objects")
    configured = role in routes
    profile = routes[role] if configured else "default"
    if configured:
        if not isinstance(profile, str) or profile not in profiles:
            raise ValueError("Unknown LLM profile for role " + role)
        if not isinstance(profiles[profile], dict):
            raise ValueError("LLM profile must be an object")
        lm = copy.deepcopy(profiles[profile])
        for key in ("base_url", "model"):
            if not isinstance(lm.get(key), str) or not lm[key].strip():
                raise ValueError("LLM profile requires " + key)
        lm = normalize_profile(lm)
    else:
        if not config.get("llm") and config.get("setup_incomplete"):
            raise ValueError("请先在模型与连接中为角色配置模型：" + role)
        lm = copy.deepcopy(config.get("llm", {}))
        if not isinstance(lm, dict):
            raise ValueError("llm must be an object")
        if all(key in lm for key in ("base_url", "model", "context_tokens")):
            lm = normalize_profile(lm, legacy=True)
        else:
            # Older offline callers may mock the transport with a partial legacy
            # model. A real request still validates its endpoint and context.
            from .providers import _reject_secret_fields
            _reject_secret_fields(lm)
    vision = lm.get("vision", not configured)
    if not isinstance(vision, bool):
        raise ValueError("LLM vision capability must be a boolean")
    if (require_vision or role == "visual_critic") and not vision:
        raise ValueError("Role %s requires a vision-capable LLM profile" % role)
    mode = lm.get("system_prompt_mode", "system")
    if mode not in ("system", "user_prefix"):
        raise ValueError("Unsupported system_prompt_mode")
    if "temperature_override" in lm:
        temperature = lm["temperature_override"]
        if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not math.isfinite(temperature) or not 0 <= temperature <= 2):
            raise ValueError("temperature_override must be within 0..2")
    top_p = lm.get("top_p", .9)
    if (isinstance(top_p, bool) or not isinstance(top_p, (int, float))
            or not math.isfinite(top_p) or not 0 < top_p <= 1):
        raise ValueError("top_p must be within (0,1]")
    if not isinstance(lm.get("chat_template_kwargs", {}), dict):
        raise ValueError("chat_template_kwargs must be an object")
    lm.update(profile=profile, configured=configured, vision=vision)
    return lm


def llm_identity(config, role):
    """Only non-secret model and request-policy fields may enter cache keys."""
    lm = resolve_llm(config, role)
    identity = {key: lm.get(key) for key in (
        "base_url", "model", "endpoint", "provider", "revision", "api_key_env", "api_key_credential",
        "network_mode", "enabled", "allowed_hosts", "context_validation", "model_aliases",
        "tls_ca_file_env", "tls_client_cert_env", "tls_client_key_env",
        "image_token_budget", "max_tokens_field", "send_sampling_parameters",
        "max_schema_attempts", "thinking", "model_discovery")}
    identity.update(profile=lm["profile"], vision=lm["vision"],
                    context_tokens=lm.get("context_tokens"),
                    response_format=lm.get("response_format", "llama_json_schema"),
                    chat_template_kwargs=copy.deepcopy(lm.get("chat_template_kwargs", {"enable_thinking": False})),
                    temperature_override=lm.get("temperature_override"),
                    top_p=lm.get("top_p", .9),
                    system_prompt_mode=lm.get("system_prompt_mode", "system"))
    from .providers import redact_secrets
    return redact_secrets(identity, lm)


def list_roles(config=None):
    """Public, read-only role inventory for the CLI and workflow console."""
    config = load_config() if config is None else config
    rows = []
    for role, label in ROLE_LABELS.items():
        if role in OPTIONAL_ROLES and role not in config.get("llm_roles", {}):
            rows.append({"role": role, "label": label, "profile": None, "endpoint": None, "model": None,
                         "provider": None, "network_mode": None, "enabled": False, "vision": False,
                         "configured": False, "requires_vision": False, "optional": True})
            continue
        if config.get("setup_incomplete") and role not in config.get("llm_roles", {}) and not config.get("llm"):
            rows.append({"role": role, "label": label, "profile": None, "enabled": False, "configured": False, "requires_vision": role == "visual_critic"})
            continue
        lm = resolve_llm(config, role)
        rows.append({"role": role, "label": label, "profile": lm["profile"],
                     "endpoint": lm.get("base_url"), "model": lm.get("model"),
                     "provider": lm.get("provider", "llama_cpp"),
                     "network_mode": lm.get("network_mode", "loopback"),
                     "enabled": lm.get("enabled", True),
                     "vision": lm["vision"], "configured": lm["configured"],
                     "requires_vision": role == "visual_critic"})
    return rows


def config_write_path():
    explicit = os.environ.get("DRAMA_FACTORY_CONFIG")
    return Path(explicit) if explicit else ROOT / "config/factory.json"


def config_read_path():
    target = config_write_path()
    if target.is_file() or os.environ.get("DRAMA_FACTORY_CONFIG"):
        return target
    return ROOT / "config/factory.example.json"


def load_config():
    snapshot = _CONFIG_SNAPSHOT.get()
    if snapshot is not None:
        return copy.deepcopy(snapshot)
    value = json.loads(config_read_path().read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported factory configuration schema")
    return copy.deepcopy(value)


@contextmanager
def config_snapshot(configuration=None):
    """Freeze one execution batch, up to its next human gate, across graph nodes."""
    snapshot = load_config() if configuration is None else copy.deepcopy(configuration)
    token = _CONFIG_SNAPSHOT.set(snapshot)
    try:
        yield copy.deepcopy(snapshot)
    finally:
        _CONFIG_SNAPSHOT.reset(token)


def production_config(project, config):
    from .video_providers import resolve_video
    result = copy.deepcopy(config.get("production", {}))
    result["video_connection"] = resolve_video(config)
    result["remote_step"] = True  # Worker executions always yield remote waiting.
    result["semantic_enabled"] = bool(config.get("quality",{}).get("semantic_enabled",True))
    result["semantic_model_identity"] = llm_identity(config, "visual_critic")
    result["semantic_model"] = result["semantic_model_identity"].get("model")
    from .evaluation_sampling import POLICY, sample_times
    count = config.get("quality", {}).get("temporal_samples", 8)
    sample_times(1, count)
    result["temporal_sampling"] = {"policy": POLICY, "count": count}
    budget = project["budget"]
    result.update(
        max_gpu_seconds=float(budget["gpu_minutes"]) * 60,
        max_attempts=int(budget["max_attempts"]),
        max_wall_seconds=float(budget.get("wall_minutes", 180)) * 60,
    )
    return result

