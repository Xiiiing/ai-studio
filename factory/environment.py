"""Load a small, private KEY=value file without shell evaluation or expansion."""
from __future__ import annotations
import os
from pathlib import Path
import re
import stat


def load_environment(path=None):
    # Do not import studio.core here: it resolves model/lock paths from the
    # environment during import, so the file must be loaded first.
    root = Path(__file__).resolve().parents[1]
    target = Path(path) if path is not None else root / "config/runtime.env"
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_size > 65536:
            raise ValueError("runtime.env must be a private regular file (chmod 600), at most 64 KiB")
        updates = {}
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if not match:
                raise ValueError(f"Invalid environment assignment at line {number}")
            key, value = match.groups()
            if value[:1] in {"'", '"'}:
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"Invalid environment quoting at line {number}")
                value = value[1:-1]
            if "\x00" in value:
                raise ValueError(f"Invalid environment value at line {number}")
            updates[key] = value
    for key, value in updates.items():
        os.environ.setdefault(key, value)
