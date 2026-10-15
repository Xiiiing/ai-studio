"""Explicit, bounded model transports and the editable model registry.

Model servers run outside this application. This module speaks HTTP; it cannot
start a shell, install a model, follow redirects, or silently change providers.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import http.client
import ipaddress
import json
import math
import os
import queue
import re
import socket
import stat
import ssl
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 8 * 1024 * 1024
_DNS_SLOTS = threading.BoundedSemaphore(4)
PROVIDERS = {
    "llama_cpp": {"label": "llama.cpp", "protocol": "openai",
                  "base_url_example": "http://127.0.0.1:8090/v1",
                  "response_format": "llama_json_schema", "context_validation": "llama_tokenize"},
    "ollama": {"label": "Ollama /v1", "protocol": "openai",
               "base_url_example": "http://127.0.0.1:11434/v1",
               "response_format": "openai_json_schema", "context_validation": "conservative_estimate"},
    "ollama_native": {"label": "Ollama native", "protocol": "ollama",
                      "base_url_example": "http://127.0.0.1:11434",
                      "response_format": "openai_json_schema", "context_validation": "conservative_estimate"},
    "vllm": {"label": "vLLM", "protocol": "openai",
             "base_url_example": "http://127.0.0.1:8000/v1",
             "response_format": "openai_json_schema", "context_validation": "conservative_estimate"},
    "lm_studio": {"label": "LM Studio", "protocol": "openai",
                  "base_url_example": "http://127.0.0.1:1234/v1",
                  "response_format": "openai_json_schema", "context_validation": "conservative_estimate"},
    "deepseek": {"label": "DeepSeek API", "protocol": "openai",
                 "base_url_example": "https://api.deepseek.com",
                 "response_format": "json_object", "context_validation": "conservative_estimate"},
    "dashscope": {"label": "Qwen / DashScope", "protocol": "openai",
                  "base_url_example": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "model_discovery": "skip",
                  "response_format": "json_object", "context_validation": "conservative_estimate"},
    "openai_compatible": {"label": "OpenAI-compatible API", "protocol": "openai",
                          "base_url_example": "http://127.0.0.1:8000/v1",
                          "response_format": "json_object", "context_validation": "conservative_estimate"},
}
PROFILE_FIELDS = frozenset({
    "provider", "enabled", "network_mode", "allowed_hosts", "base_url", "model",
    "context_tokens", "vision", "api_key_env", "api_key_credential", "response_format", "context_validation",
    "tls_ca_file_env", "tls_client_cert_env", "tls_client_key_env",
    "model_aliases", "revision", "request_timeout_seconds", "max_schema_attempts",
    "chat_template_kwargs", "temperature_override", "top_p", "system_prompt_mode",
    "image_token_budget", "max_tokens_field", "send_sampling_parameters", "thinking", "model_discovery",
    # Legacy defaults are preserved; role-specific calls still set an explicit budget.
    "max_tokens", "temperature",
})
TLS_ENV_FIELDS = ("tls_ca_file_env", "tls_client_cert_env", "tls_client_key_env")

SECRET_FIELDS = frozenset({
    "api_key", "apikey", "authorization", "password", "passwd", "secret",
    "access_token", "refresh_token", "credentials", "headers", "token",
})
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
CREDENTIAL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                     ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))


class ProviderError(RuntimeError):
    """A stable, safe error; remote error bodies and credentials never enter it."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ConfigConflict(ValueError):
    pass


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def config_revision(config):
    return hashlib.sha256(_json_bytes(config)).hexdigest()


