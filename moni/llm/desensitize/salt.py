"""Per-machine salt management for deterministic desensitization placeholders."""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

_SALT_LEN = 32
_SALT_ENV = "MONI_DESENSITIZE_SALT_PATH"
_SALT_LOCK = threading.Lock()


def salt_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Return the resolved path of the 32-byte HMAC salt file."""
    if path is not None:
        return Path(path).expanduser()
    env_path = os.environ.get(_SALT_ENV, "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".moni" / "desensitize.salt"


def load_salt(
    path: str | os.PathLike[str] | None = None,
    *,
    rotate: bool = False,
) -> bytes:
    """Load or create the 32-byte HMAC salt with 0700 dir and 0600 file perms."""
    target = salt_path(path)
    with _SALT_LOCK:
        if not rotate and target.is_file():
            existing = target.read_bytes()
            if len(existing) >= _SALT_LEN:
                return existing[:_SALT_LEN]

        key = secrets.token_bytes(_SALT_LEN)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)

        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        os.chmod(target, 0o600)
        return key
