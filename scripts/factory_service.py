"""Install/control only the two project-owned factory systemd units."""
from __future__ import annotations

import argparse
import json
import os
import pwd
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
UNITS=["drama-factory-web.service","drama-factory-worker.service"]


def run(args,check=True):
    return subprocess.run(args,check=check,capture_output=True,text=True,timeout=90)


def validate_install_path(root):
    if any(ch.isspace() or ch in '%"\\' for ch in str(root)):
        raise ValueError("systemd 安装路径不能含空白、百分号、引号或反斜杠")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["install","start","stop","restart","status","rollback"])
    parser.add_argument("--user", help="install only: system user; defaults to the checkout owner")
    args=parser.parse_args()
    if args.action=="install":
        if os.geteuid()!=0:
            raise RuntimeError("安装项目服务需要 root")
        (ROOT/"runtime/factory").mkdir(parents=True,exist_ok=True)
        validate_install_path(ROOT)
        user=args.user or pwd.getpwuid(ROOT.stat().st_uid).pw_name
        pwd.getpwnam(user)
        rendered={}
        for unit in UNITS:
            source=ROOT/"deploy"/unit
            target=Path("/etc/systemd/system")/unit
            if target.exists() and str(ROOT) not in target.read_text():
                raise RuntimeError("同名服务不属于本项目，已保留")
            rendered[target]=source.read_text().replace("@PROJECT_ROOT@",str(ROOT)).replace("@SERVICE_USER@",user)
        backup=ROOT/"runtime/factory/service-backup"
        backup.mkdir(parents=True,exist_ok=True)
        for target,content in rendered.items():
            saved=backup/target.name
            if target.exists() and not saved.exists():
                shutil.copy2(target,saved)
            staged=target.with_suffix(".service.tmp")
            staged.write_text(content)
            staged.chmod(0o644)
            os.replace(staged,target)
        run(["systemctl","daemon-reload"])
        print("Installed project-owned factory services")
        return
    if args.action in {"start","restart"}:
        # The old dashboard launcher checks exact PID ownership and active work.
        if (ROOT/".venv/bin/python").exists() and (ROOT/"scripts/studio_service.py").exists():
            result=run([str(ROOT/".venv/bin/python"),str(ROOT/"scripts/studio_service.py"),"stop"],check=False)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        run(["systemctl","enable",*UNITS])
        run(["systemctl",args.action,*UNITS])
    elif args.action=="stop":
        run(["systemctl","stop",*UNITS])
    elif args.action=="rollback":
        run(["systemctl","disable","--now",*UNITS])
        run([str(ROOT/".venv/bin/python"),str(ROOT/"scripts/studio_service.py"),"start"])
        print("Restored the original dashboard at port 8787; factory data retained")
        return
    state={}
    for unit in UNITS:
        state[unit]=run(["systemctl","is-active",unit],check=False).stdout.strip()
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8787/api/health",timeout=4) as response:
            state["health"]=json.load(response)
    except Exception as exc:
        state["health"]={"error":str(exc)}
    print(json.dumps(state,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
