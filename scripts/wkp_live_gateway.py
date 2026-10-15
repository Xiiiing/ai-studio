#!/usr/bin/env python3
"""One authenticated cami workbench entry; its control plane also runs on cami."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
AREA = ROOT / "runtime/wkp-live-gateway"
UNIT = "ai-studio-wkp-live.service"
WKP_HOST = "172.20.114.10"
WKP_PORT = 8790
WKP_FINGERPRINT = "953d3f0a15f0186e39abdc1b3d507baf48ad664ff522aba0f38ae821228452ac"


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=True, **kwargs)


def endpoint(host, port):
    address = ipaddress.ip_address(host)
    if address.version != 4 or not any(address in ipaddress.ip_network(network)
            for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("Use an RFC1918 IPv4 LAN interface")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Port must be within 1024..65535")
    if address == ipaddress.ip_address(WKP_HOST):
        raise ValueError("Run this gateway on cami, not on Wkp")
    return f"{address}:{port}"


def safe_path(path):
    """Private material may never follow a preexisting symlink."""
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise RuntimeError(f"Symlink not permitted: {candidate}")
    if path.exists() and path.stat().st_uid != os.getuid():
        raise RuntimeError(f"File belongs to another user: {path}")
    return path


def private_write(path, value):
    safe_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(value)


def read_credentials():
    path = safe_path(AREA / "credentials.json")
    if not path.exists():
        private_write(path, json.dumps({"username": "studio", "password": secrets.token_urlsafe(24)}))
    if path.stat().st_mode & 0o077:
        raise RuntimeError("Gateway credentials must have permissions 0600")
    credentials = json.loads(path.read_text())
    if credentials.get("username") != "studio" or not isinstance(credentials.get("password"), str) or len(credentials["password"]) < 24:
        raise RuntimeError("Malformed gateway credentials; refusing to replace them")
    return credentials


def verified_server_certificate(der):
    if hashlib.sha256(der).hexdigest() != WKP_FINGERPRINT:
        raise RuntimeError("Wkp TLS certificate differs from the handoff fingerprint; refusing to trust it")
    return ssl.DER_cert_to_PEM_cert(der)


def fetch_server_certificate():
    # This one handshake retrieves a public certificate only. Trust is granted
    # exclusively after matching the independently recorded handoff fingerprint.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((WKP_HOST, 8787), timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname=WKP_HOST) as secured:
            return verified_server_certificate(secured.getpeercert(binary_form=True))


def certificate_pair(certificate, key, common_name, extension):
    safe_path(certificate)
    safe_path(key)
    if certificate.exists() != key.exists():
        raise RuntimeError("Incomplete TLS pair; refusing to rotate existing identity")
    if not certificate.exists():
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "90",
            "-subj", "/CN=" + common_name, "-addext", extension,
            "-keyout", str(key), "-out", str(certificate))
        key.chmod(0o600)
        certificate.chmod(0o600)
    run("openssl", "x509", "-in", str(certificate), "-checkend", "3600", "-noout")


def metadata(host, port):
    authority = endpoint(host, port)
    return {"host": host, "port": port, "authority": authority, "url": f"https://{authority}/",
            "upstream": "http://127.0.0.1:8787", "access_file": str(AREA / "ACCESS.txt"),
            "client_certificate": str(AREA / "client-certificate.pem"),
            "server_fingerprint": WKP_FINGERPRINT,
            "client_fingerprint": hashlib.sha256(ssl.PEM_cert_to_DER_cert((AREA / "client-certificate.pem").read_text())).hexdigest()}


def prepare(host, port):
    endpoint(host, port)
    if any(c.isspace() or c in '%"\\' for c in str(ROOT)):
        raise ValueError("Unsupported systemd installation path")
    safe_path(AREA)
    AREA.mkdir(parents=True, exist_ok=True, mode=0o700)
    AREA.chmod(0o700)
    # Existing trust material permits the cami workbench to start even while
    # Wkp is offline. A first setup still verifies the independent fingerprint.
    trusted = safe_path(AREA / "wkp-server-certificate.pem")
    certificate = (verified_server_certificate(ssl.PEM_cert_to_DER_cert(trusted.read_text()))
                   if trusted.exists() else fetch_server_certificate())
    private_write(AREA / "wkp-server-certificate.pem", certificate)
    certificate_pair(AREA / "client-certificate.pem", AREA / "client-private-key.pem",
                     "ai-studio-cami-live", "extendedKeyUsage=clientAuth")
    certificate_pair(AREA / f"{host}-certificate.pem", AREA / f"{host}-private-key.pem",
                     host, "subjectAltName=IP:" + host)
    credentials = read_credentials()
    fingerprint = run("openssl", "x509", "-in", str(AREA / f"{host}-certificate.pem"),
                      "-noout", "-fingerprint", "-sha256").stdout.strip()
    private_write(AREA / "ACCESS.txt", f"真实生产工作台：https://{host}:{port}/\n账号：{credentials['username']}\n密码：{credentials['password']}\n"
                  f"本地入口证书：{fingerprint}\n工作流、项目数据与 worker 位于 cami；Wkp 只提供模型推理 API。\n")
    result = metadata(host, port)
    private_write(AREA / "prepared.json", json.dumps(result, indent=2))
    return result


def caddyfile(host, port, password_hash):
    authority = endpoint(host, port)
    upstream = "127.0.0.1:8787"
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
            studio {password_hash}
        }}
        @workbench path /
        handle @workbench {{
            header Cache-Control no-store
            header X-Studio-Frontend cami
            root * {ROOT / 'ui/factory/dist'}
            rewrite * /index.html
            file_server
        }}
        handle_path /factory/static/* {{
            root * {ROOT / 'ui/factory/dist'}
            file_server
        }}
        handle {{
            reverse_proxy http://{upstream} {{
                header_up Host {upstream}
                header_up Origin "{origin_pattern}" "https://{upstream}"
                header_up -Authorization
                transport http {{
                    network_proxy none
                    dial_timeout 10s
                }}
            }}
        }}
    }}
}}
"""


