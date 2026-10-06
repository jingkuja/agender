import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from agender.browser.runtime import BrowserRuntime
from agender.protocol.file_client import FileClient
from agender.protocol.planner import catalog, planner_hints
from agender.protocol.schemas import Lease, SessionState, TaskEnvelope, WorkerStatus
from agender.store.content import ContentStore
from agender.store.db import Store
from agender.worker.heartbeat import capabilities
from agender.worker.loop import WorkerLoop
from agender.worker.router import Router


@pytest.mark.parametrize("site", ["xiaohongshu", "weibo", "bilibili"])
def test_capabilities_and_catalog_reflect_platform_flags(cfg, site):
    setattr(cfg.tools, site, True)
    getattr(cfg, site).tos_ack = True
    caps, disabled, status = capabilities(cfg, browser_ready=True, douyin_session=SessionState.OK, sessions={site: SessionState.MISSING})
    assert f"{site}.search" in caps
    assert f"{site}.comments" in disabled
    assert "session.login" in caps and status == WorkerStatus.DEGRADED
    caps, disabled, _ = capabilities(cfg, browser_ready=True, douyin_session=SessionState.OK, sessions={site: SessionState.OK})
    assert f"{site}.bundle" in caps and f"{site}.comments" not in disabled
    tasks = {t["type"]: t for t in catalog(cfg)["tasks"]}
    assert tasks[f"{site}.detail"]["enabled"]
    assert "content_id" in tasks[f"{site}.detail"]["params_schema"]["properties"]


async def test_cdp_heartbeat_uses_each_platform_cookie(cfg):
    cfg.browser.mode = "cdp"
    cfg.tools.xiaohongshu = cfg.tools.weibo = cfg.tools.bilibili = True
    cfg.xiaohongshu.tos_ack = cfg.weibo.tos_ack = cfg.bilibili.tos_ack = True
    runtime = BrowserRuntime(cfg)
    runtime.browser_ready = True
    runtime._context = AsyncMock()
    async def cookies(urls):
        if "xiaohongshu" in urls[0]:
            return [{"name": "web_session", "expires": -1}]
        if "bilibili" in urls[0]:
            return [{"name": "SESSDATA", "expires": 1}]
        return []
    runtime.context.cookies.side_effect = cookies
    worker = WorkerLoop(cfg, runtime=runtime)
    caps, disabled, _, _, _ = await worker._caps()
    assert worker.sessions["xiaohongshu"] == SessionState.OK
    assert worker.sessions["weibo"] == SessionState.MISSING
    assert worker.sessions["bilibili"] == SessionState.EXPIRED
    assert "xiaohongshu.comments" in caps and "bilibili.comments" in disabled


async def test_social_task_file_result_and_content_pipeline(cfg, tmp_path, monkeypatch):
    cfg.tools.bilibili = cfg.bilibili.tos_ack = True
    post = {"bvid": "BV1xx411c7mD", "title": "Public video", "owner": {"mid": "123456", "name": "Creator"}, "stat": {"like": 3}}
    html = '<script type="application/json">' + json.dumps({"items": [post]}) + '</script><div class="no-more">end</div>'
    original = Router.execute
    async def execute(self, *args, **kwargs):
        self.fetch_social = {"bilibili": AsyncMock(return_value=html)}
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(Router, "execute", execute)
    store = Store(tmp_path / "runs.db")
    content = ContentStore(tmp_path / "content.db")
    await store.open()
    await content.open()
    client = FileClient(tmp_path / "inbox", tmp_path / "outbox")
    worker = WorkerLoop(cfg, client=client, store=store)
    worker.content = content
    try:
        task = TaskEnvelope(task_id="social", type="bilibili.search", params={"query": "demo", "capture_screenshot": "never"})
        lease = Lease(lease_id="local", lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())
        result = await worker._run_claimed(task, lease)
        assert result.status.value == "succeeded"
        assert result.hints_for_planner.suggested_tasks[0]["type"] == "bilibili.detail"
        assert (tmp_path / "outbox/results/social.json").exists()
        row = await (await content.db.execute("SELECT platform,content_id FROM contents")).fetchone()
        assert tuple(row) == ("bilibili", "BV1xx411c7mD")
        repeat = await worker._run_claimed(task, lease)
        assert repeat.result == result.result
    finally:
        await worker.aclose()


async def test_content_is_partitioned_by_platform_and_saves_replies(tmp_path):
    content = ContentStore(tmp_path / "content.db")
    await content.open()
    try:
        for site in ("xiaohongshu", "weibo", "bilibili"):
            await content.save_result(f"{site}.bundle", {"content": {"content_id": "same-id", "url": "https://example.org/", "stats": {"like_count": 0}},
                "comments": [{"comment_id": "root", "text": "root", "replies": [{"comment_id": "child", "parent_id": "root", "text": "reply"}]}]})
        rows = await (await content.db.execute("SELECT platform FROM contents")).fetchall()
        assert {r[0] for r in rows} == {"xiaohongshu", "weibo", "bilibili"}
        assert (await (await content.db.execute("SELECT COUNT(*) FROM comments")).fetchone())[0] == 6
        assert (await (await content.db.execute("SELECT COUNT(*) FROM metric_snapshots")).fetchone())[0] == 3
    finally:
        await content.close()


def test_login_planner_hint_targets_requested_platform():
    task = TaskEnvelope(task_id="blocked", type="weibo.comments", params={"content_id": "123456"})
    hints = planner_hints(task, None, "LOGIN_WALL")
    assert hints.blockers == ["LOGIN_WALL"]
    assert hints.suggested_tasks == [{"type": "session.login", "params": {"site": "weibo"}}]


async def test_new_attempt_restores_social_checkpoint(cfg, tmp_path, monkeypatch):
    cfg.tools.weibo = cfg.weibo.tos_ack = True
    one = {"id": "5099999999999999", "text": "first", "attitudes_count": 2}
    two = {"id": "5099999999999998", "text": "second", "attitudes_count": 3}
    source = [one]
    async def fetch(url):
        return '<script type="application/json">' + json.dumps({"items": source}) + '</script>'
    original = Router.execute
    async def execute(self, *args, **kwargs):
        self.fetch_social = {"weibo": fetch}
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(Router, "execute", execute)
    store = Store(tmp_path / "runs.db")
    content = ContentStore(tmp_path / "content.db")
    await store.open()
    await content.open()
    worker = WorkerLoop(cfg, client=FileClient(tmp_path / "in", tmp_path / "out"), store=store)
    worker.content = content
    lease = Lease(lease_id="local", lease_until=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())
    try:
        task = TaskEnvelope(task_id="resume-social", type="weibo.search", params={"query": "q", "max_results": 2, "capture_screenshot": "never"})
        first = await worker._run_claimed(task, lease)
        assert first.status.value == "partial"
        checkpoint = first.result["checkpoint_id"]
        source[:] = [two]
        next_task = task.model_copy(deep=True)
        next_task.attempt = 2
        next_task.params["resume_from"] = checkpoint
        second = await worker._run_claimed(next_task, lease)
        assert second.status.value == "succeeded"
        assert [row["content_id"] for row in second.result["items"]] == [one["id"], two["id"]]
        assert second.result["limit_reached"]
    finally:
        await worker.aclose()
