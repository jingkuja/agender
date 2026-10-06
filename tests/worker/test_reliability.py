import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from agender.protocol.errors import LeaseLost, ProtocolError
from agender.protocol.file_client import FileClient
from agender.protocol.schemas import HeartbeatAck, Lease, ResultAck, TaskEnvelope, WorkerStatus
from agender.store.db import Store
from agender.worker.execution import ExecutionContext, ExecutionStopped
from agender.worker.loop import WorkerLoop, _wall_clock
from agender.worker.router import Router


def lease(seconds=240):
    return Lease(lease_id="lease1", lease_until=(datetime.now(UTC) + timedelta(seconds=seconds)).isoformat())


def task(**kwargs):
    return TaskEnvelope(task_id="t", type="web.search", params={"query": "q"}, **kwargs)


async def worker(cfg, tmp_path, client=None):
    cfg.worker.submit_retry_s = 0.01
    cfg.worker.shutdown_grace_s = 0.1
    store = Store(tmp_path / "runs.db")
    await store.open()
    transport = client or FileClient(tmp_path / "in", tmp_path / "out")
    w = WorkerLoop(cfg, store=store, client=transport)
    return w


@pytest.mark.asyncio
async def test_expired_deadline_never_executes(cfg, tmp_path, monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(Router, "execute", execute)
    w = await worker(cfg, tmp_path)
    t = task(deadline_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    assert _wall_clock(cfg, t) == 0
    result = await w._run_claimed(t, lease())
    assert result.error.code == "DEADLINE_SOFT"
    execute.assert_not_awaited()
    await w.aclose()


@pytest.mark.asyncio
async def test_stop_keeps_partial_result(cfg, tmp_path, monkeypatch):
    started = asyncio.Event()
    async def execute(self, *args, **kwargs):
        self.execution.snapshot({"kind": "web.search", "serp": [{"title": "already read", "url": "https://example.org/a"}]})
        started.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(Router, "execute", execute)
    w = await worker(cfg, tmp_path)
    work = asyncio.create_task(w._run_claimed(task(), lease()))
    await started.wait()
    w.request_stop()
    result = await asyncio.wait_for(work, 1)
    assert result.status.value == "partial"
    assert result.error.code == "WORKER_SHUTDOWN"
    assert len(result.result["serp"]) == 1
    await w.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [HeartbeatAck(), HeartbeatAck(lease=lease(-1)), HeartbeatAck(lease=Lease(lease_id="other", lease_until=lease().lease_until))])
async def test_invalid_renewal_cancels(cfg, tmp_path, ack):
    w = await worker(cfg, tmp_path)
    w._busy_task, w._lease_id = task(), "lease1"
    w.execution = ExecutionContext()
    w.client.heartbeat = AsyncMock(return_value=ack)
    await w._heartbeat(WorkerStatus.BUSY)
    assert w.execution.reason == "LEASE_LOST"
    await w.aclose()


@pytest.mark.asyncio
async def test_confirmed_lost_lease_stops_and_does_not_submit(cfg, tmp_path, monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    async def execute(self, *args, **kwargs):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()
    monkeypatch.setattr(Router, "execute", execute)
    w = await worker(cfg, tmp_path)
    submit = AsyncMock()
    w.client.submit_result = submit
    w.client.heartbeat = AsyncMock(side_effect=LeaseLost())
    work = asyncio.create_task(w._run_claimed(task(), lease()))
    await started.wait()
    await w._heartbeat(WorkerStatus.BUSY)
    result = await asyncio.wait_for(work, 1)
    assert result.error.code == "LEASE_LOST"
    assert cancelled.is_set()
    submit.assert_not_awaited()
    assert (await w.store.get_run("t", 1))["status"] == "superseded"
    await w.aclose()


@pytest.mark.asyncio
async def test_negative_ack_waits_and_replays_without_recrawling(cfg, tmp_path, monkeypatch):
    execute = AsyncMock(return_value={"kind": "web.search", "serp": [], "returned": 0})
    monkeypatch.setattr(Router, "execute", execute)
    w = await worker(cfg, tmp_path)
    w.client.submit_result = AsyncMock(side_effect=[
        ResultAck(ok=False, task_id="t", accepted_status="succeeded"),
        ResultAck(task_id="t", accepted_status="succeeded"),
    ])
    w.client.claim = AsyncMock()
    await w._run_claimed(task(), lease())
    assert (await w.store.get_run("t", 1))["status"] == "submitting"
    assert w._busy_task is not None
    await w._idle_once()  # backoff only; never claim a second task
    w.client.claim.assert_not_awaited()
    w._next_submit = 0
    assert await w._recover()
    assert (await w.store.get_run("t", 1))["status"] == "acked"
    assert execute.await_count == 1
    calls = w.client.submit_result.await_args_list
    assert calls[0].args[0].model_dump() == calls[1].args[0].model_dump()
    await w.aclose()


@pytest.mark.asyncio
async def test_restart_replays_saved_result(cfg, tmp_path, monkeypatch):
    execute = AsyncMock(return_value={"kind": "web.search", "serp": []})
    monkeypatch.setattr(Router, "execute", execute)
    w = await worker(cfg, tmp_path)
    w.client.submit_result = AsyncMock(side_effect=ProtocolError("UNAVAILABLE", "offline", retryable=True))
    await w._run_claimed(task(), lease())
    await w.aclose()
    w2 = await worker(cfg, tmp_path)
    assert await w2._recover()
    saved = json.loads((tmp_path / "out/results/t/1.json").read_text())
    assert saved["status"] == "succeeded"
    assert execute.await_count == 1
    await w2.aclose()


@pytest.mark.asyncio
async def test_orphan_file_claim_is_interrupted_not_executed(cfg, tmp_path, monkeypatch):
    w = await worker(cfg, tmp_path)
    inbox = tmp_path / "in"
    (inbox / ".claimed-t.json").write_text(task().model_dump_json())
    execute = AsyncMock()
    monkeypatch.setattr(Router, "execute", execute)
    await w._recover_file_claims()
    await w._recover()
    row = await w.store.get_run("t", 1)
    assert row["status"] == "acked"
    assert json.loads(row["result_json"])["error"]["code"] == "WORKER_INTERRUPTED"
    execute.assert_not_awaited()
    assert not list(inbox.glob(".claimed-*.json"))
    await w.aclose()


@pytest.mark.asyncio
async def test_same_attempt_never_executes_twice(cfg, tmp_path, monkeypatch):
    execute = AsyncMock(return_value={"kind": "web.search", "serp": []})
    monkeypatch.setattr(Router, "execute", execute)
    w = await worker(cfg, tmp_path)
    one = await w._run_claimed(task(), lease())
    two = await w._run_claimed(task(), lease())
    assert one == two
    assert execute.await_count == 1
    await w.aclose()


@pytest.mark.asyncio
async def test_execution_wait_is_cancelled_and_lease_can_renew():
    e = ExecutionContext()
    e.renew(lease(0.01).lease_until)
    e.renew(lease(10).lease_until)
    await e.sleep(0.02)
    e.cancel("LEASE_LOST")
    with pytest.raises(ExecutionStopped, match="LEASE_LOST"):
        await e.sleep(60)


async def test_shutdown_submission_is_bounded_and_saved(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(Router, 'execute', AsyncMock(return_value={'kind': 'web.search', 'serp': []}))
    w = await worker(cfg, tmp_path)
    async def stalled(*args):
        await asyncio.sleep(60)
    w.client.submit_result = stalled
    w.request_stop()
    result = await asyncio.wait_for(w._run_claimed(task(), lease()), 1)
    assert result.status.value == 'cancelled'
    assert (await w.store.get_run('t', 1))['status'] == 'submitting'
    await w.aclose()


async def test_bundle_cancel_after_video_keeps_typed_video(cfg, tmp_path, monkeypatch):
    started = asyncio.Event()
    async def execute(self, *args, **kwargs):
        self.execution.snapshot({'kind': 'douyin.video_stats', 'video_id': '12345', 'title': 'Saved'})
        started.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(Router, 'execute', execute)
    w = await worker(cfg, tmp_path)
    t = TaskEnvelope(task_id='bundle', type='douyin.video_bundle', params={'video_id': '12345'})
    work = asyncio.create_task(w._run_claimed(t, lease()))
    await started.wait()
    w.request_stop()
    result = await work
    assert result.status.value == 'partial'
    assert result.result['video']['title'] == 'Saved'
    assert result.result['kind'] == 'douyin.video_bundle'
    await w.aclose()


async def test_progress_saves_checkpoint_then_new_attempt_resumes(cfg, tmp_path, monkeypatch):
    from agender.store.content import ContentStore
    w = await worker(cfg, tmp_path)
    w.content = ContentStore(tmp_path / 'content.db')
    await w.content.open()
    started = asyncio.Event()
    async def first(self, *args, **kwargs):
        self.execution.snapshot({'kind': 'web.search', 'serp': [{'url': 'https://example.org/a', 'title': 'Saved'}]})
        await self.execution.progress('search_page', {'returned': 1})
        started.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(Router, 'execute', first)
    work = asyncio.create_task(w._run_claimed(task(), lease()))
    await started.wait()
    w.execution.cancel('DEADLINE_SOFT')
    one = await work
    cp = one.result['checkpoint_id']
    assert cp
    async def second(self, *args, **kwargs):
        assert self.execution.resume['serp'][0]['title'] == 'Saved'
        return {**self.execution.resume, 'limit_reached': True}
    monkeypatch.setattr(Router, 'execute', second)
    next_task = task(attempt=2)
    next_task.params['resume_from'] = cp
    two = await w._run_claimed(next_task, lease())
    assert two.status.value == 'succeeded'
    assert len(two.result['serp']) == 1
    await w.aclose()


async def test_stop_interrupts_an_already_pending_claim(cfg, tmp_path):
    started = asyncio.Event()
    w = await worker(cfg, tmp_path)
    w._caps = AsyncMock(return_value=(['web.search'], [], None, None, True))
    async def stalled(*args):
        started.set()
        await asyncio.sleep(60)
    w.client.claim = stalled
    work = asyncio.create_task(w._idle_once())
    await started.wait()
    w.request_stop()
    await asyncio.wait_for(work, 1)
    await w.aclose()


async def test_one_shot_http_configuration_is_local(cfg, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agender.protocol.schemas import SessionState
    from agender.worker.loop import run_one_file
    cfg.server.base_url = 'https://server.invalid'
    cfg.token = ''
    runtime = SimpleNamespace(context=None, browser_ready=True, aclose=AsyncMock(),
                              douyin_session=AsyncMock(return_value=SessionState.MISSING))
    monkeypatch.setattr(Router, 'execute', AsyncMock(return_value={'kind': 'web.search', 'serp': []}))
    path = tmp_path / 'task.json'
    path.write_text(task().model_dump_json())
    result = await run_one_file(cfg, path, runtime)
    assert result.status.value == 'succeeded'
    assert (cfg.path(cfg.worker.file_client.outbox_dir) / 'results/t/1.json').exists()
    assert cfg.server.base_url == 'https://server.invalid'


async def test_one_shot_never_acknowledges_existing_pending_remote_run(cfg, tmp_path):
    from types import SimpleNamespace

    from agender.worker.loop import run_one_file
    cfg.server.base_url = 'https://server.invalid'
    runtime = SimpleNamespace(context=None, aclose=AsyncMock())
    store = Store(cfg.path(cfg.store.sqlite_path))
    await store.open()
    await store.insert_run(task_id='remote', task_type='web.search', attempt=1,
                           lease_id='remote-lease', lease_until=None, payload={})
    await store.close()
    path = tmp_path / 'task.json'
    path.write_text(task().model_dump_json())
    with pytest.raises(ProtocolError, match='resolve pending result'):
        await run_one_file(cfg, path, runtime)
    await store.open()
    assert (await store.get_run('remote', 1))['status'] == 'running'
    await store.close()
    assert not (cfg.path(cfg.worker.file_client.outbox_dir) / 'results/remote.json').exists()


async def test_restart_replays_durable_partial_progress(cfg, tmp_path):
    w = await worker(cfg, tmp_path)
    w._busy_task, w._lease_id = task(), 'lease1'
    w._run_id = await w.store.insert_run(task_id='t', task_type='web.search', attempt=1,
                                         lease_id='lease1', lease_until=None, payload={})
    w.execution = ExecutionContext()
    w.execution.snapshot({'kind': 'web.search', 'serp': [{'url': 'https://example.org/a', 'title': 'Durable'}]})
    await w._progress('search_page', {'returned': 1})
    await w.aclose()  # Simulate loss after progress, before final set_result.
    restarted = await worker(cfg, tmp_path)
    assert await restarted._recover()
    saved = json.loads((tmp_path / 'out/results/t/1.json').read_text())
    assert saved['status'] == 'partial'
    assert saved['error']['code'] == 'WORKER_INTERRUPTED'
    assert saved['result']['serp'][0]['title'] == 'Durable'
    await restarted.aclose()


async def test_soft_timeout_escalates_when_extractor_ignores_first_cancel(cfg, tmp_path, monkeypatch):
    async def resistant(self, *args, **kwargs):
        self.execution.snapshot({'kind': 'web.search', 'serp': [{'url': 'https://example.org/a'}]})
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(60)
    monkeypatch.setattr(Router, 'execute', resistant)
    w = await worker(cfg, tmp_path)
    t = task(deadline_at=(datetime.now(UTC) + timedelta(seconds=0.03)).isoformat())
    result = await asyncio.wait_for(w._run_claimed(t, lease()), 1)
    assert result.error.code == 'DEADLINE_HARD'
    assert result.status.value == 'partial'
    assert w.stop.is_set()
    await w.aclose()