def unit_path():
    path = safe_path(Path.home() / ".config/systemd/user" / UNIT)
    if path.exists() and f"AI-STUDIO-WKP-LIVE:{AREA}" not in path.read_text():
        raise RuntimeError("An unrelated service owns the gateway unit")
    return path


def upstream_health():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:8787/api/health", timeout=15) as response:
        if response.status != 200:
            raise RuntimeError("cami control API is not healthy")
        observed = json.load(response)
        if observed.get("root") != str(ROOT):
            raise RuntimeError("The local API belongs to another installation")
        return observed


def start(host, port):
    path = unit_path()  # Reject foreign units before preparing or reading private material.
    prepare(host, port)
    upstream_health()  # Never replace a working local service with an unverified upstream.
    if not (ROOT / "ui/factory/dist/index.html").is_file():
        raise RuntimeError("Build the current frontend with npm run build before starting")
    caddy = shutil.which("caddy") or str(ROOT / "runtime/cami-preview/lan/bin/caddy")
    if not Path(caddy).is_file():
        raise RuntimeError("Install Caddy 2.11+; this script never downloads executables")
    old_unit = path.read_text() if path.exists() else None
    config = safe_path(AREA / "Caddyfile")
    old_config = config.read_text() if config.exists() else None
    was_active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT]).returncode == 0
    was_enabled = subprocess.run(["systemctl", "--user", "is-enabled", "--quiet", UNIT]).returncode == 0
    credentials = read_credentials()
    password_hash = run(caddy, "hash-password", input=credentials["password"] + "\n").stdout.strip()
    staged = safe_path(AREA / "Caddyfile.staged")
    private_write(staged, caddyfile(host, port, password_hash))
    try:
        run(caddy, "validate", "--config", str(staged), "--adapter", "caddyfile")
        os.replace(staged, config)
    finally:
        staged.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        private_write(path, f"""# AI-STUDIO-WKP-LIVE:{AREA}
[Unit]
Description=AI Studio cami workbench HTTPS entry
After=network.target

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
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", UNIT)
        run("systemctl", "--user", "restart", UNIT)
        run("systemctl", "--user", "is-active", UNIT)
    except BaseException:
        if old_config is not None:
            private_write(config, old_config)
        else:
            config.unlink(missing_ok=True)
        if old_unit is not None:
            private_write(path, old_unit)
        else:
            subprocess.run(["systemctl", "--user", "disable", "--now", UNIT], capture_output=True)
            path.unlink(missing_ok=True)
        run("systemctl", "--user", "daemon-reload")
        if old_unit is not None:
            run("systemctl", "--user", "enable" if was_enabled else "disable", UNIT)
            run("systemctl", "--user", "restart" if was_active else "stop", UNIT)
        raise
    result = metadata(host, port)
    private_write(AREA / "deployed.json", json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "start", "stop", "status"])
    parser.add_argument("--host", default="172.20.12.202")
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare(args.host, args.port)
    elif args.action == "start":
        result = start(args.host, args.port)
    elif args.action == "stop":
        unit_path()
        run("systemctl", "--user", "disable", "--now", UNIT)
        result = {"status": "stopped"}
    else:
        unit_path()
        result = json.loads(safe_path(AREA / "deployed.json").read_text())
        status = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True)
        result["status"] = status.stdout.strip()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
