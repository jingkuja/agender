from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote_plus, urlparse

import structlog
from bs4 import BeautifulSoup

from agender.browser.navigator import PageSnapshot
from agender.config import AppConfig
from agender.extractors.web.article import extract_article
from agender.extractors.web.hotlists import HOTLIST_PARSERS
from agender.extractors.web.policy import PolicyDenied, check_url, robots_allowed_async
from agender.extractors.web.search_baidu import baidu_blocked, parse_baidu
from agender.extractors.web.search_bing import bing_blocked, parse_bing
from agender.extractors.web.urls import normalize_url
from agender.protocol.schemas import WebFetchParams, WebHotListParams, WebResearchParams, WebSearchParams
from agender.worker.execution import ExecutionContext, ExecutionStopped

Fetcher = Callable[[str], Awaitable[str | PageSnapshot]]
log = structlog.get_logger("agender.web")


class SoftDeadline(ExecutionStopped):
    def __init__(self):
        super().__init__("DEADLINE_SOFT")


def bing_url(query: str, page: int) -> str:
    return f"https://cn.bing.com/search?q={quote_plus(query)}&first={1 + (page - 1) * 10}"


def baidu_url(query: str, page: int) -> str:
    return f"https://www.baidu.com/s?wd={quote_plus(query)}&pn={(page - 1) * 10}"


