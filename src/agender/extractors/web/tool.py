from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from agender.config import AppConfig
from agender.extractors.web.article import extract_article
from agender.extractors.web.hotlists import parse_baidu_hot
from agender.extractors.web.policy import check_url, robots_allowed
from agender.extractors.web.search_baidu import baidu_blocked, parse_baidu
from agender.extractors.web.search_bing import bing_blocked, parse_bing
from agender.protocol.schemas import (
    WebFetchParams,
    WebHotListParams,
    WebResearchParams,
    WebSearchParams,
)

Fetcher = Callable[[str], Awaitable[str]]


class SoftDeadline(Exception):
    pass


def bing_url(query: str, page: int) -> str:
    first = 1 + (page - 1) * 10
    return f"https://cn.bing.com/search?q={quote_plus(query)}&first={first}"


def baidu_url(query: str, page: int) -> str:
    pn = (page - 1) * 10
    return f"https://www.baidu.com/s?wd={quote_plus(query)}&pn={pn}"


class WebTool:
    def __init__(self, cfg: AppConfig, fetch: Fetcher, *, now_mono: Callable[[], float] | None = None):
        self.cfg = cfg
        self.fetch = fetch
        self._now = now_mono or asyncio.get_event_loop().time
        self.deadline: float | None = None

    def remaining(self) -> float:
        if self.deadline is None:
            return 1e9
        return self.deadline - self._now()

    def _robots_fetch(self, url: str) -> str:
        with httpx.Client(timeout=10.0, follow_redirects=True, trust_env=False) as client:
            r = client.get(url, headers={"User-Agent": "AgenderWorker/0.1"})
            r.raise_for_status()
            return r.text

    def _gate(self, url: str, params: WebSearchParams | WebFetchParams | WebResearchParams) -> tuple[bool, str | None, bool]:
        allow = getattr(params, "allowlist", []) or []
        deny = getattr(params, "denylist", []) or []
        decision = check_url(url, self.cfg, allowlist=allow, extra_denylist=deny)
        if not decision.ok:
            return False, decision.code, False
        respect = params.respect_robots if params.respect_robots is not None else self.cfg.web.respect_robots
        allowed, would_block = robots_allowed(url, fetch=self._robots_fetch, respect=respect)
        if not allowed:
            return False, "POLICY_ROBOTS", would_block
        return True, None, would_block

    async def _get_html(self, url: str) -> str:
        if self.remaining() <= 0:
            raise SoftDeadline()
        return await self.fetch(url)

    async def search(self, params: WebSearchParams) -> dict[str, Any]:
        engine = params.search_engine or self.cfg.web.default_search_engine
        order = ["bing_cn", "baidu"] if engine == "auto" else [engine]
        fallback = False
        last_block = False
        for i, eng in enumerate(order):
            items, blocked, pages = await self._search_engine(eng, params)
            if blocked:
                last_block = True
                if engine == "auto" and i == 0 and len(order) > 1:
                    fallback = True
                    continue
                return {
                    "kind": "web.search",
                    "query": params.query,
                    "search_engine": engine,
                    "engine_used": eng,
                    "engine_fallback": fallback,
                    "serp": [],
                    "returned": 0,
                    "pages_fetched": pages,
                    "error_code": "CAPTCHA",
                }
            return {
                "kind": "web.search",
                "query": params.query,
                "search_engine": engine,
                "engine_used": eng,
                "engine_fallback": fallback,
                "serp": [it.as_dict() for it in items],
                "returned": len(items),
                "pages_fetched": pages,
            }
        return {
            "kind": "web.search",
            "query": params.query,
            "search_engine": engine,
            "engine_used": order[-1],
            "engine_fallback": fallback,
            "serp": [],
            "returned": 0,
            "pages_fetched": 0,
            "error_code": "CAPTCHA" if last_block else "EMPTY_RENDER",
        }

    async def _search_engine(self, engine: str, params: WebSearchParams):
        items = []
        pages = 0
        blocked = False
        for page in range(1, params.max_pages + 1):
            url = bing_url(params.query, page) if engine == "bing_cn" else baidu_url(params.query, page)
            ok, code, _ = self._gate(url, params)
            if not ok:
                if code in {"POLICY_DENIED", "POLICY_ROBOTS"}:
                    break
            try:
                html = await self._get_html(url)
            except SoftDeadline:
                break
            pages += 1
            if engine == "bing_cn" and bing_blocked(html):
                blocked = True
                break
            if engine == "baidu" and baidu_blocked(html):
                blocked = True
                break
            parsed = parse_bing(html) if engine == "bing_cn" else parse_baidu(html)
            items.extend(parsed)
        return items, blocked, pages

    async def fetch_articles(self, params: WebFetchParams) -> dict[str, Any]:
        docs = []
        for url in params.urls:
            docs.append(await self._one_article(url, params))
        return {"kind": "web.fetch_article", "documents": docs}

    async def _one_article(self, url: str, params: WebFetchParams | WebResearchParams) -> dict[str, Any]:
        if self.remaining() <= 0:
            return _skipped(url, "DEADLINE_SOFT")
        ok, code, bypassed = self._gate(url, params)
        if bypassed and not (params.respect_robots if params.respect_robots is not None else True):
            pass
        if not ok:
            status = "skipped" if code == "POLICY_ROBOTS" else "failed"
            return {
                "title": "",
                "url": url,
                "canonical_url": url,
                "source": None,
                "source_domain": urlparse(url).netloc,
                "published_at": None,
                "byline": None,
                "summary": None,
                "body_text": "",
                "body_chars": 0,
                "truncated": False,
                "language": None,
                "related_urls": [],
                "fetch_status": status,
                "error": {"code": code, "message": code},
            }
        try:
            html = await self._get_html(url)
        except SoftDeadline:
            return _skipped(url, "DEADLINE_SOFT")
        except Exception as exc:
            return {
                **_skipped(url, "NAVIGATION_TIMEOUT"),
                "fetch_status": "failed",
                "error": {"code": "NAVIGATION_TIMEOUT", "message": str(exc)},
            }
        include = getattr(params, "include_body", True)
        max_chars = getattr(params, "max_body_chars", self.cfg.web.max_body_chars)
        doc = extract_article(html, url, max_body_chars=max_chars, include_body=include)
        return doc

    async def research(self, params: WebResearchParams) -> dict[str, Any]:
        search_res = await self.search(params)
        if search_res.get("error_code") == "CAPTCHA":
            return {**search_res, "kind": "web.research", "documents": []}
        docs = []
        urls = [row["url"] for row in search_res.get("serp", [])][: params.max_articles]
        fetch_params = WebFetchParams(
            urls=urls or ["https://invalid.invalid/"],
            include_body=params.include_body,
            max_body_chars=self.cfg.web.max_body_chars,
            respect_robots=params.respect_robots,
            allowlist=params.allowlist,
            denylist=params.denylist,
        )
        if not urls:
            return {**search_res, "kind": "web.research", "documents": []}
        for url in urls:
            docs.append(await self._one_article(url, fetch_params))
        return {**search_res, "kind": "web.research", "documents": docs}

    async def hot_list(self, params: WebHotListParams) -> dict[str, Any]:
        sources = []
        for sid in params.sources:
            spec = self.cfg.web.hot_lists.get(sid)
            if spec is None or not spec.enabled or not spec.url:
                sources.append(
                    {
                        "id": sid,
                        "url": spec.url if spec else "",
                        "status": "skipped",
                        "error": {"code": "UNSUPPORTED_TYPE", "message": "source disabled"},
                        "items": [],
                    }
                )
                continue
            ok, code, _ = self._gate(spec.url, WebSearchParams(query="hot", respect_robots=params.respect_robots))
            if not ok:
                sources.append(
                    {
                        "id": sid,
                        "url": spec.url,
                        "status": "skipped" if code == "POLICY_ROBOTS" else "failed",
                        "error": {"code": code, "message": code},
                        "items": [],
                    }
                )
                continue
            try:
                html = await self._get_html(spec.url)
            except SoftDeadline:
                sources.append(
                    {
                        "id": sid,
                        "url": spec.url,
                        "status": "failed",
                        "error": {"code": "DEADLINE_SOFT", "message": "deadline"},
                        "items": [],
                    }
                )
                continue
            if sid != "baidu_hot":
                sources.append(
                    {
                        "id": sid,
                        "url": spec.url,
                        "status": "skipped",
                        "error": {"code": "UNSUPPORTED_TYPE", "message": "v1 only baidu_hot"},
                        "items": [],
                    }
                )
                continue
            items = parse_baidu_hot(html, spec.url, params.max_items_per_source)
            sources.append({"id": sid, "url": spec.url, "status": "ok", "error": None, "items": items})
        return {"kind": "web.hot_list", "sources": sources}


def _skipped(url: str, code: str) -> dict[str, Any]:
    return {
        "title": "",
        "url": url,
        "canonical_url": url,
        "source": None,
        "source_domain": urlparse(url).netloc,
        "published_at": None,
        "byline": None,
        "summary": None,
        "body_text": "",
        "body_chars": 0,
        "truncated": False,
        "language": None,
        "related_urls": [],
        "fetch_status": "skipped",
        "error": {"code": code, "message": code},
    }
