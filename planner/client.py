"""Small loopback-only OpenAI-compatible client with bounded JSON reads."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES = 2 * 1024 * 1024

def local_base(value):
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Planner base_url must be a local loopback HTTP(S) endpoint.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Planner base_url must not contain credentials, a query, or a fragment.")
    return value.rstrip("/")

def request_json(base, route, payload=None, timeout=30, api_key_env=None):
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        secret = os.environ.get(api_key_env)
        if not secret:
            raise ValueError(f"Missing configured authentication environment variable: {api_key_env}")
        headers["Authorization"] = "Bearer " + secret
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(local_base(base) + route, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"Local planner HTTP {exc.code}: {detail}") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Planner response exceeded the bounded JSON response limit.")
    return json.loads(body.decode("utf-8"))

def check_model(config):
    result = request_json(config["base_url"], "/models", timeout=10,
                          api_key_env=config.get("api_key_env"))
    models = [x.get("id") for x in result.get("data", [])]
    if config["model"] not in models:
        raise RuntimeError(f"Requested model {config['model']!r} is not served. Visible ids: {models!r}")
    return result

def context_budget(config, messages, max_tokens):
    """Count the current conversation with the serving llama.cpp tokenizer."""
    if config.get("context_validation") != "llama_tokenize":
        # Explicitly configured alternative endpoints retain the conservative CLI guard.
        return {"method": "configured_margin", "context_tokens": config["context_tokens"],
                "reserved_prompt_tokens": 8192, "max_tokens": max_tokens,
                "fits": max_tokens + 8192 <= config["context_tokens"]}
    base = local_base(config["base_url"])
    if not base.endswith("/v1"):
        raise ValueError("llama_tokenize requires a base_url ending in /v1.")
    server = base[:-3]
    kwargs = {"timeout": 30, "api_key_env": config.get("api_key_env")}
    props = request_json(server, "/props", **kwargs)
    actual_context = props.get("default_generation_settings", {}).get("n_ctx")
    if not isinstance(actual_context, int) or actual_context < 1024:
        raise ValueError("Serving llama.cpp did not report a valid per-slot context length.")
    rendered = request_json(server, "/apply-template", {
        "messages": messages,
        "chat_template_kwargs": config.get("chat_template_kwargs", {"enable_thinking": False}),
    }, **kwargs)
    if not isinstance(rendered.get("prompt"), str):
        raise ValueError("Serving llama.cpp did not return a rendered chat prompt.")
    tokens = request_json(server, "/tokenize", {"content": rendered["prompt"],
                          "add_special": False, "parse_special": True}, **kwargs).get("tokens")
    if not isinstance(tokens, list) or not all(isinstance(t, int) for t in tokens):
        raise ValueError("Serving llama.cpp returned an invalid token count response.")
    available = min(config["context_tokens"], actual_context)
    count = len(tokens)
    margin = 64
    return {"method": "llama_tokenize", "prompt_tokens": count,
            "server_context_tokens": actual_context, "context_tokens": available,
            "max_tokens": max_tokens, "margin_tokens": margin,
            "fits": count + max_tokens + margin <= available}
