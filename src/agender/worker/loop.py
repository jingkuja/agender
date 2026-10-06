from __future__ import annotations

import asyncio
import contextlib
import random
import signal
import time
from datetime import UTC, datetime, timedelta

import structlog
from pydantic import ValidationError

from agender import SCHEMA_VERSION
from agender.browser.lock import FileLock
from agender.browser.runtime import BrowserRuntime
from agender.config import AppConfig
from agender.obs import metrics
from agender.platforms import SESSION_SITES, task_site
from agender.protocol.client import build_client
from agender.protocol.errors import LeaseLost, ProtocolError
from agender.protocol.redact import strip_secrets
from agender.protocol.schemas import (
    ArtifactRef,
    ClaimRequest,
    ErrorBody,
    Heartbeat,
    HeartbeatAck,
    HeartbeatMetrics,
    Lease,
    Progress,
    ResultStatus,
    SessionState,
    TaskEnvelope,
    TaskResult,
    TaskType,
    WorkerStatus,
    parse_params,
)
from agender.store.artifacts import artifact_dir, write_bytes
from agender.store.db import Store
from agender.worker.execution import ExecutionContext, ExecutionStopped, remaining_seconds
from agender.worker.heartbeat import capabilities
from agender.worker.router import Router

log = structlog.get_logger("agender.worker")


def _iso() -> str:
    return datetime.now(UTC).isoformat()


def _payload(task, result):
    result = strip_secrets(result)
    result.pop("error_code", None)
    if task.type in {"douyin.video_comments", "douyin.video_bundle"} and "video_id" in result:
        result = {"video": result, "comments": [], "returned": 0, "exhausted": False}
    result["kind"] = task.type
    return result


def _wall_clock(cfg: AppConfig, task: TaskEnvelope) -> float:
    defaults = {
        TaskType.DOUYIN_VIDEO_COMMENTS.value: cfg.limits.douyin_bundle_wall_clock_s,
        TaskType.DOUYIN_VIDEO_BUNDLE.value: cfg.limits.douyin_bundle_wall_clock_s,
        TaskType.SESSION_LOGIN.value: cfg.limits.session_login_wall_clock_s,
    }
    default = defaults.get(task.type, cfg.worker.task_wall_clock_s)
    requested = (task.params or {}).get("timeout_s")
    budget = min(default, max(30, float(requested)) if requested is not None else default, cfg.worker.task_hard_cap_s)
    if task.deadline_at:
        budget = min(budget, remaining_seconds(task.deadline_at))
    return max(0.0, budget)


