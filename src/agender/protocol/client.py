from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import uuid4

import httpx

from agender import SCHEMA_VERSION, __version__
from agender.protocol.errors import LeaseLost, ProtocolError
from agender.protocol.schemas import (
    ArtifactMeta,
    ArtifactUploadResponse,
    ClaimRequest,
    Heartbeat,
    HeartbeatAck,
    Progress,
    ProgressAck,
    ResultAck,
    TaskClaim,
    TaskResult,
)


def _map_status(status: int, body: dict[str, Any] | None) -> ProtocolError:
    code = (body or {}).get("code")
    message = (body or {}).get("message") or f"HTTP {status}"
    retryable = bool((body or {}).get("retryable", status >= 500 or status == 429))
    if status in (401, 403):
        return ProtocolError("UNAUTHORIZED", message, retryable=False, http_status=status)
    if status == 409:
        return LeaseLost(message)
    if status == 413:
        return ProtocolError("PAYLOAD_TOO_LARGE", message, retryable=False, http_status=status)
    if status == 422:
        return ProtocolError("INVALID_PARAMS", message, retryable=False, http_status=status)
    if status == 429:
        return ProtocolError("RATE_LIMITED", message, retryable=True, http_status=status)
    if status >= 500:
        return ProtocolError(code or "UNAVAILABLE", message, retryable=True, http_status=status)
    return ProtocolError(code or "UNAVAILABLE", message, retryable=retryable, http_status=status)


class HttpClient:
    def __init__(self, base_url: str, token: str, worker_id: str, timeout_s: float = 30):
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            trust_env=False,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Worker-Id": worker_id,
                "X-Worker-Version": __version__,
                "Accept": "application/json",
            },
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency: str | None = None,
    ) -> dict[str, Any]:
        extra = dict(headers or {})
        if idempotency:
            extra["Idempotency-Key"] = idempotency
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                resp = await self._client.request(method, path, json=json_body, headers=extra)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == 4:
                    raise ProtocolError("UNAVAILABLE", type(exc).__name__, retryable=True) from exc
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
                continue
            if resp.status_code == 204:
                return {"schema_version": SCHEMA_VERSION, "task": None, "lease": None}
            parsed: dict[str, Any] | None = None
            if resp.content:
                try:
                    parsed = resp.json()
                except json.JSONDecodeError:
                    parsed = None
            if 200 <= resp.status_code < 300:
                if not isinstance(parsed, dict):
                    raise ProtocolError("INVALID_RESPONSE", "expected JSON object", retryable=True)
                return parsed
            err = _map_status(resp.status_code, parsed if isinstance(parsed, dict) else None)
            if resp.status_code == 413 and json_body and json_body.get("artifacts") and attempt < 4:
                json_body = {**json_body, "artifacts": []}
                continue
            if err.retryable and attempt < 4 and (resp.status_code >= 500 or resp.status_code == 429):
                wait = delay
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = float(retry_after)
                    except ValueError:
                        try:
                            wait = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                        except (TypeError, ValueError):
                            pass
                await asyncio.sleep(max(0, min(wait, 60)))
                delay = min(delay * 2, 8)
                last_exc = err
                continue
            raise err
        raise ProtocolError("UNAVAILABLE", str(last_exc), retryable=True)

    async def heartbeat(self, body: Heartbeat) -> HeartbeatAck:
        data = await self._request("POST", "/v1/heartbeat", json_body=body.model_dump(mode="json"))
        return HeartbeatAck.model_validate(data)

    async def claim(self, body: ClaimRequest) -> TaskClaim:
        data = await self._request("POST", "/v1/tasks/claim", json_body=body.model_dump(mode="json"), idempotency=f"claim:{body.worker_id}:{uuid4()}")
        return TaskClaim.model_validate(data)

    async def progress(self, task_id: str, body: Progress) -> ProgressAck:
        data = await self._request(
            "POST",
            f"/v1/tasks/{task_id}/progress",
            json_body=body.model_dump(mode="json"),
        )
        return ProgressAck.model_validate(data)

    async def submit_result(self, body: TaskResult) -> ResultAck:
        key = f"{body.task_id}:{body.attempt}"
        data = await self._request(
            "POST",
            f"/v1/tasks/{body.task_id}/result",
            json_body=body.model_dump(mode="json"),
            idempotency=key,
        )
        return ResultAck.model_validate(data)

    async def upload_artifact(self, meta: ArtifactMeta, blob: bytes) -> ArtifactUploadResponse:
        return ArtifactUploadResponse(
            schema_version=SCHEMA_VERSION,
            ok=True,
            artifact_id=meta.artifact_id,
            sha256=meta.sha256,
            stored=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_client(cfg) -> HttpClient | Any:
    from agender.protocol.file_client import FileClient

    if cfg.uses_http:
        if not cfg.token:
            raise ProtocolError("UNAUTHORIZED", "AGENDER_WORKER_TOKEN required for HTTP mode")
        return HttpClient(cfg.server.base_url, cfg.token, cfg.worker.id, cfg.server.timeout_s)
    return FileClient(cfg.path(cfg.worker.file_client.inbox_dir), cfg.path(cfg.worker.file_client.outbox_dir))
