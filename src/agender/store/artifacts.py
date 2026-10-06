from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path


def artifact_dir(base: Path, task_id: str) -> Path:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    dest = base / day / task_id
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def write_bytes(path: Path, data: bytes) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return digest, len(data)
