"""Race-resistant, descriptor-relative HTTP media reads; never follow symlinks."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat

from .http_security import HTTPError


@contextmanager
def open_media(root, path):
    root, path = Path(root).absolute(), Path(path).absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise HTTPError(400, "invalid_media_path", "Media path is outside the allowed root") from None
    if not relative.parts or any(part in (".", "..") for part in relative.parts):
        raise HTTPError(400, "invalid_media_path", "Invalid media path")
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise HTTPError(503, "secure_media_unavailable", "Secure media reads require descriptor-relative file support")
    directory = descriptor = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HTTPError(400, "invalid_media_file", "Only regular media files may be served")
        source = os.fdopen(descriptor, "rb")
        descriptor = None
        with source:
            yield source
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise HTTPError(404, "media_not_found", "File not ready") from None
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EACCES):
            raise HTTPError(403, "media_path_rejected", "Symbolic links and inaccessible media paths are not served") from None
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)