def explicit_empty(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return bool(soup.select_one(".no-results, [data-e2e='no-results']")) or any(s in soup.get_text() for s in ("没有找到相关结果", "抱歉，没有找到", "There are no results for"))


class WebTool:
    def __init__(self, cfg: AppConfig, fetch: Fetcher, *, now_mono=None, execution: ExecutionContext | None = None, robots_fetch=None):
        self.cfg, self.fetch = cfg, fetch
        self._now = now_mono or asyncio.get_running_loop().time
        self.deadline: float | None = None
        self.execution = execution
        if robots_fetch:
            self._robots_fetch = robots_fetch

    def remaining(self) -> float:
        return 1e9 if self.deadline is None else self.deadline - self._now()

    def restored(self, key: str) -> list:
        return ((getattr(self.execution, "resume", None) or {}).get(key) or []) if self.execution else []

    async def _robots_fetch(self, url: str) -> str:
        # Standalone callers get the same pinned egress as BrowserRuntime.
        from types import SimpleNamespace

        from agender.browser.egress import EgressProxy
        from agender.browser.navigator import Navigator
        egress = EgressProxy(self.cfg)
        nav = Navigator(SimpleNamespace(cfg=self.cfg, egress=egress))
        try:
            return await nav.robots(url)
        finally:
            await egress.close()

    async def _gate(self, url: str, params) -> tuple[bool, str | None, bool]:
        decision = await asyncio.to_thread(check_url, url, self.cfg,
                                          allowlist=getattr(params, "allowlist", []), extra_denylist=getattr(params, "denylist", []))
        if not decision.ok:
            return False, decision.code, False
        try:
            allowed, would_block = await robots_allowed_async(url, fetch=self._robots_fetch, respect=params.respect_robots)
        except PolicyDenied as exc:
            return False, exc.code, False
        except Exception:
            return False, "ROBOTS_UNAVAILABLE", False
        if would_block and not params.respect_robots:
            log.info("policy.robots_bypassed", host=urlparse(url).hostname, source="task_param")
        return allowed, None if allowed else "POLICY_ROBOTS", would_block

    async def _get_html(self, url: str) -> PageSnapshot:
        if self.execution:
            self.execution.check()
        if self.remaining() <= 0:
            raise SoftDeadline()
        response = await self.fetch(url)
        return response if isinstance(response, PageSnapshot) else PageSnapshot(response, url, url)

    async def _snapshot(self, result: dict, stage: str) -> None:
        if self.execution:
            if result["kind"] == "web.search" and self.restored("documents"):
                result = {**result, "documents": self.restored("documents")}
            self.execution.snapshot(result)
            await self.execution.progress(stage, {"returned": result.get("returned", len(result.get("documents", [])))})

    async def search(self, params: WebSearchParams) -> dict[str, Any]:
        engine = params.search_engine
        order = ["bing_cn", "baidu"] if engine == "auto" else [engine]
        combined = list(self.restored("serp"))
        seen = set(params.known_ids) | {normalize_url(row["url"]) for row in combined}
        fallback = False
        for index, eng in enumerate(order):
            rows, error, pages, exhausted = await self._search_engine(eng, params, combined, seen)
            result = {"kind": "web.search", "query": params.query, "search_engine": engine,
                      "engine_used": eng, "engine_fallback": fallback, "serp": rows,
                      "returned": len(rows), "pages_fetched": pages, "exhausted": exhausted,
                      "limit_reached": not error and not exhausted and pages >= params.max_pages,
                      "has_more": False if exhausted else None, "stop_reason": "end" if exhausted else "page_limit"}
            await self._snapshot(result, "search")
            if error and index == 0 and len(order) > 1 and error in {"CAPTCHA", "EMPTY_RENDER", "RATE_LIMITED", "REGION_BLOCK"}:
                fallback = True
                continue
            if error:
                result["error_code"] = error
                result["stop_reason"] = error.lower()
            return result
        raise RuntimeError("no search engine")

    async def _search_engine(self, engine, params, items, seen):
        pages, exhausted = 0, False
        for page in range(1, params.max_pages + 1):
            url = bing_url(params.query, page) if engine == "bing_cn" else baidu_url(params.query, page)
            ok, code, _ = await self._gate(url, params)
            if not ok:
                return items, code, pages, False
            try:
                snapshot = await self._get_html(url)
            except ExecutionStopped:
                raise
            except Exception as exc:
                return items, getattr(exc, "code", "NAVIGATION_TIMEOUT"), pages, False
            pages += 1
            html = snapshot.html
            if (bing_blocked if engine == "bing_cn" else baidu_blocked)(html):
                return items, "CAPTCHA", pages, False
            parsed = (parse_bing if engine == "bing_cn" else parse_baidu)(html)
            if not parsed:
                if explicit_empty(html):
                    exhausted = True
                    break
                return items, "SELECTOR_DRIFT" if BeautifulSoup(html, "lxml").get_text(strip=True) else "EMPTY_RENDER", pages, False
            for item in parsed:
                canonical = normalize_url(item.url)
                if canonical not in seen:
                    seen.add(canonical)
                    row = item.as_dict()
                    if params.since:
                        row["date_filter_unknown"] = True
                    row["canonical_url"] = canonical
                    items.append(row)
            await self._snapshot({"kind": "web.search", "query": params.query, "serp": items, "returned": len(items),
                                  "engine_used": engine, "pages_fetched": pages}, "search_page")
        return items, None, pages, exhausted

    async def fetch_articles(self, params: WebFetchParams) -> dict[str, Any]:
        docs = [row for row in self.restored("documents") if row.get("fetch_status") == "ok"]
        known = {normalize_url(url) for url in params.known_ids}
        seen = {normalize_url(row.get("requested_url") or row["url"]) for row in docs}
        for url in params.urls:
            canonical = normalize_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            docs.append(_skipped(url, "KNOWN_ID") if canonical in known else await self._one_article(url, params))
            await self._snapshot({"kind": "web.fetch_article", "documents": docs}, "article")
        return {"kind": "web.fetch_article", "documents": docs,
                "exhausted": all(row.get("fetch_status") in {"ok", "skipped"} and (row.get("error") or {}).get("code") != "DEADLINE_SOFT" for row in docs)}

    async def _one_article(self, url: str, params) -> dict[str, Any]:
        if self.remaining() <= 0:
            return _skipped(url, "DEADLINE_SOFT")
        ok, code, _ = await self._gate(url, params)
        if not ok:
            return {**_skipped(url, code), "fetch_status": "skipped" if code == "POLICY_ROBOTS" else "failed"}
        try:
            snapshot = await self._get_html(url)
        except ExecutionStopped:
            raise
        except Exception as exc:
            return {**_skipped(url, getattr(exc, "code", "NAVIGATION_TIMEOUT")), "fetch_status": "failed"}
        doc = await asyncio.to_thread(extract_article, snapshot.html, snapshot.final_url,
                                      max_body_chars=getattr(params, "max_body_chars", self.cfg.web.max_body_chars),
                                      include_body=params.include_body)
        doc["requested_url"] = url
        if params.since:
            from agender.extractors.douyin.tool import eligible
            if not eligible(doc, params, id_key="canonical_url"):
                doc.update(fetch_status="skipped", body_text="", body_chars=0, summary=None,
                           error={"code": "FILTER_SINCE", "message": "published before requested window"})
        return doc

    async def research(self, params: WebResearchParams) -> dict[str, Any]:
        search = await self.search(params)
        restored = [row for row in self.restored("documents") if row.get("fetch_status") == "ok"]
        result = {**search, "kind": "web.research", "documents": restored, "exhausted": False, "limit_reached": False}
        if search.get("error_code") and not search.get("serp"):
            return result
        seen = set(params.known_ids) | {normalize_url(row.get("requested_url") or row["url"]) for row in restored}
        for row in search.get("serp", []):
            if len(result["documents"]) >= params.max_articles:
                break
            canonical = normalize_url(row["url"])
            if canonical in seen:
                continue
            doc = await self._one_article(row["url"], params)
            final = normalize_url(doc["canonical_url"])
            seen.add(canonical)
            if final in seen and final != canonical:
                continue
            seen.add(final)
            result["documents"].append(doc)
            await self._snapshot(result, "article")
        complete = not result.get("error_code") and all(d.get("fetch_status") in {"ok", "skipped"} and (d.get("error") or {}).get("code") != "DEADLINE_SOFT" for d in result["documents"])
        result["limit_reached"] = complete and len(result["documents"]) >= params.max_articles
        result["exhausted"] = complete and not result["limit_reached"]
        return result

    async def hot_list(self, params: WebHotListParams) -> dict[str, Any]:
        sources = []
        for sid in dict.fromkeys(params.sources):
            spec, parser = self.cfg.web.hot_lists.get(sid), HOTLIST_PARSERS.get(sid)
            entry = {"id": sid, "url": spec.url if spec else "", "status": "skipped", "error": None, "items": []}
            sources.append(entry)
            if not spec or not spec.enabled or not spec.url or parser is None:
                entry["error"] = {"code": "UNSUPPORTED_TYPE", "message": "source has no enabled adapter"}
                continue
            ok, code, _ = await self._gate(spec.url, params)
            if not ok:
                entry.update(status="skipped" if code == "POLICY_ROBOTS" else "failed", error={"code": code, "message": code})
                continue
            try:
                snapshot = await self._get_html(spec.url)
                items = parser(snapshot.html, snapshot.final_url, params.max_items_per_source)
                if baidu_blocked(snapshot.html):
                    code = "CAPTCHA"
                elif not items:
                    code = "SELECTOR_DRIFT"
                else:
                    code = None
                entry.update(status="blocked" if code == "CAPTCHA" else "failed" if code else "ok",
                             items=items, error={"code": code, "message": code} if code else None)
            except ExecutionStopped:
                raise
            except Exception as exc:
                code = getattr(exc, "code", "NAVIGATION_TIMEOUT")
                entry.update(status="failed", error={"code": code, "message": code})
            await self._snapshot({"kind": "web.hot_list", "sources": sources}, "hot_list")
        return {"kind": "web.hot_list", "sources": sources}


def _skipped(url: str, code: str) -> dict[str, Any]:
    return {"title": "", "url": url, "canonical_url": normalize_url(url), "source": None,
            "source_domain": urlparse(url).netloc, "published_at": None, "byline": None, "summary": None,
            "body_text": "", "body_chars": 0, "truncated": False, "language": None, "related_urls": [],
            "fetch_status": "skipped", "error": {"code": code, "message": code}}
