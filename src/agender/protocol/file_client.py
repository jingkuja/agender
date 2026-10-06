from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from agender import SCHEMA_VERSION
from agender.protocol.redact import strip_secrets
from agender.protocol.schemas import (
    ArtifactMeta,
    ArtifactUploadResponse,
    ClaimRequest,
    Heartbeat,
    HeartbeatAck,
    Lease,
    Progress,
    ProgressAck,
    ResultAck,
    TaskClaim,
    TaskEnvelope,
    TaskResult,
    TaskType,
)
from agender.store.atomic import atomic_json


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FileClient:
    """Single-consumer file transport. Worker owns the cross-process DB lock."""

    def __init__(self, inbox_dir: Path, outbox_dir: Path):
        self.inbox = inbox_dir
        self.outbox = outbox_dir
        self.results = outbox_dir / "results"
        self.progress_path = outbox_dir / "progress.jsonl"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)

    async def heartbeat(self, body: Heartbeat) -> HeartbeatAck:
        lease = None
        if body.status.value == "busy" and body.lease_id:
            lease = Lease(lease_id=body.lease_id, lease_until=_iso(_now() + timedelta(seconds=240)), heartbeat_s=20)
        return HeartbeatAck(server_time=_iso(_now()), lease=lease)

    def _invalid(self, path: Path) -> TaskEnvelope:
        # Do not persist malformed input: even unparsable text may contain credentials.
        identity = hashlib.sha256(path.name.encode()).hexdigest()[:20]
        task = TaskEnvelope(task_id=f"invalid-{identity}", type="__invalid__")
        atomic_json(self.inbox / "quarantine" / path.name, {"task_id": task.task_id, "error": "INVALID_PARAMS"})
        atomic_json(path, task.model_dump(mode="json"))
        return task

    async def claim(self, body: ClaimRequest) -> TaskClaim:
        known_types = {t.value for t in TaskType}
        for path in sorted(self.inbox.glob("*.json")):
            if path.name.startswith("."):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                task = TaskEnvelope.model_validate(raw)
            except (ValueError, TypeError, ValidationError):
                task = self._invalid(path)
            except OSError:
                continue
            # Known but disabled capabilities remain queued; unknown types receive a failure receipt.
            if task.type in known_types and task.type not in body.capabilities:
                continue
            safe = strip_secrets(task.model_dump(mode="json"))
            atomic_json(path, safe)
            if task.type != TaskType.SESSION_LOGIN.value:
                task.params = safe["params"]
            claimed = self.inbox / f".claimed-{path.name}"
            if claimed.exists():
                # Do not replace an earlier in-flight claim with a newly dropped file.
                claimed = self.inbox / f".claimed-{task.task_id}-{task.attempt}-{hashlib.sha256(path.name.encode()).hexdigest()[:12]}.json"
                if claimed.exists():
                    continue
            path.replace(claimed)
            lease = Lease(lease_id=f"lease_{task.task_id}_{task.attempt}", lease_until=_iso(_now() + timedelta(seconds=240)))
            return TaskClaim(task=task, lease=lease)
        return TaskClaim()

    async def claimed_tasks(self) -> list[TaskEnvelope]:
        tasks = []
        for path in sorted(self.inbox.glob(".claimed-*.json")):
            try:
                tasks.append(TaskEnvelope.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                self._invalid(path)
        return tasks

    def _finish_claim(self, task_id: str, attempt: int) -> None:
        for path in self.inbox.glob(".claimed-*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("task_id") == task_id and raw.get("attempt", 1) == attempt:
                    path.unlink()
            except (OSError, ValueError):
                continue

    async def progress(self, task_id: str, body: Progress) -> ProgressAck:
        with self.progress_path.open("a", encoding="utf-8") as fh:
            fh.write(body.model_dump_json() + "\n")
        return ProgressAck(task_id=task_id, seq=body.seq)

    async def submit_result(self, body: TaskResult) -> ResultAck:
        # Validate IDs before using them as path components (also used by direct callers).
        TaskEnvelope(task_id=body.task_id, type="result", attempt=body.attempt)
        dest = self.results / body.task_id / f"{body.attempt}.json"
        duplicate = dest.exists()
        if duplicate:
            saved = TaskResult.model_validate_json(dest.read_text(encoding="utf-8"))
        else:
            atomic_json(dest, strip_secrets(body.model_dump(mode="json")))
            saved = body
        # Maintain the legacy latest-result path without letting an old retry overwrite a newer attempt.
        latest = self.results / f"{body.task_id}.json"
        previous_attempt = 0
        if latest.exists():
            previous_attempt = TaskResult.model_validate_json(latest.read_text(encoding="utf-8")).attempt
        if saved.attempt >= previous_attempt:
            atomic_json(latest, strip_secrets(saved.model_dump(mode="json")))
        self._finish_claim(body.task_id, body.attempt)
        return ResultAck(task_id=body.task_id, accepted_status=saved.status.value, duplicate=duplicate)

    async def upload_artifact(self, meta: ArtifactMeta, blob: bytes) -> ArtifactUploadResponse:
        return ArtifactUploadResponse(schema_version=SCHEMA_VERSION, artifact_id=meta.artifact_id, sha256=meta.sha256, stored=False)

    async def aclose(self) -> None:
        pass
