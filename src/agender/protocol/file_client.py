from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FileClient:
    """Local directory queue used when AGENDER_SERVER_BASE_URL is unset."""

    def __init__(self, inbox_dir: Path, outbox_dir: Path):
        self.inbox = inbox_dir
        self.outbox = outbox_dir
        self.results = outbox_dir / "results"
        self.progress_path = outbox_dir / "progress.jsonl"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)
        self._leases: dict[str, Lease] = {}

    async def heartbeat(self, body: Heartbeat) -> HeartbeatAck:
        lease = None
        if body.status.value == "busy" and body.lease_id:
            until = _now() + timedelta(seconds=240)
            lease = Lease(lease_id=body.lease_id, lease_until=_iso(until), heartbeat_s=20)
            self._leases[body.lease_id] = lease
        return HeartbeatAck(
            schema_version=SCHEMA_VERSION,
            ok=True,
            server_time=_iso(_now()),
            lease=lease,
            stop=False,
        )

    async def claim(self, body: ClaimRequest) -> TaskClaim:
        files = sorted(self.inbox.glob("*.json"))
        for path in files:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(raw, dict):
                continue
            task_type = raw.get("type")
            if task_type and task_type not in body.capabilities:
                continue
            params = raw.get("params") or {}
            if isinstance(params, dict) and "password" in params:
                params = dict(params)
                params.pop("password", None)
                raw = dict(raw)
                raw["params"] = params
                path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            task = TaskEnvelope.model_validate(raw)
            claimed = self.inbox / f".claimed-{path.name}"
            path.replace(claimed)
            until = _now() + timedelta(seconds=240)
            lease = Lease(
                lease_id=f"lease_{task.task_id}_{task.attempt}",
                lease_until=_iso(until),
                heartbeat_s=20,
            )
            self._leases[lease.lease_id] = lease
            return TaskClaim(schema_version=SCHEMA_VERSION, task=task, lease=lease)
        return TaskClaim(schema_version=SCHEMA_VERSION, task=None, lease=None)

    async def progress(self, task_id: str, body: Progress) -> ProgressAck:
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        with self.progress_path.open("a", encoding="utf-8") as fh:
            fh.write(body.model_dump_json() + "\n")
        return ProgressAck(schema_version=SCHEMA_VERSION, ok=True, task_id=task_id, seq=body.seq)

    async def submit_result(self, body: TaskResult) -> ResultAck:
        dest = self.results / f"{body.task_id}.json"
        duplicate = dest.exists()
        payload = strip_secrets(body.model_dump(mode="json"))
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return ResultAck(
            schema_version=SCHEMA_VERSION,
            ok=True,
            task_id=body.task_id,
            accepted_status=body.status.value,
            duplicate=duplicate,
        )

    async def upload_artifact(self, meta: ArtifactMeta, blob: bytes) -> ArtifactUploadResponse:
        return ArtifactUploadResponse(
            schema_version=SCHEMA_VERSION,
            ok=True,
            artifact_id=meta.artifact_id,
            sha256=meta.sha256,
            stored=False,
        )

    async def aclose(self) -> None:
        return None
