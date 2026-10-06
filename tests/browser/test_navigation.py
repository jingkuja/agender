import asyncio
from urllib.parse import urlsplit

import httpx
import pytest

from agender.browser.egress import EgressProxy
from agender.browser.health import SiteHealth
from agender.browser.rate_limit import RateLimiter
from agender.extractors.web.policy import PolicyDecision, PolicyDenied
from agender.worker.execution import ExecutionContext, ExecutionStopped


@pytest.mark.asyncio
async def test_proxy_pins_checked_ip_and_denies_redirect(cfg, local_site):
    base, _, requests = local_site
    port = urlsplit(base).port
    checked = []
    async def validate(url):
        checked.append(url)
        return PolicyDecision(urlsplit(url).hostname == "public.test", resolved=["127.0.0.1"])
    proxy = EgressProxy(cfg, validator=validate)
    await proxy.start()
    try:
        async with httpx.AsyncClient(proxy=proxy.url, trust_env=False, follow_redirects=True) as client:
            response = await client.get(f"http://public.test:{port}/article")
            assert response.status_code == 200  # public.test has no DNS entry; only the checked IP is dialed
            response = await client.get(f"http://public.test:{port}/blocked")
            assert response.status_code == 403
        assert "/private" not in requests
        assert any("localhost" in url for url in checked)
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_browser_redirect_preserves_final_url_and_blocks_hop(browser_runtime, local_site):
    base, _, requests = local_site
    nav = browser_runtime.navigator
    page = await nav.open(base + "/redirect", "web")
    assert page.final_url == base + "/article"
    assert "Verified locally" in page.html
    with pytest.raises(PolicyDenied, match="navigation") as error:
        await nav.open(base + "/blocked", "web")
    assert error.value.code == "POLICY_DENIED"
    assert "/private" not in requests


@pytest.mark.asyncio
async def test_rate_wait_can_be_cancelled():
    limiter = RateLimiter({"default": 1})
    await limiter.acquire("example.test")
    execution = ExecutionContext()
    wait = asyncio.create_task(limiter.acquire("example.test", execution))
    await asyncio.sleep(0)
    execution.cancel("WORKER_SHUTDOWN")
    with pytest.raises(ExecutionStopped, match="WORKER_SHUTDOWN"):
        await asyncio.wait_for(wait, 0.2)


def test_circuit_reopens_then_resets_after_success():
    now = [0]
    health = SiteHealth(2, 10, now=lambda: now[0])
    health.record("douyin", "CAPTCHA")
    assert health.available("douyin")
    health.record("douyin", "CAPTCHA")
    assert not health.available("douyin")
    now[0] = 11
    assert health.available("douyin")
    health.record("douyin", None)
    assert not health.failures


@pytest.mark.asyncio
async def test_dynamic_bundle_collects_virtual_comments_and_replies_once(browser_runtime, local_site):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agender.browser.navigator import PageSnapshot
    from agender.extractors.douyin.tool import DouyinTool
    from agender.protocol.schemas import DouyinVideoParams, SessionState

    base, pages, _ = local_site
    pages["/video"] = '''<html><body>
      <h1 data-e2e="video-desc">动态视频</h1><a data-e2e="video-author-name">作者</a>
      <div data-e2e="comment-list" id="panel" style="height:80px;overflow:auto">
        <div data-e2e="comment-item" data-id="c1" data-reply-count="1" style="height:200px">
          <p data-e2e="comment-text">评论1</p>
          <button data-e2e="expand-replies" onclick="this.insertAdjacentHTML('beforebegin', '<div data-e2e=reply-item data-id=r1><p data-e2e=comment-text>回复1</p></div>');this.remove()">展开回复</button>
        </div>
      </div>
      <script>
      let n=1; const panel=document.getElementById('panel');
      panel.addEventListener('wheel',()=>{if(n>=3)return;n++;
        panel.innerHTML='<div data-e2e="comment-item" data-id="c'+n+'" style="height:200px"><p data-e2e="comment-text">评论'+n+'</p></div>'+(n===3?'<div data-e2e="comment-end">没有更多评论</div>':'');
      });
      </script></body></html>'''
    browser_runtime.cfg.douyin.load_wait_s = 0.3
    browser_runtime.douyin_session = AsyncMock(return_value=SessionState.OK)
    opens = []
    async def open_video(url, site, *, ready_selector=None):
        opens.append(url)
        snap = await browser_runtime.navigator.open(base + "/video", "web", ready_selector=ready_selector)
        return PageSnapshot(snap.html, url, "https://www.douyin.com/video/7123456789012345678")
    driver = SimpleNamespace(runtime=browser_runtime, open=open_video, load_action=browser_runtime.navigator.load_action)
    tool = DouyinTool(browser_runtime.cfg, None, session_state=SessionState.OK, navigator=driver)
    result = await tool.video_bundle(DouyinVideoParams(video_id="7123456789012345678", max_comments=3, include_replies=True))
    assert len(opens) == 1
    assert [row["comment_id"] for row in result["comments"]] == ["c1", "c2", "c3"]
    assert result["comments"][0]["replies"][0]["text"] == "回复1"
    assert result["limit_reached"] is True and result["exhausted"] is False
    assert result["video"]["title"] == "动态视频"


@pytest.mark.asyncio
async def test_rendered_web_article_through_router(browser_runtime, local_site):
    from agender.extractors.web.policy import clear_robots_cache
    from agender.protocol.schemas import SessionState, TaskType
    from agender.worker.router import Router

    clear_robots_cache()
    base, pages, _ = local_site
    pages["/robots.txt"] = "User-agent: *\nAllow: /\n"
    router = Router(browser_runtime.cfg, browser_runtime)
    result = await router.execute(TaskType.WEB_FETCH_ARTICLE, {"urls": [base + "/redirect"]}, session=SessionState.MISSING, deadline=None)
    assert result["documents"][0]["fetch_status"] == "ok"
    assert result["documents"][0]["canonical_url"] == base + "/article"
    assert result["documents"][0]["requested_url"] == base + "/redirect"