def _reject_secret_fields(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in SECRET_FIELDS:
                raise ValueError("Inline credentials are not accepted; use api_key_env")
            _reject_secret_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_fields(item)


def _credential_value(profile, *, required=False):
    """Read only a named systemd LoadCredential, never a caller-supplied file path."""
    name = profile.get("api_key_credential")
    if not name:
        return ""
    if not isinstance(name, str) or not CREDENTIAL_RE.fullmatch(name):
        raise ValueError("Invalid named systemd credential")
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not os.path.isabs(directory):
        if required:
            raise ProviderError("missing_auth", "The configured systemd credential is not mounted")
        return ""
    descriptor = None
    try:
        descriptor = os.open(os.path.join(directory, name), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= 512:
            raise ProviderError("invalid_auth", "The configured systemd credential has invalid metadata")
        raw = os.read(descriptor, 513)
        secret = raw.decode("utf-8").strip()
        if not secret or len(secret) > 512 or any(ord(char) < 33 or ord(char) > 126 for char in secret):
            raise ProviderError("invalid_auth", "The configured systemd credential is not a valid API token")
        return secret
    except (OSError, UnicodeError):
        if required:
            raise ProviderError("missing_auth", "The configured systemd credential cannot be read") from None
        return ""
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _credential_available(profile):
    """Inventory reports existence only; it does not read or expose a token."""
    name = profile.get("api_key_credential")
    directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not isinstance(name, str) or not CREDENTIAL_RE.fullmatch(name) or not os.path.isabs(directory):
        return False
    try:
        details = os.stat(os.path.join(directory, name), follow_symlinks=False)
        return stat.S_ISREG(details.st_mode) and 1 <= details.st_size <= 512
    except OSError:
        return False


def redact_secrets(value, profile=None):
    """Scrub both credential fields and the active environment value recursively."""
    env_name = (profile or {}).get("api_key_env")
    secret = os.environ.get(env_name, "") if isinstance(env_name, str) else ""
    if not secret and (profile or {}).get("api_key_credential"):
        try:
            secret = _credential_value(profile)
        except (ValueError, ProviderError):
            secret = ""
    def clean(item):
        if isinstance(item, dict):
            return {clean(str(key)): "[REDACTED]" if str(key).lower() in SECRET_FIELDS else clean(part)
                    for key, part in item.items()}
        if isinstance(item, list):
            return [clean(part) for part in item]
        if isinstance(item, tuple):
            return [clean(part) for part in item]
        if isinstance(item, str) and secret:
            return item.replace(secret, "[REDACTED]")
        return item
    return clean(value)


def _number(value, name, minimum, maximum, *, integer=False):
    valid_type = type(value) is int if integer else (
        not isinstance(value, bool) and isinstance(value, (int, float)))
    if not valid_type or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(name + " is outside its supported range")
    return value


def _valid_text(value, name, maximum=512, *, allow_empty=False):
    if (not isinstance(value, str) or len(value) > maximum or
            (not allow_empty and not value.strip()) or
            any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError(name + " must be bounded plain text")
    return value


def _canonical_host(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("allowed_hosts must contain exact host names without URLs or ports")
    host = value.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?", host):
            raise ValueError("allowed_hosts must contain exact host names without URLs or ports") from None
        return host


def _address_allowed(address, mode):
    ip = ipaddress.ip_address(address)
    if mode == "loopback":
        return ip.is_loopback
    if mode == "lan":
        return any(ip in network for network in LAN_NETWORKS if network.version == ip.version)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved)


def _validate_tls_fields(profile):
    """TLS profiles store environment names, never certificate/key material or paths."""
    for field in TLS_ENV_FIELDS:
        name = profile.get(field)
        if name not in (None, "") and (not isinstance(name, str) or not ENV_RE.fullmatch(name)):
            raise ValueError(field + " must be an environment variable name")
    if bool(profile.get("tls_client_cert_env")) != bool(profile.get("tls_client_key_env")):
        raise ValueError("TLS client certificate and key environment names must be configured together")


def _tls_file_path(profile, field):
    name = profile.get(field)
    if not name:
        return None
    value = os.environ.get(name)
    if not value:
        raise ProviderError("missing_tls_configuration", "A configured TLS file environment variable is not set")
    try:
        if not os.path.isabs(value) or "\0" in value:
            raise ValueError
        details = os.stat(value)
        if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= 1024 * 1024:
            raise ValueError
    except (OSError, ValueError):
        raise ProviderError("invalid_tls_configuration", "A configured TLS file is not a bounded local regular file") from None
    return value


def _tls_context(profile=None):
    """Verify the server and optionally authenticate with this profile's local identity."""
    policy = profile or {}
    _validate_tls_fields(policy)
    ca = _tls_file_path(policy, "tls_ca_file_env")
    certificate = _tls_file_path(policy, "tls_client_cert_env")
    key = _tls_file_path(policy, "tls_client_key_env")
    try:
        context = ssl.create_default_context(cafile=ca)
        if certificate:
            # An encrypted key fails closed instead of prompting an unattended worker.
            context.load_cert_chain(certfile=certificate, keyfile=key, password=lambda: "")
        return context
    except (OSError, ValueError):
        raise ProviderError("invalid_tls_configuration", "The configured TLS CA or client identity could not be loaded") from None


def validate_endpoint(profile):
    """Validate syntax and explicit scope without performing a DNS/network request."""
    _validate_tls_fields(profile)
    value = _valid_text(profile.get("base_url"), "base_url", 2048)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid model endpoint URL") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Model endpoint must use HTTP or HTTPS")
    if any(profile.get(field) for field in TLS_ENV_FIELDS) and parsed.scheme != "https":
        raise ValueError("TLS client identity and CA configuration require HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Model endpoint must not contain credentials, query, or fragment")
    if (not re.fullmatch(r"/[A-Za-z0-9._~/-]*", parsed.path or "/") or
            ".." in parsed.path.split("/") or "//" in parsed.path):
        raise ValueError("Unsupported model endpoint path")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid model endpoint port")
    host = _canonical_host(parsed.hostname)
    mode = profile.get("network_mode", "loopback")
    if mode not in ("loopback", "lan", "remote"):
        raise ValueError("network_mode must be loopback, lan, or remote")
    if mode == "loopback":
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Loopback model endpoints must be local loopback addresses")
    else:
        if profile.get("enabled") is not True:
            # Disabled profiles remain editable, but network scope must still be explicit.
            if "enabled" not in profile:
                raise ValueError("Non-loopback profiles require explicit enabled")
        allowed = profile.get("allowed_hosts")
        if not isinstance(allowed, list) or not 1 <= len(allowed) <= 16:
            raise ValueError("Non-loopback profiles require an exact allowed_hosts list")
        if host not in [_canonical_host(item) for item in allowed]:
            raise ValueError("Model endpoint host is not in allowed_hosts")
        if mode == "remote" and parsed.scheme != "https":
            raise ValueError("Remote model endpoints require HTTPS")
        if (profile.get("api_key_env") or profile.get("api_key_credential")) and parsed.scheme != "https":
            raise ValueError("Authenticated non-loopback model endpoints require HTTPS")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not _address_allowed(str(address), mode):
            raise ValueError("Model endpoint address is outside the configured network scope")
    return value.rstrip("/")


def normalize_profile(value, *, legacy=False):
    """Return a safe, complete profile. No network access and no secret lookup."""
    if not isinstance(value, dict):
        raise ValueError("LLM profile must be an object")
    _reject_secret_fields(value)
    if set(value) - PROFILE_FIELDS:
        raise ValueError("Unsupported LLM profile fields")
    profile = copy.deepcopy(value)
    for field in TLS_ENV_FIELDS:
        if profile.get(field) in (None, ""):
            profile.pop(field, None)
    provider = profile.get("provider", "llama_cpp")
    if provider not in PROVIDERS:
        raise ValueError("Unsupported model provider")
    profile["provider"] = provider
    discovery = profile.setdefault("model_discovery", "skip" if provider == "dashscope" else "required")
    if discovery not in ("required", "skip"):
        raise ValueError("model_discovery must be required or skip")
    if discovery == "skip" and provider in ("llama_cpp", "ollama_native"):
        raise ValueError("This provider requires model discovery")
    mode = profile.get("network_mode", "loopback")
    if mode != "loopback" and "enabled" not in profile:
        raise ValueError("Non-loopback profiles require explicit enabled")
    profile.setdefault("enabled", True)
    if not isinstance(profile["enabled"], bool):
        raise ValueError("enabled must be boolean")
    profile["network_mode"] = mode
    profile.setdefault("allowed_hosts", [])
    if not isinstance(profile["allowed_hosts"], list) or len(profile["allowed_hosts"]) > 16:
        raise ValueError("allowed_hosts must be a bounded list")
    profile["allowed_hosts"] = sorted(set(_canonical_host(host) for host in profile["allowed_hosts"]))
    profile["base_url"] = validate_endpoint(profile)
    if provider == "ollama_native":
        path = urlsplit(profile["base_url"]).path
        if path.endswith(("/v1", "/api/chat", "/api/tags")):
            raise ValueError("Ollama native base_url must be a service root or /api prefix")
    profile["model"] = _valid_text(profile.get("model"), "model")
    profile["context_tokens"] = _number(profile.get("context_tokens"), "context_tokens",
                                        1024, 2000000, integer=True)
    profile.setdefault("vision", legacy)
    if not isinstance(profile["vision"], bool):
        raise ValueError("LLM vision capability must be a boolean")
    env_name = profile.get("api_key_env")
    if env_name in ("", None):
        profile.pop("api_key_env", None)
    elif not isinstance(env_name, str) or not ENV_RE.fullmatch(env_name):
        raise ValueError("api_key_env must be an environment variable name")
    # Validate authenticated LAN policy after environment-name normalization.
    credential_name = profile.get("api_key_credential")
    if credential_name in ("", None):
        profile.pop("api_key_credential", None)
    elif not isinstance(credential_name, str) or not CREDENTIAL_RE.fullmatch(credential_name):
        raise ValueError("api_key_credential must be an approved systemd credential identifier")
    if profile.get("api_key_env") and profile.get("api_key_credential"):
        raise ValueError("Use exactly one model authentication source")
    validate_endpoint(profile)
    fmt = profile.setdefault("response_format", PROVIDERS[provider]["response_format"])
    if fmt not in ("llama_json_schema", "openai_json_schema", "json_object", "prompt"):
        raise ValueError("Unsupported response_format")
    tokenizer = profile.setdefault("context_validation", PROVIDERS[provider]["context_validation"])
    if tokenizer not in ("llama_tokenize", "conservative_estimate"):
        raise ValueError("Unsupported context_validation")
    if tokenizer == "llama_tokenize" and (provider != "llama_cpp" or not profile["base_url"].endswith("/v1")):
        raise ValueError("llama_tokenize requires llama_cpp with a /v1 base URL")
    aliases = profile.setdefault("model_aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 32:
        raise ValueError("model_aliases must be a bounded list")
    profile["model_aliases"] = list(dict.fromkeys(_valid_text(alias, "model alias") for alias in aliases))
    if "revision" in profile:
        _valid_text(profile["revision"], "revision", 512, allow_empty=True)
    for name, default, minimum, maximum in (
        ("request_timeout_seconds", 480, 1, 600),
        ("max_schema_attempts", 2, 1, 2),
        ("image_token_budget", 4096, 1024, 16384),
    ):
        profile[name] = _number(profile.get(name, default), name, minimum, maximum, integer=True)
    template = profile.setdefault("chat_template_kwargs",
                                   {"enable_thinking": False} if provider == "llama_cpp" else {})
    if not isinstance(template, dict) or len(_json_bytes(template)) > 4096:
        raise ValueError("chat_template_kwargs must be a bounded object")
    if provider in ("ollama_native", "dashscope") and template:
        raise ValueError("Use thinking instead of chat_template_kwargs for this provider")
    if provider == "dashscope":
        profile.setdefault("thinking", False)
    if "thinking" in profile:
        if provider not in ("ollama_native", "dashscope") or not isinstance(profile["thinking"], bool):
            raise ValueError("thinking must be boolean and requires Ollama native or DashScope")
    if "temperature_override" in profile:
        _number(profile["temperature_override"], "temperature_override", 0, 2)
    if "temperature" in profile:
        _number(profile["temperature"], "temperature", 0, 2)
    if "max_tokens" in profile:
        _number(profile["max_tokens"], "max_tokens", 128, 16384, integer=True)
    profile["top_p"] = _number(profile.get("top_p", .9), "top_p", .000001, 1)
    profile.setdefault("system_prompt_mode", "system")
    if profile["system_prompt_mode"] not in ("system", "user_prefix"):
        raise ValueError("Unsupported system_prompt_mode")
    profile.setdefault("max_tokens_field", "max_tokens")
    if profile["max_tokens_field"] not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("Unsupported max_tokens_field")
    profile.setdefault("send_sampling_parameters", True)
    if not isinstance(profile["send_sampling_parameters"], bool):
        raise ValueError("send_sampling_parameters must be boolean")
    return profile


def model_matches(profile, actual):
    if not isinstance(actual, str) or not actual:
        return False
    expected = [profile["model"], *profile.get("model_aliases", [])]
    if actual in expected:
        return True
    if profile.get("provider") in ("ollama", "ollama_native"):
        # Ollama's omitted tag means :latest. Preserve every namespace and all
        # explicit tags; no prefix guessing or substitution of another model.
        def tagged(name):
            return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"
        return tagged(actual) in {tagged(name) for name in expected}
    return False


def model_route(profile, operation):
    """Join a protocol route onto the operator's complete, unchanged API prefix."""
    if operation not in ("models", "chat"):
        raise ValueError("Unsupported model operation")
    if profile.get("provider") == "ollama_native":
        prefix = "" if urlsplit(profile["base_url"]).path.rstrip("/").endswith("/api") else "/api"
        return prefix + ("/tags" if operation == "models" else "/chat")
    return "/models" if operation == "models" else "/chat/completions"


def _resolve_addresses(host, port, mode, *, timeout=8):
    """Bound DNS time, check every answer, and pin the eventual connection IPs."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        found = [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (host, port))]
    else:
        if not _DNS_SLOTS.acquire(blocking=False):
            raise ProviderError("dns_busy", "Model DNS resolver is busy; retry the health check later")
        replies = queue.Queue(maxsize=1)
        def resolve():
            try:
                replies.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
            except Exception:
                replies.put((False, None))
            finally:
                _DNS_SLOTS.release()
        worker = threading.Thread(target=resolve, daemon=True, name="drama-model-dns")
        worker.start()
        try:
            success, found = replies.get(timeout=timeout)
        except queue.Empty:
            raise ProviderError("dns_timeout", "Model endpoint DNS lookup timed out") from None
        if not success:
            raise ProviderError("dns_failed", "Model endpoint DNS lookup failed")
    if not found:
        raise ProviderError("dns_failed", "Model endpoint DNS lookup returned no addresses")
    addresses = []
    for family, kind, protocol, _, sockaddr in found:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        if not _address_allowed(sockaddr[0], mode):
            raise ProviderError("address_denied", "Model endpoint resolved outside its configured network scope")
        row = (family, kind, protocol, sockaddr)
        if row not in addresses:
            addresses.append(row)
    if not addresses:
        raise ProviderError("dns_failed", "Model endpoint has no supported addresses")
    return addresses[:4]


def _connect_socket(addresses, timeout):
    deadline = time.monotonic() + timeout
    for family, kind, protocol, sockaddr in addresses:
        connection = socket.socket(family, kind, protocol)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            connection.settimeout(remaining)
            connection.connect(sockaddr)
            return connection
        except OSError:
            connection.close()
    raise ProviderError("connect_failed", "Model endpoint connection failed or timed out")


def _remaining_time(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Model request deadline exceeded")
    return remaining


def _connection(host, port, secure, addresses, timeout, *, deadline=None, register_socket=None, profile=None):
    # http.client does not follow redirects and does not read proxy environment variables.
    deadline = time.monotonic() + timeout if deadline is None else deadline
    register_socket = register_socket or (lambda value: None)

    class DeadlineHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            http.client.HTTPConnection.connect(self)
            # Register the live SSL socket before handshaking. Standard HTTPSConnection
            # detaches the raw socket while wrapping, hiding it from a deadline watchdog.
            self.sock = self._context.wrap_socket(
                self.sock, server_hostname=self.host, do_handshake_on_connect=False)
            register_socket(self.sock)
            self.sock.settimeout(_remaining_time(deadline))
            self.sock.do_handshake()
            self.sock.settimeout(_remaining_time(deadline))

    cls = DeadlineHTTPSConnection if secure else http.client.HTTPConnection
    options = {"timeout": timeout}
    if secure:
        options["context"] = _tls_context(profile)
    connection = cls(host, port, **options)

    def create_connection(*args, **kwargs):
        connected = _connect_socket(addresses, _remaining_time(deadline))
        try:
            register_socket(connected)
            # TCP/DNS time must not be granted again to TLS or the HTTP response.
            connected.settimeout(_remaining_time(deadline))
            return connected
        except Exception:
            connected.close()
            raise

    connection._create_connection = create_connection
    return connection


def request_json(base, route, payload=None, timeout=30, api_key_env=None, *, profile=None):
    """One bounded HTTP request; no hidden HTTP/model/provider retries."""
    policy = copy.deepcopy(profile) if profile else {
        "base_url": base, "network_mode": "loopback", "enabled": True}
    if api_key_env:
        if policy.get("api_key_env") not in (None, api_key_env):
            raise ValueError("Authentication environment name differs from profile")
        policy["api_key_env"] = api_key_env
    configured_base = validate_endpoint(policy)
    allowed_bases = {configured_base}
    if policy.get("provider", "llama_cpp") == "llama_cpp" and configured_base.endswith("/v1"):
        allowed_bases.add(configured_base[:-3])
    if base.rstrip("/") not in allowed_bases:
        raise ValueError("Request base differs from configured model endpoint")
    routes = {model_route(policy, "models"), model_route(policy, "chat")}
    if policy.get("provider", "llama_cpp") == "llama_cpp":
        routes.update(("/props", "/apply-template", "/tokenize"))
    if route not in routes:
        raise ValueError("Unsupported model API route")
    if not policy.get("enabled", True):
        raise ProviderError("disabled", "Model profile is disabled")
    timeout = _number(timeout, "request timeout", 1, 600)
    env_name = policy.get("api_key_env")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if env_name:
        if not isinstance(env_name, str) or not ENV_RE.fullmatch(env_name):
            raise ValueError("api_key_env must be an environment variable name")
        secret = os.environ.get(env_name)
        if not secret:
            raise ProviderError("missing_auth", "Configured model authentication environment variable is not set")
        if any(character in secret for character in ("\r", "\n")):
            raise ProviderError("invalid_auth", "Configured model authentication contains invalid characters")
        headers["Authorization"] = "Bearer " + secret
    elif policy.get("api_key_credential"):
        if policy.get("api_key_env"):
            raise ValueError("Conflicting model authentication sources")
        headers["Authorization"] = "Bearer " + _credential_value(policy, required=True)
    body = None if payload is None else _json_bytes(payload)
    if body is not None and len(body) > MAX_REQUEST_BYTES:
        raise ProviderError("request_too_large", "Model request exceeds its size limit")
    target = urlsplit(base.rstrip("/") + route)
    host = _canonical_host(target.hostname)
    port = target.port or (443 if target.scheme == "https" else 80)
    started = time.monotonic()
    addresses = _resolve_addresses(host, port, policy.get("network_mode", "loopback"), timeout=timeout)
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise ProviderError("transport_failed", "Model request timed out")
    deadline = started + timeout
    expired = threading.Event()
    active_socket = {}
    def register_socket(connected):
        active_socket["value"] = connected
        if expired.is_set():
            abort_at_deadline()
            raise TimeoutError("Model request deadline exceeded")
    connection = _connection(host, port, target.scheme == "https", addresses, remaining,
                             deadline=deadline, register_socket=register_socket, profile=policy)
    def abort_at_deadline():
        expired.set()
        connection_socket = active_socket.get("value") or connection.sock
        if connection_socket is not None:
            try:
                connection_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection_socket.close()
            except OSError:
                pass
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        connection.close()
        raise ProviderError("transport_failed", "Model request timed out")
    watchdog = threading.Timer(remaining, abort_at_deadline)
    watchdog.daemon = True
    watchdog.start()
    try:
        connection.connect()
        active_socket["value"] = connection.sock
        if expired.is_set():
            raise TimeoutError
        connection.request("GET" if body is None else "POST", target.path, body=body, headers=headers)
        response = connection.getresponse()
        if response.status not in range(200, 300):
            # Never read/echo an upstream error body or follow a Location header.
            raise ProviderError("http_" + str(response.status),
                                "Model endpoint returned HTTP " + str(response.status))
        if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
            raise ProviderError("response_encoding", "Unsupported model response encoding")
        chunks, total = [], 0
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ProviderError("response_too_large", "Model response exceeds its size limit")
        try:
            value = json.loads(b"".join(chunks).decode("utf-8"),
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeError, ValueError):
            raise ProviderError("invalid_json", "Model endpoint returned invalid JSON") from None
        if not isinstance(value, dict):
            raise ProviderError("invalid_response", "Model endpoint returned a non-object response")
        return redact_secrets(value, policy)
    except ProviderError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException):
        raise ProviderError("transport_failed", "Model request failed or timed out") from None
    finally:
        watchdog.cancel()
        connection.close()


def list_models(profile, *, timeout=10):
    result = request_json(profile["base_url"], model_route(profile, "models"), timeout=timeout,
                          api_key_env=profile.get("api_key_env"), profile=profile)
    if profile.get("provider") == "ollama_native":
        models = result.get("models")
        if not isinstance(models, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("model", item.get("name")), str)
                or not item.get("model", item.get("name")) for item in models):
            raise ProviderError("invalid_models", "Model endpoint returned an invalid model list")
        result = {"data": [{"id": item.get("model", item.get("name"))} for item in models]}
    data = result.get("data")
    if not isinstance(data, list) or any(not isinstance(item, dict) or
                                       not isinstance(item.get("id"), str) for item in data):
        raise ProviderError("invalid_models", "Model endpoint returned an invalid model list")
    return result


def check_model(profile, *, timeout=10):
    if not profile.get("enabled", True):
        raise ProviderError("disabled", "Model profile is disabled")
    if profile.get("model_discovery") == "skip":
        # Some compatibility gateways expose chat but no matching model catalog.
        # The completion's actual model remains mandatory and is checked by llm.
        return {"data": [], "discovery": "skipped"}
    result = list_models(profile, timeout=timeout)
    if not any(model_matches(profile, item["id"]) for item in result["data"]):
        raise ProviderError("model_missing", "Configured model or explicit alias is not served")
    return result


def _text_and_images(messages):
    text, images = [], 0
    for message in messages:
        content = message.get("content", "")
        text.append(str(message.get("role", "")))
        if isinstance(content, str):
            text.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    raise ValueError("Unsupported model message content")
                if part.get("type") in ("image_url", "input_image", "video_url"):
                    images += 1
                elif isinstance(part.get("text"), str):
                    text.append(part["text"])
                else:
                    raise ValueError("Unsupported model message content")
        else:
            raise ValueError("Unsupported model message content")
    return "\n".join(text), images


def context_budget(profile, messages, max_tokens):
    """Exact llama.cpp text counts or a conservative UTF-8 and image allowance."""
    text, images = _text_and_images(messages)
    context = profile["context_tokens"]
    method = profile.get("context_validation", "llama_tokenize")
    if method == "llama_tokenize" and not images:
        if profile.get("provider", "llama_cpp") != "llama_cpp" or not profile["base_url"].endswith("/v1"):
            raise ValueError("llama_tokenize requires a llama.cpp /v1 endpoint")
        base = profile["base_url"][:-3]
        options = {"profile": profile, "timeout": min(30, profile.get("request_timeout_seconds", 480))}
        props = request_json(base, "/props", **options)
        actual = props.get("default_generation_settings", {}).get("n_ctx")
        if type(actual) is not int or actual < 1024:
            raise ProviderError("invalid_context", "Model server did not report a valid per-slot context length")
        rendered = request_json(base, "/apply-template", {
            "messages": messages,
            "chat_template_kwargs": profile.get("chat_template_kwargs", {"enable_thinking": False})
        }, **options)
        if not isinstance(rendered.get("prompt"), str):
            raise ProviderError("invalid_template", "Model server did not return a rendered chat prompt")
        tokens = request_json(base, "/tokenize", {"content": rendered["prompt"],
                              "add_special": False, "parse_special": True}, **options).get("tokens")
        if not isinstance(tokens, list) or not all(type(token) is int for token in tokens):
            raise ProviderError("invalid_tokens", "Model server returned an invalid token count")
        count, margin = len(tokens), 64
        available = min(context, actual)
        return {"method": "llama_tokenize", "prompt_tokens": count,
                "server_context_tokens": actual, "context_tokens": available,
                "max_tokens": max_tokens, "margin_tokens": margin,
                "fits": count + max_tokens + margin <= available}
    # One token per UTF-8 byte is intentionally conservative for unfamiliar tokenizers.
    # This is an estimate, not an exact count; model-specific image tokenization differs.
    count = len(text.encode("utf-8")) + len(messages) * 64
    image_tokens = images * profile.get("image_token_budget", 4096)
    margin = 256
    return {"method": "conservative_utf8_estimate", "prompt_tokens": count,
            "image_count": images, "reserved_image_tokens": image_tokens,
            "context_tokens": context, "max_tokens": max_tokens,
            "margin_tokens": margin,
            "fits": count + image_tokens + max_tokens + margin <= context}


def response_format(profile, schema, role):
    mode = profile.get("response_format", "llama_json_schema")
    if mode == "prompt":
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    if mode == "openai_json_schema":
        return {"type": "json_schema", "json_schema": {
            "name": "drama_" + re.sub(r"\W", "_", role)[:50], "strict": True, "schema": schema}}
    return {"type": "json_object", "schema": schema}


def _ollama_messages(messages):
    """Translate inline review images without fetching URLs or reading files."""
    translated = []
    for message in messages:
        content = message.get("content", "")
        row = {"role": message["role"], "content": content}
        if isinstance(content, list):
            text, images = [], []
            for part in content:
                if not isinstance(part, dict):
                    raise ValueError("Unsupported Ollama message content")
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    text.append(part["text"])
                elif part.get("type") == "image_url":
                    image = part.get("image_url")
                    url = image.get("url") if isinstance(image, dict) else image
                    match = re.fullmatch(
                        r"data:image/(?:png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/]+={0,2})",
                        url if isinstance(url, str) else "")
                    if not match:
                        raise ValueError("Ollama review images must use inline base64 image data")
                    encoded = match.group(1)
                    try:
                        base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError):
                        raise ValueError("Ollama review image contains invalid base64 data") from None
                    images.append(encoded)
                else:
                    raise ValueError("Unsupported Ollama message content")
            row["content"] = "\n".join(text)
            if images:
                row["images"] = images
        elif not isinstance(content, str):
            raise ValueError("Unsupported Ollama message content")
        translated.append(row)
    return translated


def chat_request(profile, messages, schema, role, parameters):
    """Build one protocol's request; never infer a different endpoint or model."""
    fmt = response_format(profile, schema, role)
    if profile.get("provider") == "ollama_native":
        options = {"num_predict": parameters["max_tokens"], "num_ctx": profile["context_tokens"]}
        if parameters["send_sampling_parameters"]:
            options.update(temperature=parameters["temperature"], top_p=parameters["top_p"])
        request = {"model": profile["model"], "messages": _ollama_messages(messages),
                   "stream": False, "options": options}
        if fmt is not None:
            request["format"] = ("json" if profile["response_format"] == "json_object" else schema)
        if "thinking" in profile:
            request["think"] = profile["thinking"]
        return model_route(profile, "chat"), request
    request = {"model": profile["model"], "messages": messages, "stream": False,
               parameters["max_tokens_field"]: parameters["max_tokens"]}
    if parameters["send_sampling_parameters"]:
        request.update(temperature=parameters["temperature"], top_p=parameters["top_p"])
    if fmt is not None:
        request["response_format"] = fmt
    if parameters["chat_template_kwargs"]:
        request["chat_template_kwargs"] = parameters["chat_template_kwargs"]
    if profile.get("provider") == "dashscope":
        request["enable_thinking"] = profile.get("thinking", False)
    return model_route(profile, "chat"), request


def chat_response(profile, result):
    """Normalize native Ollama telemetry while keeping the reported model intact."""
    if profile.get("provider") != "ollama_native":
        return result
    message = result.get("message")
    finish = result.get("done_reason") if result.get("done") is True else "incomplete"
    choices = [{"message": message, "finish_reason": finish}] if isinstance(message, dict) else []
    usage = {}
    for remote, field in (("prompt_eval_count", "prompt_tokens"), ("eval_count", "completion_tokens")):
        value = result.get(remote)
        if type(value) is int and value >= 0:
            usage[field] = value
    if len(usage) == 2:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    timings = {field: result[field] for field in (
        "total_duration", "load_duration", "prompt_eval_duration", "eval_duration")
        if type(result.get(field)) is int and result[field] >= 0}
    return {"model": result.get("model"), "choices": choices, "usage": usage, "timings": timings}


def validate_registry(profiles, roles, base_config=None):
    """Validate a complete registry without connecting to providers or changing files."""
    from .config import ROLE_LABELS, OPTIONAL_ROLES, resolve_llm
    if not isinstance(profiles, dict) or not 1 <= len(profiles) <= 32:
        raise ValueError("profiles must contain between 1 and 32 named connections")
    if not isinstance(roles, dict) or any(role not in ROLE_LABELS for role in roles):
        raise ValueError("roles contains an unsupported production role")
    normalized = {}
    for name, value in profiles.items():
        if not isinstance(name, str) or not ID_RE.fullmatch(name):
            raise ValueError("Model profile ids must use letters, numbers, dots, dashes, or underscores")
        normalized[name] = normalize_profile(value)
    for role, name in roles.items():
        if not isinstance(name, str) or name not in normalized:
            raise ValueError("A role references an unknown model profile")
        if not normalized[name]["enabled"]:
            raise ValueError("A role cannot reference a disabled model profile")
    result = copy.deepcopy(base_config or {"schema_version": 1})
    if result.get("schema_version") != 1:
        raise ValueError("Unsupported factory configuration schema")
    result["llm_profiles"], result["llm_roles"] = normalized, copy.deepcopy(roles)
    for role in roles:
        resolve_llm(result, role)
    # Unassigned roles retain the legacy default only when one is present and valid.
    if not (set(ROLE_LABELS) - OPTIONAL_ROLES).issubset(roles):
        if not isinstance(result.get("llm"), dict):
            raise ValueError("Assign every role or provide a valid legacy default LLM")
        legacy = normalize_profile(result["llm"], legacy=True)
        if not legacy["enabled"]:
            raise ValueError("The legacy default LLM must be enabled for unassigned roles")
        if "visual_critic" not in roles and not legacy["vision"]:
            raise ValueError("Unassigned visual_critic requires a vision-capable legacy default")
    return result


def public_inventory(config=None):
    from .config import load_config, list_roles
    from .video_providers import public_video_inventory
    current = load_config() if config is None else copy.deepcopy(config)
    normalized = {name: normalize_profile(profile)
                  for name, profile in current.get("llm_profiles", {}).items()}
    rows = []
    for name, profile in normalized.items():
        row = {"id": name, **redact_secrets(profile, profile),
               # Preserve the bearer-auth observation separately from TLS configuration.
               "transport_auth": "mtls" if profile.get("tls_client_cert_env") else "none",
               "tls_client_configured": bool(profile.get("tls_client_cert_env") and profile.get("tls_client_key_env")),
               "auth_configured": bool((profile.get("api_key_env") and os.environ.get(profile["api_key_env"]))
                                         or _credential_available(profile)),
               "roles": [role for role, selected in current.get("llm_roles", {}).items() if selected == name]}
        rows.append(row)
    return {**public_video_inventory(current), "revision": config_revision(current), "profiles": rows,
            "editable_profiles": {name: redact_secrets(profile, profile) for name, profile in normalized.items()},
            "role_assignments": copy.deepcopy(current.get("llm_roles", {})),
            "roles": list_roles(current),
            "providers": [{"id": name, **properties} for name, properties in PROVIDERS.items()],
            "capabilities": {"network_modes": ["loopback", "lan", "remote"],
                             "health_generates_content": False, "starts_model_processes": False,
                             "response_formats": ["llama_json_schema", "openai_json_schema", "json_object", "prompt"],
                             "model_discovery_modes": ["required", "skip"],
                             "configuration_effect": "Each execution batch keeps its snapshot until the next human gate; the next job reads the saved configuration."}}


def probe_profile(profile_id, config=None, timeout=8):
    from .config import load_config
    started = time.monotonic()
    current = load_config() if config is None else copy.deepcopy(config)
    if not isinstance(profile_id, str) or profile_id not in current.get("llm_profiles", {}):
        raise ValueError("Unknown model profile")
    profile = normalize_profile(current["llm_profiles"][profile_id])
    result = {"profile": profile_id, "status": "error", "models": [],
              "configured_model": profile["model"], "model_match": False, "generation": False,
              "model_discovery": profile["model_discovery"]}
    try:
        if not profile["enabled"]:
            result["status"] = "disabled"
        elif profile["model_discovery"] == "skip":
            result.update(status="unverified", message=(
                "Configuration is valid; endpoint connectivity and model identity have not been checked"))
        else:
            served = list_models(profile, timeout=max(1, min(15, timeout)))
            result["models"] = [item["id"] for item in served["data"]]
            result["model_match"] = any(model_matches(profile, actual) for actual in result["models"])
            if result["model_match"]:
                result["status"] = "ready"
            else:
                result["error"] = {"code": "model_missing", "message": "Configured model or explicit alias is not served"}
    except (ProviderError, ValueError) as exc:
        result["error"] = {"code": getattr(exc, "code", "invalid_profile"),
                           "message": redact_secrets(str(exc), profile)}
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return redact_secrets(result, profile)


def save_registry(profiles, roles, expected_revision, *, config_path=None,
                  video_profiles=None, video_roles=None):
    """Compare-and-swap registry update; running jobs retain their in-memory snapshot."""
    import fcntl
    from .config import config_read_path, config_write_path
    if not isinstance(expected_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
        raise ValueError("A configuration revision is required")
    target = Path(config_path) if config_path is not None else config_write_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / (target.name + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        source = target if target.exists() else (
            config_read_path() if config_path is None else target)
        current = json.loads(source.read_text(encoding="utf-8"))
        if config_revision(current) != expected_revision:
            raise ConfigConflict("Model configuration changed; reload before saving")
        updated = validate_registry(profiles, roles, current)
        if video_profiles is not None or video_roles is not None:
            from .video_providers import validate_video_registry
            updated = validate_video_registry(video_profiles, video_roles, updated)
        else:
            from .video_providers import video_registry
            video_registry(updated)
        data = (json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix="." + target.name + ".", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                os.fchmod(output.fileno(), (target.stat().st_mode & 0o777) if target.exists() else 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return public_inventory(updated)
