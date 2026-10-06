import pytest

from agender.protocol.client import HttpClient
from agender.protocol.errors import LeaseLost
from agender.protocol.schemas import ClaimRequest, Heartbeat, WorkerStatus


@pytest.mark.asyncio
async def test_http_claim_empty(httpx_mock):
    httpx_mock.add_response(
        url="https://example.test/v1/tasks/claim",
        json={"schema_version": "1.0", "task": None, "lease": None},
    )
    client = HttpClient("https://example.test", "tok", "w")
    claim = await client.claim(ClaimRequest(worker_id="w", capabilities=["web.search"]))
    assert claim.task is None
    await client.aclose()


@pytest.mark.asyncio
async def test_http_409(httpx_mock):
    httpx_mock.add_response(
        url="https://example.test/v1/heartbeat",
        status_code=409,
        json={"schema_version": "1.0", "code": "LEASE_LOST", "message": "gone"},
    )
    client = HttpClient("https://example.test", "tok", "w")
    with pytest.raises(LeaseLost):
        await client.heartbeat(
            Heartbeat(worker_id="w", status=WorkerStatus.BUSY, current_task_id="t", lease_id="l")
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limit_retry_after(httpx_mock, monkeypatch):
    from unittest.mock import AsyncMock
    sleep = AsyncMock()
    monkeypatch.setattr("agender.protocol.client.asyncio.sleep", sleep)
    httpx_mock.add_response(url="https://example.test/v1/tasks/claim", status_code=429, headers={"Retry-After": "3"})
    httpx_mock.add_response(url="https://example.test/v1/tasks/claim", json={"task": None})
    client = HttpClient("https://example.test", "tok", "w")
    await client.claim(ClaimRequest(worker_id="w", capabilities=[]))
    sleep.assert_awaited_once_with(3)
    requests = httpx_mock.get_requests()
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
    await client.aclose()
