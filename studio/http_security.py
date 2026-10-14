"""Loopback HTTP trust boundary. This is deliberately NOT internet authentication."""
from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
from urllib.parse import unquote, urlsplit

from .http_routes import invalid_identifier, match_route, routes

ERROR_CODES = {400: "invalid_request", 403: "access_denied", 404: "not_found",
               405: "method_not_allowed", 408: "request_timeout", 409: "conflict",
               413: "body_too_large", 414: "target_too_long", 415: "json_required",
               416: "invalid_range", 431: "headers_too_large", 500: "internal_error"}


class HTTPError(ValueError):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def validate_bind(host, port):
    """Fail before opening a socket or initializing a database. No unsafe override."""
    if not isinstance(host, str):
        raise ValueError("HTTP bind host must be a loopback address")
    try:
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise ValueError("Non-loopback HTTP binding is disabled; use an SSH tunnel or an authenticated proxy with a loopback upstream")
    if isinstance(port, str) and re.fullmatch(r"[0-9]{1,5}", port):
        port = int(port)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Invalid HTTP bind port")
    return ("127.0.0.1" if host == "localhost" else host), port


def checked_target(target):
    if len(target) > 4096:
        raise HTTPError(414, "target_too_long", "Request target is too long")
    if not target.startswith("/") or target.startswith("//") or "#" in target:
        raise HTTPError(400, "invalid_target", "An origin-form request target is required")
    if re.search(r"%(?![0-9a-fA-F]{2})", target):
        raise HTTPError(400, "invalid_target", "Invalid URL encoding")
    try:
        path = unquote(urlsplit(target).path, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeError):
        raise HTTPError(400, "invalid_target", "Invalid request path") from None
    if (any(ord(c) < 32 or ord(c) == 127 for c in target + path) or
            "\\" in path or "%" in path or "//" in path or
            any(part in (".", "..") for part in path.split("/"))):
        raise HTTPError(400, "invalid_target", "Non-canonical request path")
    return path


def check_origin(handler):
    """Exact local authority, no DNS lookup, no trust in proxy-supplied headers."""
    headers = handler.headers
    hosts = headers.get_all("Host", [])
    origins = headers.get_all("Origin", [])
    port = handler.server.server_port
    names = {"127.0.0.1", "localhost", "[::1]"}
    bound = handler.server.server_address[0]
    try:
        address = ipaddress.ip_address(bound)
        if address.is_loopback:
            names.add("[" + str(address) + "]" if address.version == 6 else str(address))
        if not ipaddress.ip_address(handler.client_address[0]).is_loopback:
            raise HTTPError(403, "peer_rejected", "Only loopback clients are admitted")
    except ValueError:
        raise HTTPError(403, "peer_rejected", "Only loopback clients are admitted") from None
    allowed = {f"{name}:{port}" for name in names}
    if port == 80:
        allowed.update(names)
    if len(hosts) != 1 or hosts[0].lower() not in allowed:
        raise HTTPError(403, "host_rejected", "Host rejected")
    if len(origins) > 1 or (origins and origins[0].lower() != "http://" + hosts[0].lower()):
        raise HTTPError(403, "origin_rejected", "Origin rejected")
    sites = headers.get_all("Sec-Fetch-Site", [])
    if len(sites) > 1:
        raise HTTPError(400, "invalid_header", "Duplicate fetch metadata")
    if sites and sites[0] in ("cross-site", "same-site"):
        raise HTTPError(403, "cross_site_rejected", "Cross-origin request rejected")
    if sites and sites[0] not in ("none", "same-origin"):
        raise HTTPError(400, "invalid_header", "Invalid fetch metadata")


