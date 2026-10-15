#!/usr/bin/env python3
"""Authenticated LAN gateway for the isolated cami preview; no model access."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess

ROOT = Path(__file__).resolve().parents[1]
AREA = ROOT / "runtime/cami-preview/lan"
UNIT = "ai-studio-preview-lan.service"


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=True, **kwargs)


def endpoint(host, port):
    address = ipaddress.ip_address(host)
    if address.version != 4 or not any(address in ipaddress.ip_network(network)
            for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("Use an RFC1918 IPv4 LAN interface")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Port must be within 1024..65535")
    return f"{address}:{port}"


def caddyfile(host, port, upstream_port, password_hash):
    authority = endpoint(host, port)
    endpoint(host, upstream_port)
    upstream = f"127.0.0.1:{upstream_port}"
    # route preserves check order. Validate external headers before translating
    # authority; regex rewrites each matching Origin value, preserving duplicates
    # and unexpected values so the upstream can reject them as well.
    origin_pattern = "^https://" + host.replace(".", "[.]") + f":{port}$"
    return f"""{{
    admin off
    auto_https off
}}
https://:{port} {{
    bind {host}
    tls {AREA / (host + '-certificate.pem')} {AREA / (host + '-private-key.pem')}
    route {{
        @outside not remote_ip private_ranges
        respond @outside "Private network required" 403
        @wrong_host not header Host {authority}
        respond @wrong_host "Host rejected" 403
        @wrong_origin {{
            header Origin *
            not header Origin https://{authority}
        }}
        respond @wrong_origin "Origin rejected" 403
        @missing_origin {{
            not method GET HEAD
            not header Origin *
        }}
        respond @missing_origin "Origin required" 403
        @cross_site header Sec-Fetch-Site cross-site
        respond @cross_site "Cross-site request rejected" 403
        @same_site header Sec-Fetch-Site same-site
        respond @same_site "Cross-site request rejected" 403
        basic_auth {{
            preview {password_hash}
        }}
        reverse_proxy {upstream} {{
            header_up Host {upstream}
            header_up Origin "{origin_pattern}" "http://{upstream}"
            header_up -Authorization
        }}
    }}
}}
"""


def start(host, port):
    authority = endpoint(host, port)
    if any(c.isspace() or c in '%"\\' for c in str(ROOT)):
        raise ValueError("Unsupported systemd installation path")
    state = json.loads((ROOT / "runtime/cami-preview/deployed.json").read_text())
    upstream_port = state["port"]
    if port == upstream_port:
        raise ValueError("Gateway and backend must use separate ports")
    AREA.mkdir(parents=True, exist_ok=True, mode=0o700)
    AREA.chmod(0o700)
    caddy = shutil.which("caddy") or str(AREA / "bin/caddy")
    if not Path(caddy).is_file():
        raise RuntimeError("Install Caddy 2.8+ or place its binary at " + caddy)
    unit_path = Path.home() / ".config/systemd/user" / UNIT
    previous = unit_path.read_text() if unit_path.exists() else None
    if previous and str(AREA) not in previous:
        raise RuntimeError("An unrelated service owns the gateway unit")
    saved = AREA / "endpoint.json"
    old = json.loads(saved.read_text()) if saved.exists() else {}
    if old.get("authority") != authority:
        with socket.socket() as probe:
            probe.bind((host, port))
    credentials_path = AREA / "credentials.json"
    if not credentials_path.exists():
        credentials_path.write_text(json.dumps({"username": "preview", "password": secrets.token_urlsafe(18)}))
        credentials_path.chmod(0o600)
    credentials = json.loads(credentials_path.read_text())
    password_hash = run(caddy, "hash-password", input=credentials["password"] + "\n").stdout.strip()
    certificate = AREA / (host + "-certificate.pem")
    private_key = AREA / (host + "-private-key.pem")
    if old.get("host") == host and not certificate.exists() and (AREA / "certificate.pem").exists():
        shutil.copy2(AREA / "certificate.pem", certificate)
        shutil.copy2(AREA / "private-key.pem", private_key)
    if not certificate.exists():
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "90",
            "-subj", "/CN=" + host, "-addext", "subjectAltName=IP:" + host,
            "-keyout", str(private_key), "-out", str(certificate))
        private_key.chmod(0o600)
    config = AREA / "Caddyfile"
    old_config = config.read_text() if config.exists() else None
    staged = AREA / "Caddyfile.staged"
    staged.write_text(caddyfile(host, port, upstream_port, password_hash))
    staged.chmod(0o600)
    try:
        run(caddy, "validate", "--config", str(staged), "--adapter", "caddyfile")
        os.replace(staged, config)
    finally:
        staged.unlink(missing_ok=True)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(f"""[Unit]
Description=AI Studio authenticated LAN preview ({AREA})
After=network.target ai-studio-preview.service

[Service]
ExecStart={caddy} run --config {config} --adapter caddyfile
Environment=XDG_DATA_HOME={AREA / 'data'}
Environment=XDG_CONFIG_HOME={AREA / 'config'}
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
""")
    try:
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", UNIT)
        run("systemctl", "--user", "restart", UNIT)
        run("systemctl", "--user", "is-active", UNIT)
    except BaseException:
        if previous:
            unit_path.write_text(previous)
            if old_config is not None:
                config.write_text(old_config)
            run("systemctl", "--user", "daemon-reload")
            run("systemctl", "--user", "restart", UNIT)
        else:
            run("systemctl", "--user", "disable", "--now", UNIT)
            unit_path.unlink(missing_ok=True)
            run("systemctl", "--user", "daemon-reload")
        raise
    fingerprint = run("openssl", "x509", "-in", str(certificate), "-noout", "-fingerprint", "-sha256").stdout.strip()
    access = AREA / "ACCESS.txt"
    access.write_text(f"内网预览：https://{authority}/\n账号：{credentials['username']}\n密码：{credentials['password']}\n"
                      f"证书指纹：{fingerprint}\n示例数据；未接入 Wkp 模型，未启动 worker。\n")
    access.chmod(0o600)
    result = {"host": host, "authority": authority, "url": f"https://{authority}/",
              "access_file": str(access), "certificate": str(certificate), "fingerprint": fingerprint}
    saved.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--host", default="172.20.12.202")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    if args.action == "start":
        result = start(args.host, args.port)
    elif args.action == "stop":
        run("systemctl", "--user", "disable", "--now", UNIT)
        result = {"status": "stopped"}
    else:
        result = json.loads((AREA / "endpoint.json").read_text())
        status = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True)
        result["status"] = status.stdout.strip()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
