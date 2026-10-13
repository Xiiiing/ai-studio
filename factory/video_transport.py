"""Bounded MiniMax API and artifact transport; API credentials never reach CDNs."""
from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from .providers import (MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, ProviderError, _canonical_host,
                        _connection, _json_bytes, _resolve_addresses, redact_secrets, validate_endpoint)


def _transfer(url, policy, headers, *, payload=None, destination=None, timeout=60, max_bytes=MAX_RESPONSE_BYTES):
    parsed = urlsplit(url)
    host = _canonical_host(parsed.hostname)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    timeout = max(.05, float(timeout))
    deadline = time.monotonic() + timeout
    addresses = _resolve_addresses(host, port, policy.get("network_mode", "remote"), timeout=timeout)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderError("transport_failed", "Video request timed out")
    active, expired = {}, threading.Event()
    def abort():
        expired.set()
        sock = active.get("socket")
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
    def register(sock):
        active["socket"] = sock
        if expired.is_set():
            abort()
            raise TimeoutError
    connection = _connection(host, port, parsed.scheme == "https", addresses, remaining,
                             deadline=deadline, register_socket=register)
    timer = threading.Timer(max(.001, deadline - time.monotonic()), abort)
    timer.daemon = True
    timer.start()
    temporary = Path(str(destination) + ".part") if destination else None
    output = None
    try:
        connection.connect()
        register(connection.sock)
        request_path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection.request("GET" if payload is None else "POST", request_path, body=payload, headers=headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise ProviderError("http_" + str(response.status), "Video endpoint returned HTTP " + str(response.status))
        if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
            raise ProviderError("response_encoding", "Unsupported video response encoding")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > max_bytes):
            raise ProviderError("response_too_large", "Video response exceeds its size limit")
        if temporary:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            output = temporary.open("wb")
        chunks, count = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or expired.is_set():
                raise TimeoutError
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, max_bytes + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            if count > max_bytes:
                raise ProviderError("response_too_large", "Video response exceeds its size limit")
            if output:
                output.write(chunk)
            else:
                chunks.append(chunk)
        if not count or (length is not None and count != int(length)):
            raise ProviderError("incomplete_response", "Video response is empty or incomplete")
        if output:
            output.flush()
            os.fsync(output.fileno())
            output.close()
            output = None
            os.replace(temporary, destination)
            return Path(destination)
        try:
            result = json.loads(b"".join(chunks).decode("utf-8"),
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError):
            raise ProviderError("invalid_json", "Video endpoint returned invalid JSON") from None
        if not isinstance(result, dict):
            raise ProviderError("invalid_response", "Video endpoint returned a non-object response")
        return redact_secrets(result, policy)
    except ProviderError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException):
        raise ProviderError("transport_failed", "Video request failed or timed out") from None
    finally:
        timer.cancel()
        connection.close()
        if output:
            output.close()
        if temporary:
            temporary.unlink(missing_ok=True)


def video_request(profile, route, *, payload=None, params=None, timeout=None):
    """Only the three fixed MiniMax operations are admitted, with literal query data."""
    base = validate_endpoint(profile)
    if profile.get("provider") != "minimax" or not profile.get("enabled"):
        raise ProviderError("disabled", "An enabled MiniMax profile is required")
    operations = {"/video_generation": None, "/query/video_generation": "task_id",
                  "/files/retrieve": "file_id"}
    if route not in operations:
        raise ValueError("Unsupported video API route")
    param = operations[route]
    if param is None:
        if not isinstance(payload, dict) or params:
            raise ValueError("Video submission requires a JSON object")
    elif payload is not None or not isinstance(params, dict) or set(params) != {param}:
        raise ValueError("Video query requires exactly " + param)
    if params and any(not isinstance(value, str) or not value or len(value) > 128
                      for value in params.values()):
        raise ValueError("Invalid video task or file identifier")
    secret = os.environ.get(profile.get("api_key_env", ""))
    if not secret or any(char in secret for char in ("\r", "\n")):
        raise ProviderError("missing_auth", "Video authentication environment variable is missing or invalid")
    body = None if payload is None else _json_bytes(payload)
    if body and len(body) > MAX_REQUEST_BYTES:
        raise ProviderError("request_too_large", "Video request exceeds its size limit")
    url = base + route + ("?" + urlencode(params) if params else "")
    result = _transfer(url, profile, {"Accept": "application/json", "Content-Type": "application/json",
                                     "Authorization": "Bearer " + secret}, payload=body,
                       timeout=timeout or profile["request_timeout_seconds"])
    business = result.get("base_resp")
    if not isinstance(business, dict) or type(business.get("status_code")) is not int:
        raise ProviderError("invalid_response", "Video response omitted its business status")
    if business["status_code"] != 0:
        raise ProviderError("api_" + str(business["status_code"]),
                            "Video service rejected the request (code " + str(business["status_code"]) + ")")
    return result


def download_video(profile, url, destination, *, timeout=None):
    """Fetch only the HTTPS/public artifact returned by the authenticated provider."""
    if not isinstance(url, str) or len(url) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ProviderError("invalid_download", "Invalid video download URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = _canonical_host(parsed.hostname)
    except (ValueError, TypeError):
        raise ProviderError("invalid_download", "Invalid video download URL") from None
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        raise ProviderError("invalid_download", "Invalid video download URL")
    # LAN/loopback are only for an explicitly configured private adapter/test gateway.
    mode = profile.get("network_mode", "remote")
    if parsed.scheme not in ("http", "https") or (mode == "remote" and parsed.scheme != "https"):
        raise ProviderError("invalid_download", "Remote video downloads require HTTPS")
    if port is not None and not 1 <= port <= 65535:
        raise ProviderError("invalid_download", "Invalid video download port")
    hosts = profile.get("download_hosts", [])
    if hosts and host not in hosts:
        raise ProviderError("download_host", "Video download host is not in download_hosts")
    policy = {"network_mode": mode}
    # No Authorization, cookies, signed URL persistence, redirects, or environment proxies.
    return _transfer(url, policy, {"Accept": "video/mp4,application/octet-stream"},
                     destination=destination, timeout=timeout or profile["request_timeout_seconds"],
                     max_bytes=profile["max_download_bytes"])
