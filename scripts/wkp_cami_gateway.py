#!/usr/bin/env python3
"""Install Wkp's separate mTLS-only gateway; trust a public cami client certificate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

ROOT = Path("/root/jialin/drama_studio")
AREA = ROOT / "runtime/cami-gateway"
NGINX = Path("/opt/ai-studio-lan/dist/usr/sbin/nginx")
UNIT = "ai-studio-cami-gateway.service"


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=30)


def configuration():
    return f"""user root;
worker_processes 1;
pid {AREA / 'nginx.pid'};
error_log {AREA / 'error.log'} warn;
events {{ worker_connections 256; }}
http {{
    access_log off;
    client_body_temp_path {AREA / 'body'};
    proxy_temp_path {AREA / 'proxy'};
    fastcgi_temp_path {AREA / 'fastcgi'};
    uwsgi_temp_path {AREA / 'uwsgi'};
    scgi_temp_path {AREA / 'scgi'};
    client_max_body_size 4m;
    map $http_origin $upstream_origin {{
        default invalid;
        "" "";
        "https://172.20.114.10:8790" "http://127.0.0.1:8787";
    }}
    map $http_sec_fetch_site $bad_site {{
        default 1;
        "" 0;
        none 0;
        same-origin 0;
    }}
    map "$request_method:$http_origin" $missing_origin {{
        default 0;
        ~^(POST|PUT|PATCH|DELETE):$ 1;
    }}
    server {{
        listen 172.20.114.10:8790 ssl;
        server_name _;
        allow 172.20.12.202;
        deny all;
        ssl_certificate /etc/ai-studio-lan/tls.crt;
        ssl_certificate_key /etc/ai-studio-lan/tls.key;
        ssl_client_certificate {AREA / 'cami-client.pem'};
        ssl_verify_client on;
        ssl_verify_depth 1;
        ssl_protocols TLSv1.2 TLSv1.3;
        if ($http_host != "172.20.114.10:8790") {{ return 403; }}
        if ($upstream_origin = invalid) {{ return 403; }}
        if ($bad_site = 1) {{ return 403; }}
        if ($missing_origin = 1) {{ return 403; }}
        location / {{
            proxy_pass http://127.0.0.1:8787;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host "127.0.0.1:8787";
            proxy_set_header Origin $upstream_origin;
            proxy_set_header Authorization "";
            proxy_read_timeout 60s;
        }}
    }}
}}
"""


def install(public_certificate, checksum):
    if os.geteuid() != 0:
        raise RuntimeError("Run on Wkp with authorized service-management access")
    if not re.fullmatch(r"[0-9a-f]{64}", checksum) or hashlib.sha256(public_certificate.encode()).hexdigest() != checksum:
        raise ValueError("Public client certificate checksum mismatch")
    if "PRIVATE KEY" in public_certificate or not public_certificate.startswith("-----BEGIN CERTIFICATE-----"):
        raise ValueError("Only a public certificate may be transferred")
    target = Path("/etc/systemd/system") / UNIT
    previous = target.read_text() if target.exists() else None
    if previous and str(AREA) not in previous:
        raise RuntimeError("An unrelated service owns the gateway unit")
    if not previous:
        with socket.socket() as probe:
            probe.bind(("172.20.114.10", 8790))
    AREA.mkdir(parents=True, exist_ok=True, mode=0o700)
    AREA.chmod(0o700)
    certificate = AREA / "cami-client.pem"
    if certificate.exists() and certificate.read_text() != public_certificate:
        raise RuntimeError("Existing client identity differs; certificate rotation must be explicit")
    certificate.write_text(public_certificate)
    certificate.chmod(0o600)
    run("openssl", "verify", "-purpose", "sslclient", "-CAfile", str(certificate), str(certificate))
    config = AREA / "nginx.conf"
    old_config = config.read_text() if config.exists() else None
    staged = AREA / "nginx.staged.conf"
    staged.write_text(configuration())
    staged.chmod(0o600)
    try:
        run(str(NGINX), "-t", "-p", str(AREA) + "/", "-c", str(staged))
        os.replace(staged, config)
    finally:
        staged.unlink(missing_ok=True)
    target.write_text(f"""[Unit]
Description=AI Studio mTLS gateway for cami ({AREA})
After=network.target drama-factory-web.service

[Service]
Type=simple
User=root
ExecStart={NGINX} -p {AREA}/ -c {config} -g 'daemon off;'
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
""")
    try:
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", UNIT)
        run("systemctl", "restart", UNIT)
        run("systemctl", "is-active", UNIT)
    except BaseException:
        if previous:
            target.write_text(previous)
            if old_config is not None:
                config.write_text(old_config)
            run("systemctl", "daemon-reload")
            run("systemctl", "restart", UNIT)
        else:
            run("systemctl", "disable", "--now", UNIT)
            target.unlink(missing_ok=True)
            run("systemctl", "daemon-reload")
        raise
    return {"service": UNIT, "url": "https://172.20.114.10:8790/", "client_certificate_sha256": checksum,
            "authentication": "mutual TLS only", "production_credentials_copied": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(install(sys.stdin.read(), args.client_sha256), indent=2))
