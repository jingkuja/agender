from __future__ import annotations

import asyncio
import json
import random
import signal
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from agender import SCHEMA_VERSION
from agender.browser.lock import FileLock
from agender.browser.runtime import BrowserRuntime
from agender.config import AppConfig
from agender.obs import metrics
from agender.protocol.client import build_client
from agender.protocol.errors import LeaseLost, ProtocolError
from agender.protocol.redact import strip_secrets
from agender.protocol.schemas import (
    ClaimRequest,
    ErrorBody,
    Heartbeat,
    HeartbeatAck,
    HeartbeatMetrics,
    ResultStatus,
    SessionState,
    TaskEnvelope,
    TaskResult,
    TaskType,
    WorkerStatus,
)
from agender.store.artifacts import artifact_dir, write_bytes
from agender.store.db import Store
from agender.worker.heartbeat import capabilities
from agender.worker.router import Router
from agender.worker.status import evaluate_status

log = structlog.get_logger("agender.worker")


def _iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wall_clock(cfg: AppConfig, task: TaskEnvelope) -> int:
    defaults = {
        TaskType.WEB_RESEARCH.value: cfg.worker.task_wall_clock_s,
        TaskType.DOUYIN_VIDEO_COMMENTS.value: cfg.limits.douyin_bundle_wall_clock_s,
        TaskType.DOUYIN_VIDEO_BUNDLE.value: cfg.limits.douyin_bundle_wall_clock_s,
        TaskType.SESSION_LOGIN.value: cfg.limits.session_login_wall_clock_s,
    }
    soft = int((task.params or {}).get("timeout_s") or defaults.get(task.type, cfg.worker.task_wall_clock_s))
    return max(30, min(cfg.worker.task_hard_cap_s, soft))