class WorkerLoop:
    def __init__(self, cfg: AppConfig, *, runtime: BrowserRuntime | None = None, client=None, store: Store | None = None):
        self.cfg, self.runtime, self.client, self.store = cfg, runtime, client, store
        self.stop = asyncio.Event()
        self._busy_task: TaskEnvelope | None = None
        self._lease_id: str | None = None
        self._lease_until: str | None = None
        self._started = time.monotonic()
        self._locks: list[FileLock] = []
        self._seq = 0
        self.execution: ExecutionContext | None = None
        self._revoked: set[str] = set()
        self._next_submit = 0.0
        self._submit_failures = 0
        self._run_id: str | None = None
        self.content = None
        self.sessions: dict[str, SessionState] = {}

    def initial_site(self) -> str:
        return next((site for site in SESSION_SITES if getattr(self.cfg.tools, site)), "web")

    async def setup(self, *, recover: bool = True) -> None:
        try:
            lock = FileLock(self.cfg.path(self.cfg.store.sqlite_path + ".lock"))
            lock.acquire()
            self._locks.append(lock)
            if self.store is None:
                self.store = Store(self.cfg.path(self.cfg.store.sqlite_path))
                await self.store.open()
            if self.client is None:
                self.client = build_client(self.cfg)
            if self.runtime is None:
                self.runtime = BrowserRuntime(self.cfg)
            if self.cfg.store.content_enabled:
                from agender.store.content import ContentStore
                self.content = ContentStore(self.cfg.path(self.cfg.store.content_path))
                await self.content.open()
            # Initialize locally; no target-site navigation is performed here.
            if isinstance(self.runtime, BrowserRuntime):
                await self.runtime.start(self.initial_site())
            if recover:
                await self._recover_file_claims()
                await self._recover()
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        try:
            for resource in (self.runtime, self.client):
                if resource:
                    with contextlib.suppress(Exception):
                        await resource.aclose()
            if self.content:
                await self.content.close()
            if self.store:
                await self.store.close()
        finally:
            for lock in reversed(self._locks):
                lock.release()
            self._locks.clear()

    def request_stop(self) -> None:
        self.stop.set()
        if self.execution:
            self.execution.cancel("WORKER_SHUTDOWN")

    async def _session(self) -> SessionState:
        return await self.runtime.douyin_session() if self.runtime else SessionState.MISSING

    async def _caps(self, status: WorkerStatus | None = None):
        session = await self._session()
        ready = bool(self.runtime and self.runtime.browser_ready)
        metrics.BROWSER_UP.set(int(ready))
        self.sessions = {"douyin": session, "web": SessionState.OK if ready else SessionState.UNKNOWN}
        if isinstance(self.runtime, BrowserRuntime):
            for site in SESSION_SITES:
                if site != "douyin" and getattr(self.cfg.tools, site):
                    self.sessions[site] = await self.runtime.session(site)
        caps, disabled, derived = capabilities(self.cfg, browser_ready=ready, douyin_session=session, sessions=self.sessions)
        health = getattr(self.runtime, "health", None)
        if health:
            available = []
            for cap in caps:
                if health.available(task_site(cap)) or cap == "session.login":
                    available.append(cap)
                else:
                    disabled.append(cap)
            caps = available
            if disabled:
                derived = WorkerStatus.DEGRADED
        return caps, disabled, derived, session, ready

    def _lose_lease(self, *, confirmed: bool = False) -> None:
        if confirmed and self._lease_id:
            self._revoked.add(self._lease_id)
        if self.execution:
            self.execution.cancel("LEASE_LOST")

    async def _heartbeat(self, status: WorkerStatus) -> HeartbeatAck | None:
        caps, disabled, derived, session, ready = await self._caps()
        if status == WorkerStatus.IDLE:
            status = derived
        if status == WorkerStatus.BUSY and not self._lease_id:
            status = WorkerStatus.DEGRADED
        body = Heartbeat(
            worker_id=self.cfg.worker.id, status=status,
            current_task_id=self._busy_task.task_id if self._busy_task else None,
            lease_id=self._lease_id, browser_ready=ready,
            sessions=self.sessions,
            capabilities=caps, disabled_capabilities=disabled,
            metrics=HeartbeatMetrics(uptime_s=int(time.monotonic() - self._started),
                                     tasks_completed=await self.store.acked_count() if self.store else 0,
                                     blocked_rate_1h=await self.store.blocked_rate_1h() if self.store else 0),
        )
        # Capture identity: a slow heartbeat must never renew/cancel a later task.
        identity = (body.current_task_id, body.lease_id)
        try:
            ack = await self.client.heartbeat(body)
        except ProtocolError as exc:
            log.warning("heartbeat_failed", code=exc.code)
            if exc.code == "UNAUTHORIZED":
                self.request_stop()
            if exc.code == "LEASE_LOST" and identity == (self._busy_task.task_id if self._busy_task else None, self._lease_id):
                self._lose_lease(confirmed=True)
            return None
        if ack.stop:
            self.request_stop()
        if status == WorkerStatus.BUSY and self._busy_task and identity == (self._busy_task.task_id, self._lease_id):
            lease = ack.lease
            try:
                valid = ack.ok and lease and lease.lease_id == self._lease_id and remaining_seconds(lease.lease_until) > 0
            except ValueError:
                valid = False
            if not valid:
                self._lose_lease()
            else:
                self._lease_until = lease.lease_until
                if self.execution:
                    self.execution.renew(lease.lease_until)
                if self.store:
                    await self.store.renew_lease(self._busy_task.task_id, self._busy_task.attempt, lease.lease_until)
        return ack

    async def _recover_file_claims(self) -> None:
        if not hasattr(self.client, "claimed_tasks"):
            return
        for task in await self.client.claimed_tasks():
            row = await self.store.get_run(task.task_id, task.attempt)
            if row is None:
                # The file was claimed but execution was never durably started.
                await self.store.insert_run(task_id=task.task_id, task_type=task.type, attempt=task.attempt,
                                            lease_id=f"lease_{task.task_id}_{task.attempt}", lease_until=None,
                                            payload=strip_secrets(task.model_dump(mode="json")))
            elif row["status"] in {"acked", "interrupted", "superseded"}:
                self.client._finish_claim(task.task_id, task.attempt)

    async def _deliver(self, run_id: str, result: TaskResult) -> bool:
        if result.lease_id and result.lease_id in self._revoked:
            await self.store.mark(run_id, "superseded")
            return True
        try:
            try:
                ack = await self._network(self.client.submit_result(result))
            except TimeoutError:
                raise ProtocolError("UNAVAILABLE", "result submission timed out", retryable=True)
            except ValidationError:
                raise ProtocolError("INVALID_ACK", "malformed result acknowledgement", retryable=False)
            if not ack.ok:
                raise ProtocolError("RESULT_NOT_ACCEPTED", "result not acknowledged", retryable=True)
            if ack.task_id != result.task_id or ack.accepted_status != result.status.value or ack.schema_version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
                raise ProtocolError("INVALID_ACK", "ack does not match submitted result", retryable=False)
            await self.store.mark(run_id, "acked", submitted_at=_iso())
            self._submit_failures = 0
            self._next_submit = 0
            return True
        except LeaseLost:
            await self.store.mark(run_id, "superseded")
            return True
        except ProtocolError as exc:
            log.warning("submit_pending", code=exc.code, task_id=result.task_id, attempt=result.attempt)
            metrics.SUBMIT_RETRY.labels(code=exc.code).inc()
            await self.store.add_event(run_id, "warning", "submit_pending", {"code": exc.code})
            if not exc.retryable:
                self.request_stop()  # preserve the outbox for operator repair
            self._submit_failures += 1
            self._next_submit = time.monotonic() + min(60, self.cfg.worker.submit_retry_s * 2 ** min(self._submit_failures - 1, 5))
            return False

    async def _network(self, operation):
        """Bound network waits even when stop arrives after a request started."""
        request = asyncio.create_task(operation)
        stopping = asyncio.create_task(self.stop.wait())
        try:
            done, _ = await asyncio.wait({request, stopping}, timeout=self.cfg.server.timeout_s,
                                         return_when=asyncio.FIRST_COMPLETED)
            if request in done:
                return await request
            if stopping in done:
                return await asyncio.wait_for(request, self.cfg.worker.shutdown_grace_s)
            raise TimeoutError()
        finally:
            for pending in (request, stopping):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(request, stopping, return_exceptions=True)

    def _clear_busy(self) -> None:
        self._busy_task = None
        self._lease_id = self._lease_until = None
        self._run_id = None

    async def _recover(self) -> bool:
        """Replay results, never browser operations. False means claims must wait."""
        for row in await self.store.unfinished():
            self._busy_task = TaskEnvelope(task_id=row["task_id"], type=row["task_type"], attempt=row["attempt"])
            self._lease_id, self._lease_until = row["lease_id"], row["lease_until"]
            if row["result_json"]:
                result = TaskResult.model_validate_json(row["result_json"])
                if row["status"] == "running":
                    result.finished_at = _iso()
                    await self.store.set_result(row["id"], result.model_dump(mode="json"), "submitting", "WORKER_INTERRUPTED")
            else:
                result = TaskResult(task_id=row["task_id"], worker_id=self.cfg.worker.id, attempt=row["attempt"],
                                    lease_id=row["lease_id"], status=ResultStatus.FAILED, started_at=row["started_at"],
                                    finished_at=_iso(), error=ErrorBody(code="WORKER_INTERRUPTED", message="worker interrupted", retryable=True))
                await self.store.set_result(row["id"], result.model_dump(mode="json"), "submitting", "WORKER_INTERRUPTED")
            if time.monotonic() < self._next_submit or not await self._deliver(row["id"], result):
                return False
        self._clear_busy()
        return True

    async def run_forever(self) -> None:
        await self.setup()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.request_stop)
        hb = asyncio.create_task(self._hb_loop())
        try:
            while not self.stop.is_set():
                await self._idle_once()
        finally:
            hb.cancel()
            await asyncio.gather(hb, return_exceptions=True)
            await self.aclose()

    async def _pause(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=max(0.01, seconds))

    async def _hb_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await self._heartbeat(WorkerStatus.BUSY if self._busy_task else WorkerStatus.IDLE)
            except Exception:
                log.error("heartbeat_invalid_response")
                self._lose_lease()
            await self._pause(self.cfg.worker.heartbeat_interval_s)

    async def _idle_once(self) -> None:
        if not await self._recover():
            await self._pause(max(0.01, self._next_submit - time.monotonic()))
            return
        if self.stop.is_set():
            return
        if isinstance(self.runtime, BrowserRuntime) and not self.runtime.browser_ready:
            try:
                await self.runtime.start(self.initial_site())
            except Exception:
                log.warning("browser_restart_failed")
                await self._pause(self.cfg.worker.poll_interval_s)
                return
        caps, _, _, _, _ = await self._caps()
        try:
            claim = await self._network(self.client.claim(ClaimRequest(worker_id=self.cfg.worker.id, capabilities=caps)))
        except (ProtocolError, ValidationError, TimeoutError) as exc:
            code = getattr(exc, "code", "INVALID_RESPONSE")
            log.warning("claim_failed", code=code)
            if code == "UNAUTHORIZED":
                self.request_stop()
            await self._pause(self.cfg.worker.poll_interval_s)
            return
        if claim.task is None:
            metrics.CLAIM_EMPTY.inc()
            await self._pause(self.cfg.worker.poll_interval_s + random.uniform(0, self.cfg.worker.poll_jitter_s))
            return
        await self._run_claimed(claim.task, claim.lease)

    async def _progress(self, stage: str, counts: dict[str, int]) -> None:
        if not self._busy_task or not self._lease_id:
            return
        self._seq += 1
        if self.content and self.execution and self.execution.result:
            await self._save_content(self._busy_task, self.execution.result)
        if self.execution and self.execution.result:
            from agender.protocol.results import validate_result
            from agender.worker.status import evaluate_status
            saved = _payload(self._busy_task, self.execution.result)
            try:
                saved = validate_result(saved)
            except ValidationError:
                saved = None
            if saved:
                row = await self.store.get_run(self._busy_task.task_id, self._busy_task.attempt)
                snapshot = TaskResult(task_id=self._busy_task.task_id, worker_id=self.cfg.worker.id,
                                      attempt=self._busy_task.attempt, lease_id=self._lease_id,
                                      status=evaluate_status(TaskType(self._busy_task.type), saved, "DEADLINE_SOFT"),
                                      started_at=row["started_at"], finished_at=_iso(), result=saved,
                                      error=ErrorBody(code="WORKER_INTERRUPTED", message="worker interrupted after saved progress", retryable=True))
                await self.store.save_snapshot(self._run_id, snapshot.model_dump(mode="json"))
        body = Progress(task_id=self._busy_task.task_id, lease_id=self._lease_id, seq=self._seq, message=stage, counts=counts)
        await self.store.add_event(self._run_id, "info", stage, counts)
        try:
            # Telemetry cannot hold up task cancellation or browser work indefinitely.
            async with asyncio.timeout(2):
                ack = await self.client.progress(body.task_id, body)
                if not ack.ok or ack.task_id != body.task_id or ack.seq != body.seq:
                    log.warning("invalid_progress_ack")
        except LeaseLost:
            self._lose_lease(confirmed=True)
        except ProtocolError as exc:
            if exc.code == "UNAUTHORIZED":
                self.request_stop()
        except TimeoutError:
            pass
        except ValidationError:
            log.warning("invalid_progress_ack")

    async def _supervise(self, operation, execution: ExecutionContext):
        work = asyncio.create_task(operation)
        watcher = asyncio.create_task(execution.watch())
        try:
            done, _ = await asyncio.wait({work, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                code = watcher.result()
                work.cancel()
                done, _ = await asyncio.wait({work}, timeout=self.cfg.worker.shutdown_grace_s)
                if not done:
                    code = "DEADLINE_HARD" if code == "DEADLINE_SOFT" else code
                    self.request_stop()
                    if self.runtime:
                        with contextlib.suppress(Exception):
                            async with asyncio.timeout(2):
                                await self.runtime.close_context()
                    work.cancel()
                else:
                    await asyncio.gather(work, return_exceptions=True)
                raise ExecutionStopped(code)
            return await work
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if not work.done():
                work.cancel()
                work.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    async def _run_claimed(self, task: TaskEnvelope, lease) -> TaskResult:
        from agender.worker.status import evaluate_status

        existing = await self.store.get_run(task.task_id, task.attempt)
        if existing:
            if existing["status"] in {"running", "submitting"}:
                await self._recover()
                existing = await self.store.get_run(task.task_id, task.attempt)
            if existing["result_json"]:
                if existing["status"] in {"acked", "interrupted", "superseded"} and hasattr(self.client, "_finish_claim"):
                    self.client._finish_claim(task.task_id, task.attempt)
                return TaskResult.model_validate_json(existing["result_json"])
            raise ProtocolError("DUPLICATE_ATTEMPT", "attempt already exists")
        self._busy_task = task
        self._lease_id = lease.lease_id if lease else None
        self._lease_until = lease.lease_until if lease else None
        self._seq = 0
        run_id = await self.store.insert_run(task_id=task.task_id, task_type=task.type, attempt=task.attempt,
                                            lease_id=self._lease_id, lease_until=self._lease_until,
                                            payload=strip_secrets(task.model_dump(mode="json")))
        self._run_id = run_id
        started, t0 = _iso(), time.monotonic()
        result, error_code = None, None
        ttype = None
        status = ResultStatus.FAILED
        self.execution = execution = ExecutionContext(on_progress=self._progress)
        try:
            if task.type == "__invalid__":
                raise ExecutionStopped("INVALID_PARAMS")
            try:
                ttype = TaskType(task.type)
            except ValueError:
                raise ExecutionStopped("UNSUPPORTED_TYPE")
            if task.schema_version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
                raise ExecutionStopped("SCHEMA_MISMATCH")
            try:
                params = parse_params(ttype, task.params)
            except ValidationError:
                raise ExecutionStopped("INVALID_PARAMS")
            params_raw = params.model_dump(mode="json")
            task.params.pop("password", None)
            budget = _wall_clock(self.cfg, task)
            execution.deadline = time.monotonic() + budget
            execution.hard_deadline = min(t0 + self.cfg.worker.task_hard_cap_s, execution.deadline + self.cfg.worker.shutdown_grace_s)
            if lease is None:
                raise ExecutionStopped("LEASE_LOST")
            try:
                execution.renew(lease.lease_until)
            except ValueError:
                raise ExecutionStopped("LEASE_LOST")
            if self.stop.is_set():
                execution.cancel("WORKER_SHUTDOWN")
            execution.check()
            await self._prepare_resume(task, params_raw, execution)
            router = Router(self.cfg, self.runtime)
            router.execution = execution
            session = await self._session()
            result = await self._supervise(router.execute(ttype, params_raw, session=session, deadline=execution.deadline), execution)
            error_code = (result or {}).pop("error_code", None)
            execution.snapshot(result)
            status = evaluate_status(ttype, result, error_code)
        except ExecutionStopped as exc:
            error_code, result = exc.code, execution.result
            status = evaluate_status(ttype or TaskType.WEB_SEARCH, result, error_code)
        except asyncio.CancelledError:
            error_code, result = "WORKER_SHUTDOWN", execution.result
            status = evaluate_status(ttype or TaskType.WEB_SEARCH, result, error_code)
        except Exception as exc:
            # Exception text can include credentials or response bodies; persist only its class.
            log.error("task_failed", exception_type=type(exc).__name__)
            error_code, result = getattr(exc, "code", "EXTRACTION_FAILED"), execution.result
            status = evaluate_status(ttype or TaskType.WEB_SEARCH, result, error_code)
        finally:
            task.params.pop("password", None)
        if result:
            result = _payload(task, result)
            if error_code:
                result["stop_reason"] = error_code.lower()
            if status in {ResultStatus.SUCCEEDED, ResultStatus.PARTIAL}:
                from agender.protocol.results import validate_result
                try:
                    result = validate_result(result)
                except ValidationError:
                    result, error_code, status = None, "RESULT_SCHEMA_ERROR", ResultStatus.FAILED
            try:
                if result:
                    await self._save_content(task, result)
            except Exception as exc:
                log.error("content_save_failed", exception_type=type(exc).__name__)
                error_code = getattr(exc, "code", "STORAGE_FAILED")
                status = ResultStatus.PARTIAL
        artifacts = await self._capture(task, run_id, status)
        duration_ms = int((time.monotonic() - t0) * 1000)
        from agender.protocol.planner import planner_hints
        tr = TaskResult(task_id=task.task_id, worker_id=self.cfg.worker.id, attempt=task.attempt,
                        lease_id=self._lease_id, status=status, started_at=started, finished_at=_iso(),
                        duration_ms=duration_ms, result=result,
                        error=ErrorBody(code=error_code, message=error_code, retryable=error_code in {
                            "NAVIGATION_TIMEOUT", "EMPTY_RENDER", "RATE_LIMITED", "WORKER_INTERRUPTED", "COMMENT_LOAD_STALL", "STORAGE_FAILED"
                        }) if error_code else None, artifacts=artifacts,
                        hints_for_planner=planner_hints(task, result, error_code))
        await self.store.set_result(run_id, tr.model_dump(mode="json"), "submitting", error_code)
        self.execution = None
        if await self._deliver(run_id, tr):
            self._clear_busy()
        health = getattr(self.runtime, "health", None)
        if health and task.type != "session.login":
            health.record(task_site(task.type, task.params), error_code)
        metrics.TASKS_TOTAL.labels(type=task.type, status=status.value).inc()
        metrics.TASK_DURATION.labels(type=task.type, status=status.value).observe(duration_ms / 1000)
        if status == ResultStatus.SUCCEEDED:
            metrics.EXTRACT_SUCCESS.labels(type=task.type).inc()
            metrics.LAST_SUCCESS.labels(type=task.type).set(time.time())
        if status == ResultStatus.BLOCKED:
            metrics.BLOCKED_TOTAL.labels(site=task_site(task.type, task.params), code=error_code or "blocked").inc()
        return tr

    async def _prepare_resume(self, task, params, execution) -> None:
        if params.get("resume_from"):
            if not self.content:
                raise ExecutionStopped("CONTENT_STORE_DISABLED")
            from agender.store.checkpoints import restore
            execution.resume = await restore(self.content, task, params, self._extractor_version(task))
            metrics.CHECKPOINT_RESTORE.labels(type=task.type).inc()
            execution.snapshot(execution.resume)
        else:
            execution.resume = None

    async def _save_content(self, task, result) -> None:
        if self.content and task.type != "session.login":
            await self.content.save_result(task.type, result)
            from agender.store.checkpoints import save
            checkpoint = await save(self.content, task, result, self._extractor_version(task), self.cfg.store.checkpoint_ttl_s)
            if checkpoint:
                result["checkpoint_id"] = checkpoint

    def _extractor_version(self, task) -> str:
        site = task_site(task.type, task.params)
        return getattr(self.cfg, site).selector_pack_version if site in SESSION_SITES else "web-1.1"

    async def _capture(self, task: TaskEnvelope, run_id: str, status: ResultStatus) -> list[ArtifactRef]:
        if task.type == "session.login" or not self.runtime or self.runtime.context is None:
            return []
        if self.stop.is_set() or self.execution and self.execution.reason in {"LEASE_LOST", "DEADLINE_HARD"}:
            return []
        if self.execution:
            try:
                self.execution.check()
            except ExecutionStopped as exc:
                if exc.code in {"LEASE_LOST", "DEADLINE_HARD", "WORKER_SHUTDOWN"}:
                    return []
        refs = []
        failed = status != ResultStatus.SUCCEEDED
        for kind, setting in (("screenshot", "capture_screenshot"), ("html", "capture_html")):
            mode = task.params.get(setting, "on_failure" if kind == "screenshot" else "never")
            if mode != "always" and not (mode == "on_failure" and failed):
                continue
            try:
                async with asyncio.timeout(2):
                    page = self.runtime.active_page if isinstance(self.runtime, BrowserRuntime) else (self.runtime.context.pages[0] if self.runtime.context.pages else None)
                    if page is None:
                        continue
                    if kind == "screenshot":
                        blob, suffix, mime = await page.screenshot(type="png"), "png", "image/png"
                    else:
                        from agender.browser.sanitize import sanitize_html
                        blob = sanitize_html(await page.content()).encode("utf-8")
                        if len(blob) > 2 * 1024 * 1024:
                            continue
                        suffix, mime = "html", "text/html"
                    dest = artifact_dir(self.cfg.path(self.cfg.store.artifact_dir), task.task_id) / str(task.attempt) / f"capture.{suffix}"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    digest, size = write_bytes(dest, blob)
                    art_id = await self.store.add_artifact(run_id, kind=kind, path=str(dest), sha256=digest, size=size, content_type=mime)
                    refs.append(ArtifactRef(artifact_id=art_id, kind=kind, path=str(dest), sha256=digest, bytes=size, content_type=mime))
            except Exception:
                log.warning("capture_failed", kind=kind)
        return refs


async def run_one_file(cfg: AppConfig, path, runtime: BrowserRuntime | None = None) -> TaskResult:
    task = TaskEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
    if "password" in task.params:
        from agender.store.atomic import atomic_json
        atomic_json(path, strip_secrets(task.model_dump(mode="json")))
    local_cfg = cfg.model_copy(deep=True)
    local_cfg.server.base_url = ""
    worker = WorkerLoop(local_cfg, runtime=runtime)
    try:
        await worker.setup(recover=False)
        if await worker.store.unfinished():
            raise ProtocolError("RESULT_PENDING", "resolve pending result before running another task")
        lease = Lease(lease_id=f"local_{task.task_id}_{task.attempt}", lease_until=(datetime.now(UTC) + timedelta(seconds=cfg.worker.task_hard_cap_s + 60)).isoformat())
        # One-shot is a local execution, never an invented lease submitted to a remote server.
        from agender.protocol.file_client import FileClient
        await worker.client.aclose()
        worker.client = FileClient(cfg.path(cfg.worker.file_client.inbox_dir), cfg.path(cfg.worker.file_client.outbox_dir))
        return await worker._run_claimed(task, lease)
    finally:
        await worker.aclose()
