"""Wire translations for native Claude Messages and Gemini generateContent."""
from __future__ import annotations

import re
from urllib.parse import quote


def route(profile, operation):
    if operation == "models":
        return "/models"
    return "/messages" if profile["provider"] == "anthropic" else "/models/" + quote(profile["model"], safe="-._") + ":generateContent"


def _parts(content, provider):
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if provider == "anthropic" else [{"text": content}]
    parts = []
    for part in content:
        if part.get("type") == "text":
            parts.extend(_parts(part["text"], provider))
        elif part.get("type") == "image_url":
            image = part["image_url"]
            url = image.get("url") if isinstance(image, dict) else image
            match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/]+={0,2})", url or "")
            if not match:
                raise ValueError("Native review images must use inline base64 data")
            mime, data = match.groups()
            if provider == "anthropic":
                parts.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
            else:
                parts.append({"inlineData": {"mimeType": mime, "data": data}})
        else:
            raise ValueError("Unsupported native message content")
    return parts


def request(profile, messages, schema, parameters):
    provider = profile["provider"]
    # Keep the full original schema in the prompt and validate locally; do not
    # silently weaken it to a provider's restricted JSON Schema subset.
    if profile["response_format"] != "prompt":
        raise ValueError("Native text profiles currently require prompt output mode with local schema validation")
    systems = [m["content"] for m in messages if m["role"] == "system"]
    others = [m for m in messages if m["role"] != "system"]
    if provider == "anthropic":
        body = {"model": profile["model"], "max_tokens": parameters["max_tokens"],
                "messages": [{"role": m["role"], "content": _parts(m["content"], provider)} for m in others]}
        if systems:
            body["system"] = "\n".join(systems)
        if parameters["send_sampling_parameters"]:
            body["temperature"] = parameters["temperature"]
    else:
        body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user",
                              "parts": _parts(m["content"], provider)} for m in others],
                "generationConfig": {"maxOutputTokens": parameters["max_tokens"], "responseMimeType": "application/json"}}
        if systems:
            body["systemInstruction"] = {"parts": [{"text": "\n".join(systems)}]}
        if parameters["send_sampling_parameters"]:
            body["generationConfig"].update(temperature=parameters["temperature"], topP=parameters["top_p"])
    return route(profile, "chat"), body


def response(profile, result):
    if profile["provider"] == "anthropic":
        blocks = result.get("content", [])
        text = "".join(p.get("text", "") for p in blocks if p.get("type") == "text")
        # Tool calls and refusals cannot masquerade as a complete text response.
        finish = "stop" if result.get("stop_reason") == "end_turn" and all(p.get("type") in {"text", "thinking", "redacted_thinking"} for p in blocks) else result.get("stop_reason", "incomplete")
        raw = result.get("usage", {})
        usage = {"prompt_tokens": raw.get("input_tokens"), "completion_tokens": raw.get("output_tokens")}
        model = result.get("model")
    else:
        candidates = result.get("candidates", [])
        if len(candidates) != 1:
            return {"model": result.get("modelVersion"), "choices": []}
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought") and "text" in p)
        finish = "stop" if candidate.get("finishReason") == "STOP" and all("text" in p for p in parts) else candidate.get("finishReason", "incomplete")
        raw = result.get("usageMetadata", {})
        usage = {"prompt_tokens": raw.get("promptTokenCount"), "completion_tokens": raw.get("candidatesTokenCount")}
        model = result.get("modelVersion")
    usage = {k: v for k, v in usage.items() if type(v) is int and v >= 0}
    if len(usage) == 2:
        usage["total_tokens"] = sum(usage.values())
    return {"model": model, "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text}}], "usage": usage}
