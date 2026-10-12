#!/usr/bin/env python3
"""Create a validated screenplay and storyboard episode from a creative brief."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from planner.client import request_json, check_model, context_budget
from planner.schema import episode_schema, generation_settings, validate_episode, normalize_episode

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sha(value):
    return hashlib.sha256(value).hexdigest()

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def project_path(path):
    value = Path(path)
    value = (ROOT / value).resolve() if not value.is_absolute() else value.resolve()
    if not value.is_relative_to(ROOT):
        raise ValueError("Planner output/config paths must stay inside this project.")
    return value

def system_prompt(settings, slug, smoke):
    return (
        "You are the screenplay and storyboard planner for a local AI short-drama production. "
        "Return exactly one complete JSON object satisfying the supplied schema. No Markdown, commentary, or reasoning. "
        "Create an ORIGINAL self-contained story with a concrete conflict, a revealing turn, and a resolved ending. "
        "Write the title, logline, dialogue, emotions, continuity notes and sound notes in simplified Chinese. "
        "LANGUAGE CONTRACT: cast.female, cast.male, visual_style, shots[].prompt and optional english_title MUST be English in printable ASCII, using Romanized character names. "
        "Dialogue emotion, continuity and sfx MUST contain simplified Chinese, never English-only directions. "
        "Write fixed cast descriptions, visual_style and image/video prompts in English. "
        "Keep each shot prompt about 400-650 ASCII characters, a continuity note about 60 Chinese characters, and sound notes about 30 Chinese characters. "
        "Keep the whole fourteen-shot episode concise, targeting 6000-8000 output tokens. "
        "Use exactly two ADULT fictional characters keyed female and male, with fixed names, ages, facial identity, haircut and clothing. "
        "Use one or two reusable locations. Prioritize a readable visual action in every shot, restrained natural performances, "
        "a clear opening hook and a final image that resolves the conflict. Avoid teaser-only endings and unfilmable abstract action. "
        "Every shot contains exactly ONE spoken Chinese line; voiceover is welcome when a face is not speaking on screen. Target 10–15 Hanzi and never exceed "
        + str(settings["max_hanzi_per_line"]) + " Hanzi. Start dialogue between 0.1 and 1.0 seconds. "
        "Do not place important content as generated image text; captions are handled in postproduction. "
        "All visual clues must use recognizable object shapes, unlettered symbols, texture or gesture, with no need to read any printed or handwritten words. "
        "Do not quote text in visual prompts or put story information on signs, envelopes, book covers or screens. "
        "reference_group must be female, male, or two. Use matching fixed character details across all shots; continuity states the action axis, props and wardrobe. "
        "Vary close-ups, reaction shots, inserts and voiceover. Do not make every shot a frontal talking head. "
        "Only mark lip_sync=true for selected clearly visible frontal speaking shots, at most one third of all shots. "
        "For a fourteen-shot episode choose about two or three frontal speaking close-ups; their reference_group must equal the dialogue speaker role so the visible face is unambiguous. "
        "Use a spoken opening hook. Make the cause of the misunderstanding, the revealing fact, and the resolution explicit through the short dialogue, not only the logline. "
        "Track state changes: a coat is removed only once and remains off, a box does not refill after becoming empty without visible action, and a prop keeps the same design. "
        "Chinese continuity and sfx use Chinese role descriptions such as 女店员 and 修书师; do not mix in English sentences or Romanized character names. "
        "For a phone-recording line only, optionally set audio_effect=telephone; this is a later postproduction effect. "
        "Optionally provide an English title and a short original Chinese poster_tagline. "
        f"Use exactly {settings['shots']} sequential shot ids S01, S02, ...; each shot lasts {settings['seconds_per_shot']} seconds at {settings['fps']} fps. "
        "Dialogue ids are the containing shot id followed by _L1. "
        "For shot index i starting at zero, timeline_start=i*seconds and timeline_end=(i+1)*seconds. "
        f"The slug is {slug!r}. The total duration is {settings['shots']*settings['seconds_per_shot']} seconds. "
        "Generation constants: " + encode(generation_settings(settings)) + ". "
        + ("This is ONLY a planner connection smoke-test draft. Prefix title with 测试草稿：. " if smoke else "")
    )

def reject_constant(value):
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "config/planner.json")
    parser.add_argument("--shots", type=int)
    parser.add_argument("--seconds-per-shot", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--model")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--smoke", action="store_true", help="Mark output receipt and title as a test draft.")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,60}", args.slug):
        raise ValueError("Invalid slug; use lowercase letters, numbers, '-' or '_'.")
    if not args.brief.strip() or len(args.brief) > 8000:
        raise ValueError("--brief must contain 1–8000 characters.")
    config = json.loads(project_path(args.config).read_text(encoding="utf-8"))
    if args.model:
        config["model"] = args.model
    settings = dict(config["defaults"])
    if args.shots is not None:
        settings["shots"] = args.shots
    if args.seconds_per_shot is not None:
        settings["seconds_per_shot"] = args.seconds_per_shot
    if not 2 <= settings["shots"] <= 40 or not 3 <= settings["seconds_per_shot"] <= 10:
        raise ValueError("Use 2–40 shots and 3–10 seconds per shot.")
    max_tokens = config["max_tokens"] if args.max_tokens is None else args.max_tokens
    minimum = 512 + settings["shots"] * 300
    if not minimum <= max_tokens <= config["context_tokens"] - 8192:
        raise ValueError(f"max_tokens must be {minimum}..{config['context_tokens']-8192} for this shot count/context.")
    output = project_path(args.output)
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"Output already exists: {output}; use a new path or explicit --overwrite.")
    schema = episode_schema(args.slug, settings)
    if config.get("response_format") == "openai_json_schema":
        response_format = {"type": "json_schema", "json_schema": {"name": "drama_episode", "strict": True, "schema": schema}}
    elif config.get("response_format") == "llama_json_schema":
        response_format = {"type": "json_object", "schema": schema}
    else:
        raise ValueError("Configure an explicit schema-constrained response format.")
    messages = [{"role": "system", "content": system_prompt(settings, args.slug, args.smoke)},
                {"role": "user", "content": "Creative brief:\n" + args.brief.strip() + "\nRequired JSON schema:\n" + encode(schema)
                 + "\n再次强调语言规则：cast、visual_style、每镜prompt以及可选english_title只能写英文，姓名用拼音或英文拼写；emotion、台词、连续性和音效写简体中文。每镜prompt约400到650个ASCII字符。剧情信息只靠可见动作和无字图形，不能依赖画面文字。只返回完整JSON。"}]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + args.slug + "-" + uuid.uuid4().hex[:8]
    run_dir = ROOT / "planner/runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": "drama-studio.planner-run.v1", "run_id": run_id,
               "artifact_status": "smoke_test_draft" if args.smoke else "unreviewed_production_draft",
               "status": "starting", "brief": args.brief, "slug": args.slug,
               "settings": settings, "endpoint": config["base_url"], "model": config["model"],
               "max_tokens": max_tokens, "schema_sha256": sha(encode(schema).encode()),
               "source_sha256": sha(Path(__file__).read_bytes()),
               "requested_output": str(output), "attempts": []}
    write_json(run_dir / "receipt.json", receipt)
    started = time.perf_counter()
    try:
        receipt["served_model_info"] = check_model(config)
        result = None
        for attempt in range(1, 3):
            request = {"model": config["model"], "messages": messages, "stream": False,
                       "n": 1, "temperature": config["temperature"], "top_p": 0.9,
                       "seed": config.get("seed", 20261009) if args.seed is None else args.seed,
                       "max_tokens": max_tokens, "response_format": response_format,
                       "chat_template_kwargs": config.get("chat_template_kwargs", {"enable_thinking": False})}
            write_json(run_dir / f"request-{attempt:02d}.json", request)
            budget = context_budget(config, messages, max_tokens)
            receipt.setdefault("context_checks", []).append({"attempt": attempt, **budget})
            write_json(run_dir / "receipt.json", receipt)
            if not budget["fits"]:
                raise ValueError("Prompt plus requested max_tokens exceeds the serving context: " + encode(budget))
            t0 = time.perf_counter()
            response = request_json(config["base_url"], "/chat/completions", request,
                                    timeout=config["request_timeout_seconds"], api_key_env=config.get("api_key_env"))
            write_json(run_dir / f"response-{attempt:02d}.json", response)
            choices = response.get("choices", [])
            if len(choices) != 1:
                raise ValueError("Expected exactly one completion choice.")
            choice = choices[0]
            finish = choice.get("finish_reason")
            message = choice.get("message", {})
            content = message.get("content")
            detail = {"attempt": attempt, "seconds": round(time.perf_counter()-t0, 3),
                      "finish_reason": finish, "usage": response.get("usage"), "timings": response.get("timings")}
            receipt["attempts"].append(detail)
            if finish == "length":
                raise ValueError("Model output was truncated at max_tokens; no episode was written. Increase the budget deliberately.")
            if finish != "stop" or message.get("tool_calls") or not isinstance(content, str):
                raise ValueError(f"Unexpected completion state: {finish!r}")
            try:
                result = json.loads(content, parse_constant=reject_constant)
                result, changes = normalize_episode(result)
                detail["normalizations"] = changes
                if changes:
                    write_json(run_dir / f"normalized-{attempt:02d}.json", result)
                errors = validate_episode(result, args.slug, settings)
            except (ValueError, TypeError) as exc:
                errors = [str(exc)]
            detail["validation_errors"] = errors
            write_json(run_dir / "receipt.json", receipt)
            if not errors:
                break
            if attempt == 2:
                raise ValueError("Episode contract failed after one repair: " + "; ".join(errors[:12]))
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": "Repair the JSON to satisfy these contract checks. Preserve the story and return the entire object again:\n" + "\n".join(errors)}]
        if args.smoke and not result["title"].startswith("测试草稿："):
            raise ValueError("Smoke draft title must start with 测试草稿：.")
        write_json(output, result)
        receipt.update(status="succeeded", elapsed_seconds=round(time.perf_counter()-started, 3),
                       output_sha256=sha(output.read_bytes()), output=str(output),
                       total_seconds=result["total_seconds"], shots=len(result["shots"]))
        write_json(run_dir / "receipt.json", receipt)
        write_json(output.with_suffix(output.suffix + ".planner.json"), receipt)
        print(json.dumps({"status": "succeeded", "artifact_status": receipt["artifact_status"],
                          "output": str(output), "receipt": str(run_dir / "receipt.json"),
                          "shots": len(result["shots"]), "seconds": result["total_seconds"],
                          "elapsed_seconds": receipt["elapsed_seconds"]}, ensure_ascii=False), flush=True)
    except Exception as exc:
        receipt.update(status="failed", error=str(exc), elapsed_seconds=round(time.perf_counter()-started, 3))
        write_json(run_dir / "receipt.json", receipt)
        raise

if __name__ == "__main__":
    main()
