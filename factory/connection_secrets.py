"""Private, atomic API-key storage shared by Web and worker processes.

Profiles contain opaque references only. Old references remain readable for
already-running configuration snapshots; the browser never receives key values.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar

PREFIX = 'STUDIO_KEY_'
REFERENCE = re.compile(r'^STUDIO_KEY_[0-9a-f]{32}$')
MAX_BYTES = 256 * 1024
_temporary = ContextVar("connection_test_keys", default={})


def vault_path():
    from .config import DATA
    return DATA / 'model-keys.json'


def read_vault():
    path = vault_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
                or metadata.st_uid != os.getuid() or metadata.st_size > MAX_BYTES):
            raise ValueError('Model key storage must be an owner-only regular file')
        value = json.load(stream)
    if not isinstance(value, dict) or any(not REFERENCE.fullmatch(k) or not isinstance(v,str) for k,v in value.items()):
        raise ValueError('Invalid model key storage')
    return value


def secret_value(name):
    if not isinstance(name,str):
        return None
    if name.startswith(PREFIX):
        if not REFERENCE.fullmatch(name):
            return None
        return _temporary.get().get(name) or read_vault().get(name)
    return os.environ.get(name)


def write_vault(value):
    path = vault_path()
    data = (json.dumps(value,ensure_ascii=False) + '\n').encode()
    if len(data) > MAX_BYTES:
        raise ValueError('Model key storage is full')
    path.parent.mkdir(parents=True,exist_ok=True)
    # Validate existing metadata, including rejecting symbolic links, before
    # replacing anything. Atomic replacement never follows the final path.
    read_vault()
    fd, temporary = tempfile.mkstemp(prefix='.model-keys-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o600)
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def prepare(updated, changes):
    """Validate a separate write-only envelope before mutating private files."""
    if changes is None:return {}
    if not isinstance(changes,dict) or len(changes)>96:
        raise ValueError('Invalid connection key updates')
    additions={}
    for connection,key in changes.items():
        if not isinstance(connection,str) or ':' not in connection:
            raise ValueError('Key update must identify its connection')
        kind,ident=connection.split(':',1)
        field={'llm':'llm_profiles','image':'image_profiles','audio':'audio_profiles','video':'video_profiles'}.get(kind)
        if field is None or ident not in updated.get(field,{}):
            raise ValueError('Key update refers to an unknown connection')
        profile=updated[field][ident]
        if not isinstance(profile,dict):raise ValueError('Invalid connection profile')
        if key is None:
            profile.pop('api_key_env',None);profile.pop('api_key_credential',None)
            continue
        if not isinstance(key,str) or not 1<=len(key)<=4096 or any(ord(c)<33 or ord(c)>126 for c in key):
            raise ValueError('API Key must be a nonempty token without spaces or control characters')
        reference=PREFIX+secrets.token_hex(16)
        profile.pop('api_key_credential',None)
        profile['api_key_env']=reference
        additions[reference]=key
    return additions


@contextmanager
def temporary_key(key):
    if key is None:
        yield None
        return
    container={'llm_profiles':{'probe':{}}}
    values=prepare(container,{'llm:probe':key})
    reference=container['llm_profiles']['probe'].get('api_key_env')
    token=_temporary.set({**_temporary.get(),**values})
    try:yield reference
    finally:_temporary.reset(token)
