from __future__ import annotations

import json

import pytest

from agender.protocol.file_client import FileClient
from agender.protocol.schemas import ResultStatus, TaskType
from agender.store.db import Store
from agender.worker.loop import WorkerLoop
from agender.worker.status import evaluate_status


def test_evaluate_login_wall():
    st = evaluate_status(
        TaskType.DOUYIN_VIDEO_COMMENTS,
        {"kind": "douyin.video_comments", "comments": []},
        "LOGIN_WALL",
    )
    assert st == ResultStatus.NEEDS_HUMAN


@pytest.mark.asyncio
async def test_worker_file_client_search(cfg, tmp_path, html, monkeypatch):
    monkeypatch.setattr("agender.extractors.web.policy.resolve_host", lambda h: ["203.0.113.10"])
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    cfg.worker.file_client.inbox_dir = str(inbox)
    cfg.worker.file_client.outbox_dir = str(outbox)
    cfg.root = tmp_path
    inbox.mkdir()
    (inbox / "t.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "task_id": "tsk_web",
                "type": "web.search",
                "params": {"query": "固态电池"},
            }
        ),
        encoding="utf-8",
    )
    store = Store(tmp_path / "db.sqlite")
    await store.open()
    client = FileClient(inbox, outbox)

    async def fetch(url: str) -> str:
        return html("bing_serp.html")

    from agender.extractors.web.tool import WebTool
    from agender.worker.router import Router

    monkeypatch.setattr(WebTool, "_robots_fetch", lambda self, u: "User-agent: *\nAllow: /\n")

    orig = Router.execute

    async def execute(self, task_type, params_raw, *, session, deadline):
        self.fetch_web = fetch
        return await orig(self, task_type, params_raw, session=session, deadline=deadline)

    monkeypatch.setattr(Router, "execute", execute)

    loop = WorkerLoop(cfg, runtime=None, client=client, store=store)
    # This test injects an HTML fetcher instead of starting a browser.
    from unittest.mock import AsyncMock
    loop._caps = AsyncMock(return_value=(["web.search"], [], None, None, True))
    loop._locks = []
    await loop._idle_once()
    dest = outbox / "results" / "tsk_web.json"
    assert dest.exists()
    body = json.loads(dest.read_text(encoding="utf-8"))
    assert body["status"] in {"succeeded", "partial"}
    await store.close()
