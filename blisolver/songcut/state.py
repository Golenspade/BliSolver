"""Atomic receipts, content identities, and process locks for resumable production."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

_PRIVATE = re.compile(
    r"authorization|api.?key|secret|password|signature|policy|access.?key|"
    r"(?:^|_)file_urls?$|transcription_url|upload_host|url$", re.IGNORECASE,
)


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def identity(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".songcut-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def redact(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if _PRIVATE.search(k) else redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if value.startswith("data:audio/"):
            return "[audio payload]"
        value = re.sub(r"(?:https?|oss)://[^\s\"<>]+", "[url]", value)
        value = re.sub(r"(?i)Bearer\s+\S+|\bsk-[\w-]+", "[credential]", value)
    return value


@contextmanager
def file_lock(path: Path):
    """Kernel lock releases on crash; no stale PID files or unsafe stale-lock deletion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("songcut output is already in use") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("songcut output is already in use") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def valid_artifacts(receipt: dict | None) -> bool:
    return bool(receipt and receipt.get("artifacts") and all(
        Path(p).is_file() and sha256(Path(p)) == digest
        for p, digest in receipt["artifacts"].items()
    ))
