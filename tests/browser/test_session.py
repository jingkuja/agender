from unittest.mock import AsyncMock

import pytest

from agender.browser.cookies import douyin_session_state
from agender.browser.runtime import BrowserRuntime
from agender.protocol.schemas import SessionState


@pytest.mark.asyncio
async def test_web_cookies_are_never_used_for_douyin(cfg):
    runtime = BrowserRuntime(cfg)
    runtime._context = AsyncMock()
    runtime._site = "web"
    runtime._douyin_cookies = [{"name": "sessionid", "expires": -1}]
    assert await runtime.douyin_session() == SessionState.OK
    runtime.context.cookies.assert_not_awaited()
    runtime._douyin_cookies = [{"name": "sessionid", "expires": 1}]
    assert await runtime.douyin_session() == SessionState.EXPIRED


def test_one_expired_cookie_does_not_hide_other_valid_session_cookie():
    cookies = [{"name": "sessionid", "expires": 1}, {"name": "sid_tt", "expires": -1}]
    assert douyin_session_state(cookies, ["sessionid", "sid_tt"]) == SessionState.OK


def test_profile_rejects_path_traversal(cfg):
    with pytest.raises(ValueError, match="unsupported site"):
        BrowserRuntime(cfg).profile_dir("../../elsewhere")


async def test_backup_validation_rejects_unrelated_cookie_before_apply(cfg):
    from unittest.mock import AsyncMock
    rt = BrowserRuntime(cfg)
    rt.navigator.validate = AsyncMock()
    with pytest.raises(ValueError, match='cookie domain'):
        await rt._validate_state('douyin', {'cookies': [{'name': 'sessionid', 'domain': 'example.org'}]})
    await rt._validate_state('douyin', {'cookies': [{'name': 'sessionid', 'domain': '.douyin.com'}]})


def test_unavailable_browser_does_not_advertise_capabilities(cfg):
    from agender.worker.heartbeat import capabilities
    caps, disabled, status = capabilities(cfg, browser_ready=False, douyin_session=SessionState.OK)
    assert not caps
    assert 'web.search' in disabled
    assert status.value == 'degraded'


def test_same_profile_lock_is_exclusive(tmp_path):
    from agender.browser.lock import FileLock, LockHeld
    first = FileLock(tmp_path / 'douyin.lock')
    second = FileLock(tmp_path / 'douyin.lock')
    first.acquire()
    try:
        with pytest.raises(LockHeld):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_diagnostic_html_removes_credentials_and_scripts():
    from agender.browser.sanitize import sanitize_html
    safe = sanitize_html('<script>sessionid=secret</script><form><input value="password"></form>'
                         '<p data-token="private">Title</p><a href="https://example.org/?token=secret">Link</a>')
    assert 'Title' in safe and 'Link' in safe
    assert all(value not in safe for value in ('secret', 'private', 'input', '<script', '<form', 'data-token'))
