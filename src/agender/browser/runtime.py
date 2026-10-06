from __future__ import annotations

import asyncio
import contextlib
import random
from pathlib import Path
from typing import Any

from agender.browser.cookies import apply_storage_state, douyin_session_state, load_storage_state, session_state
from agender.browser.egress import EgressProxy
from agender.browser.health import SiteHealth
from agender.browser.hygiene import ACCEPT_LANGUAGE, EXTRA_ARGS, IGNORE_DEFAULT_ARGS
from agender.browser.lock import FileLock
from agender.browser.navigator import Navigator
from agender.browser.rate_limit import RateLimiter
from agender.config import AppConfig
from agender.platforms import SESSION_SITES, SITES, Site, belongs_to_site
from agender.protocol.schemas import SessionState


class BrowserRuntime:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._pw: Any = None
        self._context: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._site: Site | None = None
        self.browser_ready = False
        self._profile_lock: FileLock | None = None
        self._douyin_cookies: list[dict] | None = None
        self._session_cookies: dict[str, list[dict]] = {}
        self.egress = EgressProxy(cfg)
        self.rate_limit = RateLimiter(cfg.limits.per_host_rpm)
        self.health = SiteHealth(cfg.health.failure_threshold, cfg.health.cooldown_s)
        self.navigator = Navigator(self)

    @property
    def site(self) -> Site | None:
        return self._site

    @property
    def context(self) -> Any:
        return self._context

    @property
    def active_page(self) -> Any:
        return self._page

    def profile_dir(self, site: Site) -> Path:
        if site not in SITES:
            raise ValueError("unsupported site")
        return self.cfg.path(self.cfg.browser.profile_dir) / site

    def backup_path(self, site: Site) -> Path | None:
        if site not in SESSION_SITES:
            return None
        return self.cfg.path(getattr(self.cfg.browser.session_backup, site))

    def headless_for(self, site: Site) -> bool:
        mode = self.cfg.browser.headless
        if mode == "auto":
            return site == "web"
        if isinstance(mode, bool):
            return mode
        return str(mode).lower() == "true"

    async def start(self, site: Site) -> None:
        self.profile_dir(site)  # validate before closing an existing context
        if self._context is not None and self._site == site and self.browser_ready and self._page is not None and not self._page.is_closed():
            return
        await self.close_context()
        self.navigator.site = site
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()
        if self.cfg.browser.mode == "cdp":
            await self._connect_cdp(site)
            return
        await self.egress.start()
        profile = self.profile_dir(site)
        self._profile_lock = FileLock(profile.with_name(profile.name + ".lock"))
        try:
            self._profile_lock.acquire()
        except BaseException:
            self._profile_lock = None
            raise
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
            "args": [*EXTRA_ARGS, "--proxy-bypass-list=<-loopback>", "--disable-quic"],
            "headless": self.headless_for(site),
            "slow_mo": self.cfg.browser.slow_mo_ms,
            "accept_downloads": False,
            "channel": self.cfg.browser.channel,
            "proxy": {"server": self.egress.url},
            "service_workers": "block",
        }
        try:
            self._context = await self._pw.chromium.launch_persistent_context(str(profile), **launch_kwargs)
        except Exception:
            launch_kwargs.pop("channel", None)
            try:
                self._context = await self._pw.chromium.launch_persistent_context(str(profile), **launch_kwargs)
            except BaseException:
                self._profile_lock.release()
                self._profile_lock = None
                raise
        self._site = site
        self._context.on("close", lambda *_: setattr(self, "browser_ready", False))
        await self._context.route("**/*", self.navigator.guard)
        if hasattr(self._context, "route_web_socket"):
            await self._context.route_web_socket("**/*", lambda route: route.close())
        await self._context.set_extra_http_headers({"Accept-Language": ACCEPT_LANGUAGE})
        self._context.set_default_navigation_timeout(self.cfg.browser.navigation_timeout_ms)
        if seed_empty and backup and backup.exists():
            state = load_storage_state(backup)
            await self._validate_state(site, state)
            await apply_storage_state(self._context, state)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self.browser_ready = True

    async def _connect_cdp(self, site: Site) -> None:
        try:
            if self._browser is None or not self._browser.is_connected():
                self._browser = await self._pw.chromium.connect_over_cdp(
                    self.cfg.browser.cdp_endpoint_url, timeout=self.cfg.browser.cdp_timeout_ms,
                    slow_mo=self.cfg.browser.slow_mo_ms,
                )
                self._browser.on("disconnected", lambda *_: setattr(self, "browser_ready", False))
            if not self._browser.contexts:
                raise RuntimeError("CDP Chrome has no default context")
            self._context = self._browser.contexts[0]
            self._site = site
            # Do not change routes, headers, timeouts, cookies or permissions on
            # the shared context. All automation is scoped to this owned tab.
            self._page = await self._context.new_page()
            await self._page.route("**/*", self.navigator.guard)
            if hasattr(self._page, "route_web_socket"):
                await self._page.route_web_socket("**/*", lambda route: route.close())
            self._page.set_default_navigation_timeout(self.cfg.browser.navigation_timeout_ms)
            self._page.on("close", lambda *_: setattr(self, "browser_ready", False))
            self.browser_ready = True
        except BaseException as exc:
            with contextlib.suppress(Exception):
                await self.close_context()
            if not isinstance(exc, Exception):
                raise
            raise BrowserConnectionError(
                "CDP connection failed. Start Chrome with --remote-debugging-port=9222 "
                "and a dedicated --user-data-dir, set AGENDER_CDP_ENDPOINT_URL, "
                "or set AGENDER_BROWSER_MODE=playwright."
            ) from exc

    async def close_context(self) -> None:
        try:
            if self._context is not None:
                if self._site in SESSION_SITES:
                    with contextlib.suppress(Exception):
                        await self.session(self._site)
                if self.cfg.browser.mode == "cdp":
                    if self._page is not None and not self._page.is_closed():
                        await self._page.close()
                else:
                    await self._context.close()
        finally:
            self._page = None
            self._context = None
            self._site = None
            self.browser_ready = False
            if self._profile_lock:
                self._profile_lock.release()
                self._profile_lock = None

    async def aclose(self) -> None:
        try:
            await self.close_context()
        finally:
            try:
                if self._pw is not None:
                    await self._pw.stop()
                    self._pw = None
                self._browser = None
            finally:
                self.browser_ready = False
                await self.egress.close()

    async def page(self, site: Site) -> Any:
        await self.start(site)
        assert self._page is not None
        return self._page

    async def pause(self) -> None:
        lo, hi = self.cfg.browser.action_delay_ms
        await asyncio.sleep(random.uniform(lo, hi) / 1000)

    async def douyin_session(self) -> SessionState:
        if self.cfg.browser.mode != "cdp" and (self._context is None or self._site != "douyin"):
            if self._douyin_cookies is not None:
                return douyin_session_state(self._douyin_cookies, self.cfg.douyin.session_cookie_names)
            profile = self.profile_dir("douyin")
            if not profile.exists() or not any(profile.iterdir()):
                return SessionState.MISSING
            return SessionState.UNKNOWN
        if self._context is None:
            return SessionState.UNKNOWN
        cookies = await self._context.cookies(["https://www.douyin.com/"])
        self._douyin_cookies = cookies
        return douyin_session_state(cookies, self.cfg.douyin.session_cookie_names)

    async def session(self, site: str) -> SessionState:
        if site == "douyin":
            return await self.douyin_session()
        if site not in SESSION_SITES:
            return SessionState.UNKNOWN
        config = getattr(self.cfg, site)
        if self._context is not None and (self.cfg.browser.mode == "cdp" or self._site == site):
            cookies = await self._context.cookies([config.base_url])
            self._session_cookies[site] = cookies
        else:
            cookies = self._session_cookies.get(site)
            if cookies is None:
                profile = self.profile_dir(site)
                return SessionState.UNKNOWN if profile.exists() and any(profile.iterdir()) else SessionState.MISSING
        return session_state(cookies, config.session_cookie_names)

    async def export_state(self, dest: Path) -> None:
        if self._context is None:
            raise RuntimeError("no context")
        if self.cfg.browser.mode == "cdp" and self._site == "web":
            raise ValueError("CDP session export requires a specific platform")
        state = await self._context.storage_state()
        if self._site in SESSION_SITES:
            # CDP contexts contain unrelated sessions; never export those.
            from urllib.parse import urlsplit
            state["cookies"] = [c for c in state.get("cookies", []) if belongs_to_site(str(c.get("domain", "")).lstrip("."), self._site)]
            state["origins"] = [o for o in state.get("origins", []) if belongs_to_site(urlsplit(o.get("origin", "")).hostname or "", self._site)]
        dest.parent.mkdir(parents=True, exist_ok=True)
        from agender.store.atomic import atomic_json
        atomic_json(dest, state)

    async def import_state(self, site: Site, src: Path) -> None:
        self.profile_dir(site)
        self.navigator.configure(site)
        state = load_storage_state(src)
        await self._validate_state(site, state)
        if self.cfg.browser.mode == "cdp" and site == "web":
            raise ValueError("CDP session import requires a specific platform")
        await self.start(site)
        await apply_storage_state(self._context, state, guard=self.navigator.guard)
        backup = self.backup_path(site)
        if backup:
            await self.export_state(backup)

    async def _validate_state(self, site: Site, state: dict) -> None:
        from urllib.parse import urlsplit
        for origin in state.get("origins", []):
            if site in SESSION_SITES and not belongs_to_site(urlsplit(origin["origin"]).hostname or "", site):
                raise ValueError("storage origin does not belong to site")
            await self.navigator.validate(origin["origin"], document=True)
        for cookie in state.get("cookies", []):
            domain = str(cookie.get("domain", "")).lstrip(".")
            if site in SESSION_SITES and not belongs_to_site(domain, site):
                raise ValueError("cookie domain does not belong to site")
            if not domain or urlsplit("https://" + domain).hostname != domain:
                raise ValueError("invalid cookie domain")


class BrowserConnectionError(RuntimeError):
    code = "BROWSER_UNAVAILABLE"
