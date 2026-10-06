from __future__ import annotations

import asyncio
from typing import Any

from agender.browser.runtime import BrowserRuntime
from agender.protocol.schemas import SessionLoginParams, SessionState

LOGIN_SELECTORS = {
    "username": 'input[type="text"], input[placeholder*="手机"], input[placeholder*="账号"], input[name="username"]',
    "password": 'input[type="password"]',
    "submit": 'button[type="submit"], button:has-text("登录")',
    "avatar": '[data-e2e="user-info"], img[alt*="头像"], .avatar, [class*="avatar"]',
    "captcha": '[class*="captcha"], [id*="captcha"], iframe[src*="captcha"]',
}


async def _visible(page: Any, selector: str) -> bool:
    loc = page.locator(selector).first
    try:
        return await loc.is_visible(timeout=1500)
    except Exception:
        return False


async def login_heuristic(page: Any, session: SessionState) -> bool:
    if session != SessionState.OK:
        return False
    if await _visible(page, LOGIN_SELECTORS["avatar"]):
        return True
    try:
        uid = await page.evaluate(
            """() => {
              const t = document.documentElement.innerHTML;
              const m = t.match(/"user_id"\\s*:\\s*"?(\\d{5,})"?/);
              return m ? m[1] : null;
            }"""
        )
        return bool(uid)
    except Exception:
        return False


async def run_session_login(
    runtime: BrowserRuntime,
    params: SessionLoginParams,
    *,
    confirm_cli: bool = False,
    timeout_s: float = 300,
) -> dict[str, Any]:
    """Open public Douyin login page. Never persist the password."""
    password = params.password
    params.password = None
    page = await runtime.page("douyin")
    if runtime.headless_for("douyin"):
        # login is always headed
        await runtime.close_context()
        runtime.cfg.browser.headless = False
        page = await runtime.page("douyin")
    await page.goto(runtime.cfg.douyin.login_url, wait_until="domcontentloaded")
    method = "interactive"
    try:
        if params.username:
            user_box = page.locator(LOGIN_SELECTORS["username"]).first
            if await user_box.count():
                await user_box.fill(params.username)
        if password:
            method = "form_fill_then_interactive"
            pw_box = page.locator(LOGIN_SELECTORS["password"]).first
            if await pw_box.count():
                await pw_box.fill(password)
                submit = page.locator(LOGIN_SELECTORS["submit"]).first
                if await submit.count():
                    await submit.click()
    finally:
        password = None

    deadline = asyncio.get_event_loop().time() + timeout_s
    captcha_seen = False
    while asyncio.get_event_loop().time() < deadline:
        if await _visible(page, LOGIN_SELECTORS["captcha"]):
            captcha_seen = True
        session = await runtime.douyin_session()
        if await login_heuristic(page, session):
            if confirm_cli:
                return {
                    "kind": "session.login",
                    "site": "douyin",
                    "sessions": {"douyin": session.value},
                    "method": method,
                    "username_present": bool(params.username),
                    "needs_cli_confirm": True,
                }
            backup = runtime.backup_path("douyin")
            if backup:
                await runtime.export_state(backup)
            return {
                "kind": "session.login",
                "site": "douyin",
                "sessions": {"douyin": SessionState.OK.value},
                "method": method,
                "username_present": bool(params.username),
            }
        await asyncio.sleep(1.5)

    session = await runtime.douyin_session()
    code = "CAPTCHA" if captcha_seen else "LOGIN_WALL"
    return {
        "kind": "session.login",
        "site": "douyin",
        "sessions": {"douyin": session.value},
        "method": method,
        "username_present": bool(params.username),
        "error_code": code,
    }
