from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path


_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, threading.RLock())


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path_lock(path):
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
