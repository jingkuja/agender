from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class LockHeld(RuntimeError):
    pass


class FileLock:
    """Exclusive flock plus PID file. One daemon per profile/db."""

    def __init__(self, path: Path):
        self.path = path
        self._fd = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.path, "a+", encoding="utf-8")
        if fcntl is None:
            raise LockHeld("fcntl unavailable")
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fd.seek(0)
            meta = self._fd.read().strip()
            raise LockHeld(f"{self.path} held ({meta})") from exc
        self._fd.seek(0)
        self._fd.truncate()
        self._fd.write(json.dumps({"pid": os.getpid(), "started_at": time.time()}))
        self._fd.flush()

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        finally:
            self._fd.close()
            self._fd = None

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
