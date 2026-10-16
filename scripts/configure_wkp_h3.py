#!/usr/bin/env python3
"""Check Wkp's actual services, then explicitly switch an idle cami installation to H3."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.gpu_client import GPUClient
from factory.video_providers import H3_PROFILE, H3_PRODUCTION_PROFILE
from scripts import deploy_cami as deploy


def verified_configuration(current, client, *, quality="preview", vision_model=None):
    """No generation, service operations or writes; unknown model identity fails closed."""
    selected_profile = H3_PRODUCTION_PROFILE if quality == "production" else H3_PROFILE
    if quality not in {"preview", "production"}:
        raise ValueError("Unknown H3 quality")
    client.require_h3(selected_profile) if quality == "production" else client.require_h3()
    updated = copy.deepcopy(current)
    for name in ("primary", "critic", "vision"):
        profile = updated.get("llm_profiles", {}).get(name)
        if not isinstance(profile, dict) or profile.get("base_url") != client.base + "/models/" + name + "/v1":
            raise ValueError("Preserve customized model bindings; expected the existing Wkp " + name + " route")
        catalog = client.request("/models/" + name + "/v1/models").get("data")
        if not isinstance(catalog, list) or len(catalog) != 1 or not isinstance(catalog[0].get("id"), str):
            raise ValueError("Wkp must report one unambiguous model for " + name)
        if name == "vision" and vision_model is not None and catalog[0]["id"] != vision_model:
            raise ValueError("Requested visual reviewer is not the actual Wkp vision service; no configuration changed")
        props = client.request("/models/" + name + "/props")
        modalities = props.get("modalities", {})
        image_input = modalities.get("vision")
        context = props.get("default_generation_settings", {}).get("n_ctx")
        if type(image_input) is not bool or type(context) is not int or context < 1024:
            raise ValueError("Wkp did not report model modalities/context for " + name)
        if name == "vision" and not image_input:
            raise ValueError("Wkp vision service cannot accept images")
        if name == "vision" and vision_model == "ai-studio-qwen38-vision":
            profile["image_token_budget"] = 1024
        profile.update(model=catalog[0]["id"], vision=image_input,
                       context_tokens=min(profile.get("context_tokens", context), context))
    if updated.get("llm_roles", {}).get("visual_critic") != "vision":
        raise ValueError("Preserve customized visual-review binding")
    selected = updated.get("video_roles", {}).get("video_generator")
    old = updated.get("video_profiles", {}).get(selected, {})
    if old.get("provider") not in {"comfyui_wan", "comfyui_h3"}:
        raise ValueError("Preserve customized video binding")
    if "wkp_h3" in updated["video_profiles"] and updated["video_profiles"]["wkp_h3"] not in (H3_PROFILE, selected_profile):
        raise ValueError("The wkp_h3 profile already has customized settings")
    for profile in updated["video_profiles"].values():
        if profile.get("provider") == "comfyui_wan":
            profile["enabled"] = False
    updated["video_profiles"]["wkp_h3"] = copy.deepcopy(selected_profile)
    updated["video_roles"]["video_generator"] = "wkp_h3"
    updated["production"].update(video_profile="h3_production" if quality == "production" else "h3_turbo4",
                                 video_width=768 if quality == "production" else 352,
                                 video_height=1344 if quality == "production" else 640)
    if quality == "production":
        updated["production"].update(h3_reserve_vram_gib=12, h3_int8_scratch_mib=512)
    if updated.get("llm_roles", {}).get("screenwriter") == "primary":
        updated["llm"] = copy.deepcopy(updated["llm_profiles"]["primary"])
    return updated


def configure(apply=False, *, quality="production", vision_model="ai-studio-qwen38-vision"):
    with deploy.deployment_lock():
        path = deploy.guarded(deploy.WORKSPACE / "config.json", private=True)
        original = path.read_bytes()
        current = json.loads(original)
        updated = verified_configuration(current, GPUClient(current["production"]["gpu_service"]),
                                          quality=quality, vision_model=vision_model)
        if apply:
            # Both services must be stopped, closing the new-job race during a configuration switch.
            if any(deploy.service_state(name)["active"] for name in deploy.UNITS) or deploy.active_jobs():
                raise deploy.DeployError("Stop the idle cami Web and worker before applying H3 configuration")
            if path.read_bytes() != original:
                raise deploy.DeployError("Configuration changed during model discovery; nothing was written")
            deploy.atomic_write(path, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
        return {"applied": apply, "video_provider": "comfyui_h3", "video_model": H3_PROFILE["model"],
                "video_quality": quality, "workflow_revision": updated["video_profiles"]["wkp_h3"]["revision"],
                "vision_model": updated["llm_profiles"]["vision"]["model"],
                "vision_reason": "Actual service catalog and image modality were verified",
                "generation": False, "services_restarted": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write only after H3 readiness and idle/stopped-service checks")
    parser.add_argument("--quality", choices=["preview", "production"], default="production")
    parser.add_argument("--vision-model", default="ai-studio-qwen38-vision", help="Require this exact actual model ID before switching")
    args = parser.parse_args()
    try:
        print(json.dumps(configure(args.apply, quality=args.quality, vision_model=args.vision_model), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
