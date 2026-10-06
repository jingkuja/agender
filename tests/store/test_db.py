import pytest

from agender.store.db import Store


@pytest.mark.asyncio
async def test_unique_task_attempt(tmp_path):
    store = Store(tmp_path / "agender.db")
    await store.open()
    await store.insert_run(task_id="t", task_type="web.search", attempt=1, lease_id="l", lease_until=None, payload={"params": {"password": "x"}})
    row = (await store.unfinished())[0]
    assert "password" not in row["payload_json"]
    with pytest.raises(Exception):
        await store.insert_run(task_id="t", task_type="web.search", attempt=1, lease_id="l", lease_until=None, payload={})
    await store.close()
