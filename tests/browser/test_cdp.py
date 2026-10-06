import json
from unittest.mock import AsyncMock, Mock

import pytest

from agender.browser.runtime import BrowserConnectionError, BrowserRuntime
from agender.protocol.schemas import SessionState


@pytest.fixture
def cdp(cfg, monkeypatch):
    cfg.browser.mode = "cdp"
    original = Mock()
    first, second = Mock(), Mock()
    for page in (first, second):
        page.is_closed.return_value = False
        page.route = AsyncMock()
        page.route_web_socket = AsyncMock()
        page.close = AsyncMock()
    context = Mock(pages=[original], new_page=AsyncMock(side_effect=[first, second]), cookies=AsyncMock(return_value=[]))
    browser = Mock(contexts=[context])
    browser.is_connected.return_value = True
    pw = Mock(stop=AsyncMock())
    pw.chromium.connect_over_cdp = AsyncMock(return_value=browser)
    pw.chromium.launch_persistent_context = AsyncMock()
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: Mock(start=AsyncMock(return_value=pw)))
    runtime = BrowserRuntime(cfg)
    runtime.egress.start = AsyncMock()
    return runtime, pw, browser, context, original, first, second


async def test_cdp_owns_only_its_tabs_and_does_not_close_chrome(cdp):
    runtime, pw, browser, context, original, first, second = cdp
    try:
        await runtime.start("web")
        assert await runtime.page("web") is first
        await runtime.start("xiaohongshu")
        assert runtime.active_page is second
        first.close.assert_awaited_once()
        assert not original.mock_calls
        context.route.assert_not_called()
        context.set_extra_http_headers.assert_not_called()
        pw.chromium.launch_persistent_context.assert_not_awaited()
        runtime.egress.start.assert_not_awaited()
        pw.chromium.connect_over_cdp.assert_awaited_once_with("http://127.0.0.1:9222", timeout=10000, slow_mo=50)
    finally:
        await runtime.aclose()
    second.close.assert_awaited_once()
    context.close.assert_not_called()
    browser.close.assert_not_called()
    pw.stop.assert_awaited_once()


async def test_cdp_error_never_silently_launches_playwright(cdp):
    runtime, pw, _, _, _, _, _ = cdp
    pw.chromium.connect_over_cdp.side_effect = RuntimeError("connection refused")
    with pytest.raises(BrowserConnectionError, match="AGENDER_BROWSER_MODE"):
        await runtime.start("weibo")
    assert not runtime.browser_ready
    assert runtime.context is None
    pw.chromium.launch_persistent_context.assert_not_awaited()
    await runtime.aclose()


async def test_cdp_scopes_cookie_reads_and_exports(cdp, tmp_path):
    runtime, _, _, context, _, _, _ = cdp
    context.cookies.return_value = [{"name": "web_session", "domain": ".xiaohongshu.com", "expires": -1}]
    context.storage_state = AsyncMock(return_value={
        "cookies": [{"name": "web_session", "domain": ".xiaohongshu.com", "value": "xhs"},
                    {"name": "secret", "domain": ".other.example", "value": "unrelated"}],
        "origins": [{"origin": "https://www.xiaohongshu.com", "localStorage": []},
                    {"origin": "https://other.example", "localStorage": []}],
    })
    try:
        await runtime.start("xiaohongshu")
        assert await runtime.session("xiaohongshu") == SessionState.OK
        context.cookies.assert_awaited_with(["https://www.xiaohongshu.com"])
        dest = tmp_path / "session.json"
        await runtime.export_state(dest)
        state = json.loads(dest.read_text())
        assert len(state["cookies"]) == len(state["origins"]) == 1
        assert "unrelated" not in dest.read_text()
        await runtime.start("web")
        with pytest.raises(ValueError, match="specific platform"):
            await runtime.export_state(dest)
    finally:
        await runtime.aclose()


async def test_import_rejects_other_platform_before_connect(cdp, tmp_path):
    runtime, pw, _, _, _, _, _ = cdp
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"cookies": [{"name": "SUB", "domain": ".weibo.com"}]}))
    with pytest.raises(ValueError, match="cookie domain"):
        await runtime.import_state("bilibili", path)
    pw.chromium.connect_over_cdp.assert_not_awaited()


async def test_cdp_reconnects_after_chrome_disconnect(cdp):
    runtime, pw, browser, _, _, _, _ = cdp
    try:
        await runtime.start("web")
        browser.is_connected.return_value = False
        runtime.browser_ready = False
        await runtime.start("web")
        assert pw.chromium.connect_over_cdp.await_count == 2
    finally:
        await runtime.aclose()
