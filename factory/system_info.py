"""Small, read-only identity snapshot for system settings; no private config."""
from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import subprocess

from studio.core import ROOT
from . import __version__


def _source_commit(root):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=root,
            capture_output=True, text=True, check=True, timeout=2,
        ).stdout.strip()
        return result if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", result) else None
    except (OSError, subprocess.SubprocessError):
        return None


_STARTED_AT = datetime.now(timezone.utc).isoformat()
_SOURCE_COMMIT = _source_commit(ROOT)
_ENVIRONMENT = os.environ.get("DRAMA_STUDIO_ENVIRONMENT", "unspecified")


def public_system_info():
    return {
        "schema": "ai-studio.system.v1",
        "service": "drama-studio",
        "version": __version__,
        "source_commit": _SOURCE_COMMIT,
        "started_at": _STARTED_AT,
        "environment": _ENVIRONMENT if _ENVIRONMENT in {"stable", "test", "development"} else "unspecified",
    }