class WorkerLoop:
    def __init__(self, cfg: AppConfig, *, runtime: BrowserRuntime | None = None, client=None, store: Store | None = None):
        self.cfg = cfg
        self.runtime = runtime
        self.client = client
        self.store = store
        self.stop = asyncio.Event()
        self._busy_task: TaskEnvelope | None = None
        self._lease_id: str | None = None
        self._lease_until: str | None = None
        self._started = time.time()
        self._locks: list[FileLock] = []
        self._seq = 0

    async def setup(self) -> None:
        if self.store is None:
            self.store = Store(self.cfg.path(self.cfg.store.sqlite_path))
            await self.store.open()
        db_lock = FileLock(self.cfg.path(self.cfg.store.sqlite_path + ".lock"))
        web_lock = FileLock(self.cfg.path(self.cfg.browser.profile_dir) / "web.lock")
        dy_lock = FileLock(self.cfg.path(self.cfg.browser.profile_dir) / "douyin.lock")
        for lk in (db_lock, web_lock, dy_lock):
            lk.acquire()
            self._locks.append(lk)
        if self.client is None:
            self.client = build_client(self.cfg)
        if self.runtime is None:
            self.runtime = BrowserRuntime(self.cfg)
        await self._recover()

    async def aclose(self) -> None:
        if self.runtime:
            await self.runtime.aclose()
        if self.client:
            await self.client.aclose()
        if self.store:
            await self.store.close()
        for lk in self._locks:
            lk.release()
        self._locks.clear()

    def request_stop(self) -> None:
        self.stop.set()

    async def _session(self) -> SessionState:
        if self.runtime is None:
            return SessionState.MISSING
        return await self.runtime.douyin_session()

    async def _caps(self, status: WorkerStatus | None = None):
        session = await self._session()
        ready = bool(self.runtime and self.runtime.browser_ready) or self.runtime is not None
        caps, disabled, derived = capabilities(self.cfg, browser_ready=ready, douyin_session=session)
        return caps, disabled, derived, session, ready

    async def _heartbeat(self, status: WorkerStatus) -> HeartbeatAck | None:
        caps, disabled, derived, session, ready = await self._caps(status)
        if status == WorkerStatus.IDLE and derived == WorkerStatus.DEGRADED:
            status = WorkerStatus.DEGRADED
        body = Heartbeat(
            worker_id=self.cfg.worker.id,
            status=status,
            current_task_id=self._busy_task.task_id if self._busy_task else None,
            lease_id=self._lease_id if self._busy_task else None,
            browser_ready=ready,
            sessions={"douyin": session, "web": SessionState.OK},
            capabilities=caps,
            disabled_capabilities=disabled,
            metrics=HeartbeatMetrics(
                uptime_s=int(time.time() - self._started),
                tasks_completed=await self.store.acked_count() if self.store else 0,
                blocked_rate_1h=await self.store.blocked_rate_1h() if self.store else 0.0,
            ),
        )
        try:
            ack = await self.client.heartbeat(body)
        except ProtocolError as exc:
            log.error("heartbeat_failed", code=exc.code, message=exc.message)
            if exc.code == "UNAUTHORIZED":
                self.stop.set()
            return None
        if status == WorkerStatus.BUSY:
            if ack.lease is None or not ack.lease.lease_until:
                return ack
            self._lease_until = ack.lease.lease_until
        if ack.stop:
            self.stop.set()
        return ack

    async def _recover(self) -> None:
        assert self.store
        rows = await self.store.unfinished()
        for row in rows:
            result_json = row["result_json"]
            task_id = row["task_id"]
            attempt = row["attempt"]
            if result_json:
                payload = json.loads(result_json)
                tr = TaskResult.model_validate(payload)
                try:
                    ack = await self.client.submit_result(tr)
                    if ack.ok:
                        await self.store.mark(row["id"], "acked", submitted_at=_iso())
                except LeaseLost:
                    await self.store.mark(row["id"], "superseded")
                continue
            tr = TaskResult(
                task_id=task_id,
                worker_id=self.cfg.worker.id,
                attempt=attempt,
                lease_id=row["lease_id"],
                status=ResultStatus.FAILED,
                started_at=row["started_at"],
                finished_at=_iso(),
                error=ErrorBody(code="WORKER_INTERRUPTED", message="worker restarted with no result", retryable=True),
            )
            await self.store.set_result(row["id"], tr.model_dump(mode="json"), "submitting", "WORKER_INTERRUPTED")
            try:
                ack = await self.client.submit_result(tr)
                await self.store.mark(row["id"], "interrupted" if ack.ok else "interrupted", submitted_at=_iso())
            except LeaseLost:
                await self.store.mark(row["id"], "superseded")

    async def run_forever(self) -> None:
        await self.setup()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except NotImplementedError:
                pass
        hb_task = asyncio.create_task(self._hb_loop())
        try:
            while not self.stop.is_set():
                await self._idle_once()
        finally:
            hb_task.cancel()
            await self.aclose()

    async def _hb_loop(self) -> None:
        while not self.stop.is_set():
            status = WorkerStatus.BUSY if self._busy_task else WorkerStatus.IDLE
            await self._heartbeat(status)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.cfg.worker.heartbeat_interval_s)
            except TimeoutError:
                continue

    async def _idle_once(self) -> None:
        caps, _, _, _, _ = await self._caps()
        try:
            claim = await self.client.claim(
                ClaimRequest(worker_id=self.cfg.worker.id, capabilities=caps, max_tasks=1)
            )
        except ProtocolError as exc:
            log.error("claim_failed", code=exc.code)
            if exc.code == "UNAUTHORIZED":
                self.stop.set()
            await asyncio.sleep(self.cfg.worker.poll_interval_s)
            return
        if claim.task is None:
            metrics.CLAIM_EMPTY.inc()
            delay = self.cfg.worker.poll_interval_s + random.uniform(0, self.cfg.worker.poll_jitter_s)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=delay)
            except TimeoutError:
                return
            return
        await self._run_claimed(claim.task, claim.lease)

    async def _run_claimed(self, task: TaskEnvelope, lease) -> None:
        self._busy_task = task
        self._lease_id = lease.lease_id if lease else None
        self._lease_until = lease.lease_until if lease else None
        assert self.store
        payload = strip_secrets({"type": task.type, "params": task.params, "task_id": task.task_id})
        run_id = await self.store.insert_run(
            task_id=task.task_id,
            task_type=task.type,
            attempt=task.attempt,
            lease_id=self._lease_id,
            lease_until=self._lease_until,
            payload=payload,
        )
        started = _iso()
        t0 = time.time()
        error_code = None
        result: dict[str, Any] | None = None
        status = ResultStatus.FAILED
        try:
            try:
                ttype = TaskType(task.type)
            except ValueError:
                error_code = "UNSUPPORTED_TYPE"
                raise _Done()
            major = (task.schema_version or SCHEMA_VERSION).split(".")[0]
            if major != SCHEMA_VERSION.split(".")[0]:
                error_code = "SCHEMA_MISMATCH"
                raise _Done()
            try:
                from agender.protocol.schemas import parse_params

                parse_params(ttype, task.params)
            except Exception as exc:
                error_code = "INVALID_PARAMS"
                result = {"kind": task.type, "message": str(exc)}
                raise _Done()
            soft = _wall_clock(self.cfg, task)
            deadline = time.monotonic() + soft
            session = await self._session()
            router = Router(self.cfg, self.runtime)
            result = await router.execute(ttype, task.params, session=session, deadline=deadline)
            error_code = (result or {}).pop("error_code", None)
            if time.monotonic() > deadline and not error_code:
                error_code = "DEADLINE_SOFT"
            status = evaluate_status(ttype, result, error_code)
        except _Done:
            status = evaluate_status(
                TaskType.WEB_SEARCH if error_code else TaskType.WEB_SEARCH,
                result,
                error_code,
            )
            if error_code == "UNSUPPORTED_TYPE":
                status = ResultStatus.FAILED
            if error_code == "SCHEMA_MISMATCH":
                status = ResultStatus.FAILED
            if error_code == "INVALID_PARAMS":
                status = ResultStatus.FAILED
        except asyncio.CancelledError:
            error_code = "WORKER_SHUTDOWN"
            status = ResultStatus.PARTIAL if result else ResultStatus.CANCELLED
            raise
        except LeaseLost:
            error_code = "LEASE_LOST"
            status = ResultStatus.PARTIAL if result else ResultStatus.FAILED
        except Exception as exc:
            log.exception("task_failed")
            error_code = "NAVIGATION_TIMEOUT"
            result = {"kind": task.type, "message": str(exc)}
            status = ResultStatus.FAILED
        finally:
            finished = _iso()
            duration_ms = int((time.time() - t0) * 1000)
            artifacts = []
            capture = (task.params or {}).get("capture_screenshot", "on_failure")
            if task.type == TaskType.SESSION_LOGIN.value:
                capture = "never"
            failed = status not in {ResultStatus.SUCCEEDED}
            if capture == "always" or (capture == "on_failure" and failed):
                artifacts = await self._maybe_screenshot(task.task_id, run_id)
            err = None
            if error_code:
                err = ErrorBody(
                    code=error_code,
                    message=error_code,
                    retryable=error_code in {"NAVIGATION_TIMEOUT", "EMPTY_RENDER", "RATE_LIMITED", "WORKER_INTERRUPTED", "COMMENT_LOAD_STALL"},
                )
            tr = TaskResult(
                task_id=task.task_id,
                worker_id=self.cfg.worker.id,
                attempt=task.attempt,
                lease_id=self._lease_id,
                status=status,
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                result=strip_secrets(result) if result else None,
                error=err,
                artifacts=artifacts,
            )
            await self.store.set_result(run_id, tr.model_dump(mode="json"), "submitting", error_code)
            try:
                ack = await self.client.submit_result(tr)
                await self.store.mark(run_id, "acked" if ack.ok else "acked", submitted_at=_iso())
            except LeaseLost:
                await self.store.mark(run_id, "superseded")
            except ProtocolError as exc:
                log.error("submit_failed", code=exc.code)
            metrics.TASKS_TOTAL.labels(type=task.type, status=status.value).inc()
            metrics.TASK_DURATION.labels(type=task.type, status=status.value).observe(duration_ms / 1000)
            if status == ResultStatus.SUCCEEDED:
                metrics.EXTRACT_SUCCESS.labels(type=task.type).inc()
                metrics.LAST_SUCCESS.labels(type=task.type).set(time.time())
            if status == ResultStatus.BLOCKED:
                site = "douyin" if task.type.startswith("douyin") else "web"
                metrics.BLOCKED_TOTAL.labels(site=site, code=error_code or "blocked").inc()
            self._busy_task = None
            self._lease_id = None
            self._lease_until = None

    async def _maybe_screenshot(self, task_id: str, run_id: str) -> list:
        if self.runtime is None or self.runtime.context is None:
            return []
        try:
            page = self.runtime.context.pages[0] if self.runtime.context.pages else None
            if page is None:
                return []
            png = await page.screenshot(type="png")
            dest = artifact_dir(self.cfg.path(self.cfg.store.artifact_dir), task_id) / "fail.png"
            digest, size = write_bytes(dest, png)
            art_id = await self.store.add_artifact(run_id, kind="screenshot", path=str(dest), sha256=digest, size=size, content_type="image/png")
            from agender.protocol.schemas import ArtifactRef

            return [ArtifactRef(artifact_id=art_id, kind="screenshot", path=str(dest), sha256=digest, bytes=size, content_type="image/png", uploaded=False)]
        except Exception:
            return []


class _Done(Exception):
    pass


async def run_one_file(cfg: AppConfig, path, runtime: BrowserRuntime | None = None) -> TaskResult:
    raw = json.loads(path.read_text(encoding="utf-8"))
    task = TaskEnvelope.model_validate(raw)
    loop = WorkerLoop(cfg, runtime=runtime)
    await loop.setup()
    try:
        from types import SimpleNamespace

        lease = SimpleNamespace(lease_id="local", lease_until=_iso())
        await loop._run_claimed(task, lease)
        # result already submitted via FileClient
        dest = cfg.path(cfg.worker.file_client.outbox_dir) / "results" / f"{task.task_id}.json"
        if dest.exists():
            return TaskResult.model_validate_json(dest.read_text(encoding="utf-8"))
        return TaskResult(
            task_id=task.task_id,
            worker_id=cfg.worker.id,
            status=ResultStatus.FAILED,
            started_at=_iso(),
            finished_at=_iso(),
            error=ErrorBody(code="UNAVAILABLE", message="no result written"),
        )
    finally:
        await loop.aclose()
