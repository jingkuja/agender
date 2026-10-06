from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agender.protocol.schemas import SessionState

SESSION_OK_NAMES = ("sessionid", "sid_tt")


def load_storage_state(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "cookies" not in raw:
        raise ValueError("storage_state must be JSON with cookies[]")
    cookies = raw.get("cookies") or []
    if not isinstance(cookies, list):
        raise ValueError("cookies must be a list")
    origins = raw.get("origins") or []
    if origins and not isinstance(origins, list):
        raise ValueError("origins must be a list")
    return {"cookies": cookies, "origins": origins}


def douyin_session_state(cookies: list[dict[str, Any]], names: list[str]) -> SessionState:
    selected = [c for c in cookies if c.get("name") in names]
    state = session_state(selected, list(SESSION_OK_NAMES))
    return SessionState.EXPIRED if selected and state == SessionState.MISSING else state


def session_state(cookies: list[dict[str, Any]], names: list[str]) -> SessionState:
    now = datetime.now(UTC).timestamp()
    present = {c.get("name"): c for c in cookies if c.get("name") in names}
    if not present:
        return SessionState.MISSING
    expired = False
    for key in names:
        cookie = present.get(key)
        if not cookie:
            continue
        expires = cookie.get("expires") or cookie.get("expiry")
        if expires in (None, -1, "-1"):
            return SessionState.OK
        try:
            exp = float(expires)
        except (TypeError, ValueError):
            return SessionState.OK
        if exp > now + 300:
            return SessionState.OK
        expired = True
    if expired:
        return SessionState.EXPIRED
    named = [c for c in present.values() if c.get("name") in names]
    if named:
        return SessionState.EXPIRED
    return SessionState.MISSING


async def apply_storage_state(context: Any, state: dict[str, Any], *, guard=None) -> None:
    cookies = state.get("cookies") or []
    if cookies:
        await context.add_cookies(cookies)
    for origin in state.get("origins") or []:
        origin_url = origin.get("origin")
        ls = origin.get("localStorage") or []
        if not origin_url:
            continue
        page = await context.new_page()
        try:
            if guard:
                await page.route("**/*", guard)
            await page.goto(origin_url, wait_until="domcontentloaded")
            for item in ls:
                name = item.get("name")
                value = item.get("value")
                if name is None:
                    continue
                await page.evaluate(
                    "([k, v]) => localStorage.setItem(k, v)",
                    [name, value],
                )
        finally:
            await page.close()