class LocalHTTPBoundary:
    """Admission happens before method dispatch, including inherited legacy routes."""
    factory_routes = False
    request_timeout = 5

    def setup(self):
        super().setup()
        self.connection.settimeout(self.request_timeout)

    def parse_request(self):
        if not super().parse_request():
            return False
        try:
            check_origin(self)
            if self.headers.defects:
                raise HTTPError(400, "invalid_header", "Malformed headers")
            if sum(len(k) + len(v) for k, v in self.headers.items()) > 32768:
                raise HTTPError(431, "headers_too_large", "Headers exceed 32 KiB")
            for name, value in self.headers.items():
                if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or
                        any(ord(c) < 32 or ord(c) == 127 for c in value)):
                    raise HTTPError(400, "invalid_header", "Malformed header")
            for name in ("Content-Length", "Content-Type", "Range", "Content-Encoding", "Expect"):
                if len(self.headers.get_all(name, [])) > 1:
                    raise HTTPError(400, "invalid_header", "Duplicate framing or content header")
            if any(name in self.headers for name in ("Transfer-Encoding", "Content-Encoding", "Expect")):
                raise HTTPError(400, "unsupported_framing", "Transfer encoding, content encoding and Expect are not supported")
            # BaseHTTPRequestHandler normalizes a leading '//'; inspect the original too.
            target = self.raw_requestline.decode("iso-8859-1").split()[1]
            path = checked_target(target)
            if invalid_identifier(path, factory=self.factory_routes):
                raise HTTPError(400, "invalid_identifier", "Invalid resource identifier")
            route = match_route(self.command, path, factory=self.factory_routes)
            if route is None:
                known = any(re.fullmatch(r.pattern, path) for r in routes(self.factory_routes))
                raise HTTPError(405 if known else 404, "method_not_allowed" if known else "not_found", "Method not allowed" if known else "Not found")
            if self.command == "GET" and self.headers.get("Content-Length", "0") != "0":
                raise HTTPError(400, "unexpected_body", "GET request bodies are not supported")
            self.admitted_route = route
            return True
        except HTTPError as exc:
            self.close_connection = True
            self.json({"error": str(exc), "code": exc.code}, exc.status)
            return False

    def _local_request(self):
        """Compatibility for older local-only handlers; never the sole admission gate."""
        try:
            check_origin(self)
        except HTTPError as exc:
            return str(exc)
        return None

    def read_json_body(self):
        length = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,10}", length):
            raise HTTPError(400, "invalid_length", "A single decimal Content-Length is required")
        size = int(length)
        limit = self.admitted_route.body_limit
        if size > limit:
            raise HTTPError(413, "body_too_large", "JSON request body exceeds this route's size limit")
        if size == 0:
            raise HTTPError(400, "empty_body", "A JSON object is required")
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise HTTPError(415, "json_required", "JSON required")
        try:
            raw = self.rfile.read(size)
        except (TimeoutError, socket.timeout):
            raise HTTPError(408, "request_timeout", "Request body timed out") from None
        if len(raw) != size:
            raise HTTPError(400, "incomplete_body", "Incomplete request body")

        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise ValueError("Duplicate JSON member")
                value[key] = item
            return value

        def finite(value):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("Non-finite JSON number")
            return number

        def constant(value):
            raise ValueError("Non-standard JSON constant")

        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                               parse_float=finite, parse_constant=constant)
            if not isinstance(value, dict):
                raise ValueError("A JSON object is required")
            # Graph libraries may raise the interpreter recursion limit. The
            # HTTP contract must keep its own stable nesting bound regardless.
            pending = [(value, 0)]
            while pending:
                item, depth = pending.pop()
                if depth > 64:
                    raise ValueError("JSON nesting exceeds 64 levels")
                if isinstance(item, dict):
                    for key, child in item.items():
                        key.encode("utf-8")
                        pending.append((child, depth + 1))
                elif isinstance(item, list):
                    pending.extend((child, depth + 1) for child in item)
                elif isinstance(item, str):
                    item.encode("utf-8")  # Reject escaped unpaired surrogates.
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPError(400, "invalid_json", "A bounded finite UTF-8 JSON object with unique members is required") from None
        return value

    def json(self, obj, status=200):
        if status >= 400:
            obj = {**obj, "code": obj.get("code", ERROR_CODES.get(status, "request_failed")), "status": status}
            self.close_connection = True
        payload = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self.json({"error": "HTTP request rejected", "code": ERROR_CODES.get(code, "request_failed")}, code)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()
