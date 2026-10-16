"""Read-only description of the graph that the worker actually executes."""
from __future__ import annotations

from . import __version__
from .config import list_roles, load_config


def export_spec(config=None, workflow_version=2):
    from .workflows import build_graph
    config = load_config() if config is None else config
    topology = (build_graph(None) if workflow_version == 1 else build_graph(None,workflow_version)).get_graph().to_json()
    from .team_flow import team_spec
    assignment = team_spec(config)
    from .team_tasks import contracts as task_contracts
    from .video_providers import resolve_video
    video = resolve_video(config, require_enabled=False)
    remote_video = video["provider"] == "minimax"
    gpu_service = config.get("production", {}).get("gpu_service")
    video_location = ("Wkp GPU inference service" if gpu_service else
                      "external ComfyUI HTTP service" if video["provider"] == "comfyui_external" else "managed local GPU process")
    from .team_blueprint import topology as display_topology, GROUPS
    display_nodes,display_edges=display_topology(workflow_version)
    from .workflow_presentation import presentation
    return {
        "schema_version": 2, "workflow_version":workflow_version, "supported_workflow_versions":[1,2], "name": "ai-studio.short-drama", "version": __version__,
        "orchestrator": "LangGraph", "persistence": "SQLite WAL + LangGraph checkpoints",
        "configuration_scope": "One worker advance, until the next decision or pause",
        "nodes": topology["nodes"], "edges": topology["edges"],
        "production_graph":{"nodes":[{**node,"status":"pending"} for node in display_nodes],
                            "presentation": presentation(display_nodes, workflow_version),
                            "edges":display_edges,"groups":list(GROUPS),"workflow_version":workflow_version,
                            "semantics":"executable topology with task ownership; no execution inferred"},
        "roles": list_roles(config),
        "assignment_mode": assignment["assignment_mode"],
        "assignment_semantics": assignment["assignment_semantics"],
        "teams": assignment["teams"], "handoffs": assignment["handoffs"],
        "task_contracts": task_contracts(workflow_version),
        "human_gates": [
            {"id": "story_decision", "label": "选择故事", "choices": "eligible proposals"},
            {"id": "style_decision", "label": "选择视觉方案", "choices": "generated previews or __restyle__ with required feedback"},
            {"id": "final_decision", "label": "审阅初版成片", "choices": ["approve", "revise", "reedit"]},
        ],
        "engines": [
            {"id": "llm", "label": "编剧 / 导演 / 审稿 / 视觉理解",
             "protocol": "OpenAI-compatible HTTP / Ollama native HTTP", "mode": "request / response",
             "location": "per-profile: loopback / LAN / remote",
             "implementation": "factory.providers + factory.llm"},
            {"id": "comfyui", "label": "图像 / 本地视频",
             "protocol": "ComfyUI HTTP", "mode": "submit / history / artifacts",
             "location": "Wkp GPU inference service" if gpu_service else "managed local GPU process",
             "implementation": "factory.scheduler + studio.comfy",
             "models": ["FLUX.2 klein 4B", *([] if remote_video else [video["model"]])],
             "receipt_fields": ["prompt_id", "workflow_api.json", "history.json", "sha256"]},
            {"id": "selected_video", "label": "当前视频生成分配",
             "provider": video["provider"], "profile": video["profile"], "model": video["model"],
             "enabled": video["enabled"],
             "protocol": "MiniMax v1 HTTP" if remote_video else "ComfyUI HTTP",
             "mode": "submit / query / file" if remote_video else "submit / history / artifacts",
             "location": video.get("network_mode", "remote") if remote_video else video_location,
             "implementation": "factory.minimax_video" if remote_video else "factory.production + studio.comfy",
             "applies_to": ["animation", "live_action"],
             "receipt_fields": ["task_id", "file_id", "sha256"] if remote_video else ["prompt_id", "sha256"]},
            {"id": "speech", "label": "角色配音",
             "protocol": "bounded local subprocess", "mode": "text / WAV / receipt",
             "location": "local dedicated Python environment",
             "implementation": "factory.speech + audio.tts", "model": "Qwen3-TTS 1.7B"},
            {"id": "editor", "label": "剪辑 / 混音 / 字幕 / 技术检查",
             "protocol": "local FFmpeg / PyAV", "mode": "timeline / MP4 / manifest",
             "location": "local CPU", "implementation": "factory.audio_editor + studio.media"},
        ],
        "limits": {"creative_review_rounds": config.get("creative", {}).get("max_review_rounds", 2),
                   "max_parallel_generation": config.get("production", {}).get("max_parallel", 1),
                   "budgets": "Each project authorizes GPU minutes, elapsed minutes and attempt limits"},
        "semantics": "Read-only export. Importing model bindings does not replace or execute graph code.",
    }
