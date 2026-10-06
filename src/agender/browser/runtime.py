from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any, Literal

from agender.browser.cookies import apply_storage_state, douyin_session_state, load_storage_state
from agender.browser.hygiene import ACCEPT_LANGUAGE, EXTRA_ARGS, IGNORE_DEFAULT_ARGS
from agender.config import AppConfig
from agender.protocol.schemas import SessionState

Site = Literal["web", "douyin"]


class BrowserRuntime:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._pw: Any = None
        self._context: Any = None
        self._site: Site | None = None
        self.browser_ready = False

    @property
    def site(self) -> Site | None:
        return self._site

    @property
    def context(self) -> Any:
        return self._context

    def profile_dir(self, site: Site) -> Path:
        return self.cfg.path(self.cfg.browser.profile_dir) / site

    def backup_path(self, site: Site) -> Path | None:
        if site != "douyin":
            return None
        return self.cfg.path(self.cfg.browser.session_backup.douyin)

    def headless_for(self, site: Site) -> bool:
        mode = self.cfg.browser.headless
        if mode == "auto":
            return site == "web"
        if isinstance(mode, bool):
            return mode
        return str(mode).lower() == "true"

    async def start(self, site: Site) -> None:
        if self._context is not None and self._site == site:
            return
        await self.close_context()
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()
        profile = self.profile_dir(site)
        profile.mkdir(parents=True, exist_ok=True)
        backup = self.backup_path(site)
        seed_empty = not any(profile.iterdir())
        launch_kwargs: dict[str, Any] = {
            "locale": self.cfg.browser.locale,
            "timezone_id": self.cfg.browser.timezone_id,
            "viewport": {
                "width": self.cfg.browser.viewport.width,
                "height": self.cfg.browser.viewport.height,
            },
            "ignore_default_args": IGNORE_DEFAULT_ARGS,
            "args": EXTRA_ARGS,
            "headless": self.headless_for(site),
            "slow_mo": self.cfg.browser.slow_mo_ms,
            "accept_downloads": False,
            "channel": self.cfg.browser.channel,
        }
        try:
            self._context = await self._pw.chromium.launch_persistent_context(str(profile), **launch_kwargs)
        except Exception:
            launch_kwargs.pop("channel", None)
            self._context = await self._pw.chromium.launch_persistent_context(str(profile), **launch_kwargs)
        await self._context.set_extra_http_headers({"Accept-Language": ACCEPT_LANGUAGE})
        self._context.set_default_navigation_timeout(self.cfg.browser.navigation_timeout_ms)
        if seed_empty and backup and backup.exists():
            state = load_storage_state(backup)
            await apply_storage_state(self._context, state)
        self._site = site
        self.browser_ready = True

    async def close_context(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
            self._site = None

    async def aclose(self) -> None:
        await self.close_context()
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        self.browser_ready = False

    async def page(self, site: Site) -> Any:
        await self.start(site)
        assert self._context is not None
        pages = self._context.pages
        if pages:
            return pages[0]
        return await self._context.new_page()

    async def pause(self) -> None:
        lo, hi = self.cfg.browser.action_delay_ms
        await asyncio.sleep(random.uniform(lo, hi) / 1000)

    async def douyin_session(self) -> SessionState:
        if self._context is None:
            backup = self.backup_path("douyin")
            if backup and backup.exists():
                try:
                    state = load_storage_state(backup)
                    return douyin_session_state(state.get("cookies") or [], self.cfg.douyin.session_cookie_names)
                except ValueError:
                    return SessionState.UNKNOWN
            profile = self.profile_dir("douyin")
            if not profile.exists() or not any(profile.iterdir()):
                return SessionState.MISSING
            return SessionState.UNKNOWN
        cookies = await self._context.cookies()
        return douyin_session_state(cookies, self.cfg.douyin.session_cookie_names)

    async def export_state(self, dest: Path) -> None:
        if self._context is None:
            raise RuntimeError("no context")
        state = await self._context.storage_state()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            __import__("json").dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def import_state(self, site: Site, src: Path) -> None:
        await self.start(site)
        state = load_storage_state(src)
        await apply_storage_state(self._context, state)
        backup = self.backup_path(site)
        if backup:
            await self.export_state(backup)
