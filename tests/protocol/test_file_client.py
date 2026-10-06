import json

import pytest

from agender.protocol.file_client import FileClient
from agender.protocol.schemas import ClaimRequest, Heartbeat, ResultStatus, TaskResult, WorkerStatus


@pytest.mark.asyncio
async def test_file_client_claim_and_result(tmp_path):
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    client = FileClient(inbox, outbox)
    (inbox / "t1.json").write_text(
        json.dumps(
            {
                "task_id": "tsk_1",
                "type": "web.search",
                "params": {"query": "q", "password": "nope"},
            }
        ),
        encoding="utf-8",
    )
    claim = await client.claim(ClaimRequest(worker_id="w", capabilities=["web.search"]))
    assert claim.task is not None
    assert "password" not in claim.task.params
    ack = await client.heartbeat(
        Heartbeat(worker_id="w", status=WorkerStatus.BUSY, current_task_id="tsk_1", lease_id=claim.lease.lease_id)
    )
    assert ack.lease is not None
    result = await client.submit_result(
        TaskResult(
            task_id="tsk_1",
            worker_id="w",
            status=ResultStatus.SUCCEEDED,
            started_at="2026-09-11T00:00:00Z",
            finished_at="2026-09-11T00:00:01Z",
            result={"kind": "web.search", "password": "x"},
        )
    )
    assert result.ok
    saved = json.loads((outbox / "results" / "tsk_1.json").read_text(encoding="utf-8"))
    assert "password" not in json.dumps(saved)


@pytest.mark.asyncio
async def test_empty_inbox(tmp_path):
    client = FileClient(tmp_path / "in", tmp_path / "out")
    claim = await client.claim(ClaimRequest(worker_id="w", capabilities=["web.search"]))
    assert claim.task is None


@pytest.mark.asyncio
async def test_result_idempotency_and_attempt_history(tmp_path):
    client = FileClient(tmp_path / "in", tmp_path / "out")
    body = TaskResult(task_id="t", worker_id="w", status="succeeded", started_at="now", finished_at="now", result={"value": 1})
    await client.submit_result(body)
    body.result = {"value": 2}
    ack = await client.submit_result(body)
    assert ack.duplicate
    assert json.loads((tmp_path / "out/results/t/1.json").read_text())["result"]["value"] == 1
    body.attempt = 2
    await client.submit_result(body)
    body.attempt = 1
    await client.submit_result(body)
    assert json.loads((tmp_path / "out/results/t.json").read_text())["attempt"] == 2


@pytest.mark.asyncio
async def test_malformed_and_unknown_tasks_receive_claims(tmp_path):
    client = FileClient(tmp_path / "in", tmp_path / "out")
    (tmp_path / "in/a.json").write_text('{"password":"secret",')
    bad = await client.claim(ClaimRequest(worker_id="w", capabilities=[]))
    assert bad.task.type == "__invalid__"
    assert "secret" not in "".join(p.read_text() for p in (tmp_path / "in").rglob("*.json"))
    (tmp_path / "in/b.json").write_text(json.dumps({"task_id": "unknown", "type": "future.task"}))
    unknown = await client.claim(ClaimRequest(worker_id="w", capabilities=[]))
    assert unknown.task.type == "future.task"


@pytest.mark.asyncio
async def test_login_password_is_memory_only(tmp_path):
    client = FileClient(tmp_path / "in", tmp_path / "out")
    (tmp_path / "in/a.json").write_text(json.dumps({"task_id": "login", "type": "session.login", "params": {"password": "secret"}}))
    claim = await client.claim(ClaimRequest(worker_id="w", capabilities=["session.login"]))
    assert claim.task.params["password"] == "secret"
    assert "secret" not in "".join(p.read_text() for p in (tmp_path / "in").rglob("*.json"))
