"""Prepare a portable CPU/control environment; model weights remain separate."""
from __future__ import annotations
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-install", action="store_true", help="Only initialize missing example configuration")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11+ required; Python 3.12 is recommended.")
    config = ROOT / "config/factory.json"
    if not config.exists():
        shutil.copyfile(ROOT / "config/factory.example.json", config)
        config.chmod(0o600)
        print("Created config/factory.json from the portable example.")
    else:
        print("Existing config/factory.json retained.")
    if args.skip_install:
        return
    python = ROOT / "envs/factory/bin/python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / "envs/factory")], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements-factory.txt")], check=True)
    # Media helpers use the same CPU dependencies unless an existing media env is present.
    media = ROOT / ".venv"
    if not media.exists() and not media.is_symlink():
        media.symlink_to("envs/factory", target_is_directory=True)
    (ROOT / "runtime/factory").mkdir(parents=True, exist_ok=True)
    print("Ready: ./drama models ; ./drama workflow ; python -m factory.web")
    print("Start local model services separately, then configure their endpoint and model ID in the console.")


if __name__ == "__main__":
    main()
