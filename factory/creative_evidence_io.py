"""Bounded local evidence IO for prompt catalogs and offline comparisons."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _parent(path):
    path = Path(path)
    if ".." in path.parts:
        raise ValueError("Evidence paths cannot contain parent traversal")
    path = Path(os.path.abspath(path))
    if any(not hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY")):
        raise ValueError("Safe local evidence IO requires POSIX no-follow support")
    parent = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
    except OSError as exc:
        os.close(parent)
        raise ValueError("Evidence path missing or contains a symlink") from exc
    return parent, path


def read_bytes(path, *, maximum=2 * 1024 * 1024):
    """Open every path component without following links; reject devices/FIFOs."""
    parent, path = _parent(path)
    try:
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError as exc:
            raise ValueError("Evidence file missing or contains a symlink") from exc
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise ValueError("Evidence file is not regular or exceeds the size bound")
            value = stream.read(maximum + 1)
            if len(value) > maximum:
                raise ValueError("Evidence file exceeds the size bound")
            return value
    finally:
        os.close(parent)


def read_json(path, *, maximum=2 * 1024 * 1024):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON member")
            result[key] = value
        return result
    try:
        value = json.loads(read_bytes(path, maximum=maximum), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        canonical(value)  # Reject float overflow and invalid Unicode as well.
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid or excessively nested JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Evidence JSON must be an object")
    return value


def new_directory(path):
    """Never overwrite an existing result; no production or symlink ancestors."""
    if ".." in Path(path).parts:
        raise ValueError("Evidence paths cannot contain parent traversal")
    path = Path(os.path.abspath(path))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("Output cannot contain a symlink")
        if component.name == "production":
            raise ValueError("Experiment output cannot be inside production")
    parent, path = _parent(path)
    try:
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
    finally:
        os.close(parent)
    return path


def write_new(path, value):
    """Exclusive, fsynced evidence file. Caller owns the isolated parent directory."""
    path = Path(path)
    raw = canonical(value) + b"\n"
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Evidence file exceeds the readable size bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    parent, path = _parent(path)
    try:
        descriptor = os.open(path.name, flags, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)
    return hashlib.sha256(raw).hexdigest()
