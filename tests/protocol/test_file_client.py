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
