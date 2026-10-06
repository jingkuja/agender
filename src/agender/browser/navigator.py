from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from agender.extractors.web.policy import MAX_HOPS, PolicyDenied, check_url
from agender.platforms import SESSION_SITES
from agender.worker.execution import ExecutionContext, ExecutionStopped


@dataclass
class PageSnapshot:
    html: str
    requested_url: str
    final_url: str
    status: int = 200


class Navigator:
    def __init__(self, runtime):
        self.runtime = runtime
        self.cfg = runtime.cfg
        self.execution: ExecutionContext | None = None
        self.allowlist: list[str] = []
        self.denylist: list[str] = []
        self.site = "web"
        self._blocked: str | None = None
        self._final_url: str | None = None
        self._status = 200

    def configure(self, site: str, *, execution=None, allowlist=None, denylist=None) -> None:
        self.site, self.execution = site, execution
        self.allowlist, self.denylist = allowlist or [], denylist or []
        self.runtime.egress.allowlist = self.allowlist
        self.runtime.egress.denylist = self.denylist

    async def validate(self, url: str, *, document: bool = False) -> None:
        if self.execution:
            self.execution.check()
        decision = await asyncio.to_thread(check_url, url, self.cfg, allowlist=self.allowlist, extra_denylist=self.denylist)
        if not decision.ok:
            raise PolicyDenied(decision.code or "POLICY_DENIED", decision.message)
        if document and self.site in SESSION_SITES and urlsplit(url).hostname not in getattr(self.cfg, self.site).redirect_allowlist:
            raise PolicyDenied("UNSUPPORTED_URL", f"{self.site} document host is not allowlisted")

    async def guard(self, route, request) -> None:
        """Fetch redirects explicitly: Playwright route handlers alone skip redirect hops."""
        document = request.is_navigation_request()
        main = document and request.frame.parent_frame is None
        try:
            if request.resource_type == "media":
                await route.abort()
                return
            url = request.url
            method = request.method
            data = request.post_data_buffer
            headers = await request.all_headers()
            initial_origin = (urlsplit(url).scheme, urlsplit(url).netloc)
            for hop in range(MAX_HOPS + 1):
                await self.validate(url, document=document)
                origin = (urlsplit(url).scheme, urlsplit(url).netloc)
                scoped = {k: v for k, v in headers.items() if k.lower() not in {"cookie", "host", "content-length"}}
                if origin != initial_origin:
                    scoped.pop("authorization", None)
                cookies = await self.runtime.context.cookies([url])
                if cookies:
                    scoped["cookie"] = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                response = await route.fetch(url=url, method=method, post_data=data, headers=scoped, max_redirects=0,
                                             timeout=self.cfg.browser.navigation_timeout_ms)
                if response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                    next_url = urljoin(url, response.headers["location"])
                    await response.dispose()
                    if hop == MAX_HOPS:
                        raise PolicyDenied("POLICY_DENIED", "too many redirects")
                    url = next_url
                    if method == "POST" and response.status in {301, 302, 303}:
                        method, data = "GET", b""
                    continue
                if main:
                    self._final_url, self._status = url, response.status
                try:
                    await route.fulfill(response=response)
                finally:
                    await response.dispose()
                return
        except (ExecutionStopped, PolicyDenied) as exc:
            if main:
                self._blocked = exc.code
            with contextlib.suppress(Exception):
                await route.abort()
        except Exception:
            if main:
                self._blocked = "NAVIGATION_TIMEOUT"
            with contextlib.suppress(Exception):
                await route.abort()

    async def open(self, url: str, site: str, *, ready_selector: str | None = None) -> PageSnapshot:
        self.site = site
        await self.validate(url, document=True)
        await self.runtime.rate_limit.acquire(urlsplit(url).hostname or "", self.execution)
        page = await self.runtime.page(site)
        requested = url
        # Manual redirect fetching uses fulfill; revisit a changed final URL so
        # location, relative assets and storage origin match the destination.
        for _ in range(MAX_HOPS + 1):
            self._blocked, self._final_url, self._status = None, None, 200
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except Exception:
                if self.execution:
                    self.execution.check()
                raise PolicyDenied(self._blocked or "NAVIGATION_TIMEOUT", "navigation failed")
            if self._blocked:
                raise PolicyDenied(self._blocked, "navigation blocked")
            final = self._final_url or page.url
            await self.validate(final, document=True)
            if final == page.url:
                break
            url = final
        else:
            raise PolicyDenied("POLICY_DENIED", "redirect destination is unstable")
        if self._status == 429:
            raise PolicyDenied("RATE_LIMITED", "site rate limited")
        if self._status >= 400:
            raise PolicyDenied("HTTP_ERROR", f"HTTP {self._status}")
        if ready_selector:
            try:
                await page.locator(ready_selector).first.wait_for(state="attached", timeout=15000)
            except Exception:
                if self.execution:
                    self.execution.check()
                # The extractor distinguishes empty content from a changed selector.
        if self.execution:
            self.execution.check()
            await self.execution.progress("navigation")
        return PageSnapshot(await page.content(), requested, page.url, self._status)

    async def load_action(self) -> None:
        if self.execution:
            self.execution.check()
        page = self.runtime.active_page
        await self.runtime.rate_limit.acquire(urlsplit(page.url).hostname or "", self.execution)

    async def robots(self, url: str) -> str:
        await self.runtime.egress.start()
        async with httpx.AsyncClient(proxy=self.runtime.egress.url, trust_env=False, timeout=10, follow_redirects=False) as client:
            for hop in range(MAX_HOPS + 1):
                await self.validate(url)
                response = await client.get(url, headers={"User-Agent": "AgenderWorker/0.1"})
                if response.is_redirect and response.headers.get("location"):
                    if hop == MAX_HOPS:
                        raise PolicyDenied("POLICY_DENIED", "too many robots redirects")
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code == 404:
                    return ""
                response.raise_for_status()
                return response.text
        return ""
