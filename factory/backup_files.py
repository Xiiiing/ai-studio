"""Bounded no-follow file access and exclusive publication for offline backups."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import stat

MAX_FILES = 100000
MAX_BYTES = 1024 ** 4
MAX_JSON = 16 * 1024 ** 2


class BackupError(ValueError):
    """An incomplete or unsafe snapshot must never be restored."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def checked_root(value):
    path = Path(value).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise BackupError("Symbolic links are not allowed in backup roots")
    return path.resolve()


def relative(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value or
            any(ord(c) < 32 or ord(c) == 127 for c in value) or
            any(part in {"", ".", ".."} for part in value.split("/"))):
        raise BackupError("Non-canonical snapshot path")
    return value


def private_name(path):
    name = Path(path).name.lower()
    return (name == ".env" or name.startswith(".env.") or name in {"runtime.env", "factory.json", "id_rsa", "id_ed25519"}
            or name.endswith((".pem", ".key", ".p12", ".pfx")))


@contextmanager
def open_source(root, name):
    parts = relative(name).split("/")
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise BackupError("Backup file confinement requires POSIX descriptor-relative access")
    directory = descriptor = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackupError("Only regular files can be backup evidence: " + name)
        stream = os.fdopen(descriptor, "rb"); descriptor = None
        with stream:
            yield stream
    except OSError as exc:
        raise BackupError("Missing, linked or inaccessible snapshot file: " + name) from exc
    finally:
        if descriptor is not None: os.close(descriptor)
        if directory is not None: os.close(directory)


def describe(root, name):
    with open_source(root, name) as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > MAX_BYTES:
            raise BackupError("File exceeds snapshot bounds")
        total, checksum = 0, hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            if total > MAX_BYTES: raise BackupError("File grew beyond snapshot bounds")
            checksum.update(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise BackupError("File changed while hashing: " + name)
    return {"path": name, "bytes": total, "sha256": checksum.hexdigest()}


def inventory(root, *, exclude_private=True):
    """Only production data; never traverse envs, models, private config or .git."""
    folder = Path(root) / "production"
    if folder.is_symlink(): raise BackupError("Linked production directory")
    if not folder.exists(): return [], []
    pending, files, excluded, count, total = [folder], [], [], 0, 0
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                count += 1
                if count > MAX_FILES: raise BackupError("Snapshot exceeds its file/directory count bound")
                name = Path(entry.path).relative_to(root).as_posix(); relative(name)
                if entry.is_symlink(): raise BackupError("Symbolic link in production: " + name)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    if private_name(name):
                        if not exclude_private: raise BackupError("Private credential/config file in snapshot: " + name)
                        excluded.append(name); continue
                    item = describe(root, name); total += item["bytes"]
                    if total > MAX_BYTES: raise BackupError("Snapshot exceeds its total byte bound")
                    files.append(item)
                else:
                    raise BackupError("Special files are not supported: " + name)
    return sorted(files, key=lambda v: v["path"]), sorted(excluded)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def write_new(path, content):
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    sync_directory(path.parent)


def write_json_new(path, value):
    write_new(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def copy_verified(source, target, receipt):
    name = relative(receipt["path"])
    destination = Path(target) / name
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with open_source(source, name) as stream:
        checksum, total = hashlib.sha256(), 0
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > receipt["bytes"]: raise BackupError("Source grew during copy: " + name)
                checksum.update(chunk); output.write(chunk)
            output.flush(); os.fsync(output.fileno())
        if total != receipt["bytes"] or checksum.hexdigest() != receipt["sha256"]:
            raise BackupError("Source checksum changed during copy: " + name)
    sync_directory(destination.parent)


def decode_json(raw):
    if len(raw) > MAX_JSON: raise BackupError("JSON evidence exceeds 16 MiB")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise BackupError("Duplicate JSON evidence member")
            result[key] = value
        return result
    def number(value):
        result = float(value)
        if not math.isfinite(result): raise BackupError("Non-finite JSON evidence")
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_float=number,
                           parse_constant=lambda _: (_ for _ in ()).throw(BackupError("Non-finite JSON evidence")))
        stack = [(value, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > 64: raise BackupError("JSON evidence nesting exceeds 64 levels")
            if isinstance(node, dict):
                for key, item in node.items(): key.encode("utf-8"); stack.append((item, depth + 1))
            elif isinstance(node, list): stack.extend((item, depth + 1) for item in node)
            elif isinstance(node, str): node.encode("utf-8")
        return value
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise BackupError("Malformed JSON evidence") from exc


def read_json(root, name):
    with open_source(root, name) as stream:
        return decode_json(stream.read(MAX_JSON + 1))
