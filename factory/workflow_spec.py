"""Read-only description of the graph that the worker actually executes."""
from __future__ import annotations

from . import __version__
from .config import list_roles, load_config


def export_spec(config=None):
    from .workflows import build_graph
    config = load_config() if config is None else config
    topology = build_graph(None).get_graph().to_json()
    return {
        "schema_version": 1, "name": "ai-studio.short-drama", "version": __version__,
        "orchestrator": "LangGraph", "persistence": "SQLite WAL + LangGraph checkpoints",
        "configuration_scope": "One worker advance, until the next decision or pause",
        "nodes": topology["nodes"], "edges": topology["edges"],
        "roles": list_roles(config),
        "human_gates": [
            {"id": "story_decision", "label": "选择故事", "choices": "eligible proposals"},
            {"id": "style_decision", "label": "选择视觉方案", "choices": "generated previews"},
            {"id": "final_decision", "label": "审阅初版成片", "choices": ["approve", "revise"]},
        ],
        "engines": [
            {"id": "llm", "label": "编剧 / 导演 / 审稿 / 视觉理解",
             "protocol": "OpenAI-compatible HTTP", "mode": "request / response",
             "location": "per-profile: loopback / LAN / remote",
             "implementation": "factory.providers + factory.llm"},
            {"id": "comfyui", "label": "图像 / 视频",
             "protocol": "ComfyUI HTTP", "mode": "submit / history / artifacts",
             "location": "managed local GPU process",
             "implementation": "factory.scheduler + studio.comfy",
             "models": ["FLUX.2 klein 4B", "Wan2.2 TI2V 5B"],
             "receipt_fields": ["prompt_id", "workflow_api.json", "history.json", "sha256"]},
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
