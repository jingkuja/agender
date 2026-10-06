from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote, urlparse

from bs4 import BeautifulSoup, Tag

from agender.browser.navigator import PageSnapshot
from agender.config import AppConfig
from agender.extractors.douyin import selectors as sel
from agender.extractors.douyin.normalize import UnsupportedUrl, canonicalize_video_url, extract_video_id, parse_count
from agender.protocol.schemas import DouyinSearchParams, DouyinVideoParams, SessionState
from agender.worker.execution import ExecutionContext

EMPTY_AUTHOR = {"user_id": None, "sec_uid": None, "unique_id": None, "nickname": None, "follower_count": None}
EMPTY_STATS = {"play_count": None, "like_count": None, "comment_count": None, "share_count": None, "collect_count": None}


def _text(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node else ""


def visible(node: Tag) -> bool:
    for parent in [node, *node.parents]:
        if not isinstance(parent, Tag):
            continue
        style = str(parent.get("style", "")).replace(" ", "").lower()
        if parent.has_attr("hidden") or parent.get("aria-hidden") == "true" or "display:none" in style or "visibility:hidden" in style:
            return False
    return True


def has_visible(html: str, css: str) -> bool:
    return any(visible(node) for node in BeautifulSoup(html, "lxml").select(css))


def has_login_wall(html: str) -> bool:
    return has_visible(html, sel.LOGIN_WALL)


def has_captcha(html: str) -> bool:
    return has_visible(html, sel.CAPTCHA)


def comment_panel_present(html: str) -> bool:
    return has_visible(html, f"{sel.COMMENT_LIST}, {sel.COMMENT_ITEM}")


def timestamp(value) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, (float, int)) or str(value).isdigit():
            epoch = float(value)
            return datetime.fromtimestamp(epoch / 1000 if epoch > 1e12 else epoch, UTC).isoformat()
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # A date with no timezone is retained as source text, never made precise by guessing.
        return dt.isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def inline_objects(html: str):
    soup = BeautifulSoup(html, "lxml")
    for node in soup.select("script#RENDER_DATA, script#__NEXT_DATA__, script[type='application/json']"):
        source = node.string or ""
        if len(source) > 2 * 1024 * 1024:
            continue
        try:
            data = json.loads(source if source.lstrip().startswith(("{", "[")) else unquote(source))
        except (ValueError, RecursionError):
            continue
        stack, count = [data], 0
        while stack and count < 10000:
            item = stack.pop()
            count += 1
            if isinstance(item, dict):
                yield item
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)


def parse_stats_html(html: str, url: str, *, selector_pack: str, video_id: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canonical, vid = canonicalize_video_url(url)
    if video_id and video_id != vid:
        raise UnsupportedUrl("video ID conflicts with URL")
    data = next((item for item in inline_objects(html) if str(item.get("aweme_id", item.get("awemeId", ""))) == vid), {})
    title = _text(soup.select_one(sel.VIDEO_TITLE))
    if not title:
        og = soup.select_one('meta[property="og:title"]')
        title = str(og.get("content", "")) if og else ""
    title = title or data.get("desc") or ""
    author_data = data.get("author") or {}
    nickname = _text(soup.select_one(sel.VIDEO_AUTHOR))
    meta = soup.select_one('meta[name="author"]')
    nickname = nickname or (meta.get("content") if meta else None) or author_data.get("nickname")
    author = {**EMPTY_AUTHOR, "nickname": nickname or None,
              "user_id": str(author_data["uid"]) if author_data.get("uid") is not None else None,
              "sec_uid": author_data.get("sec_uid"), "unique_id": author_data.get("unique_id"),
              "follower_count": parse_count(author_data.get("follower_count"))}
    stats, raw, sources = dict(EMPTY_STATS), {}, {}
    statistics = data.get("statistics") or {}
    mapping = {"like_count": (sel.VIDEO_LIKE, "digg_count"), "comment_count": (sel.VIDEO_COMMENT_COUNT, "comment_count"),
               "share_count": (sel.VIDEO_SHARE, "share_count"), "collect_count": (sel.VIDEO_COLLECT, "collect_count"),
               "play_count": (sel.VIDEO_PLAY, "play_count")}
    for key, (css, field) in mapping.items():
        text = _text(soup.select_one(css))
        if text:
            raw[key] = text
            stats[key] = parse_count(text)
            sources[f"stats.{key}"] = "dom"
        if stats[key] is None and statistics.get(field) is not None:
            stats[key] = parse_count(statistics[field])
            sources[f"stats.{key}"] = "inline_ssr"
    video = data.get("video") or {}
    covers = (video.get("cover") or {}).get("url_list") or []
    og_cover = soup.select_one('meta[property="og:image"]')
    duration = video.get("duration") or data.get("duration")
    published = timestamp(data.get("create_time"))
    if not published:
        time_node = soup.select_one("time[datetime]")
        published = timestamp(time_node.get("datetime")) if time_node else None
    result = {"kind": "douyin.video_stats", "video_id": vid, "aweme_id": vid, "url": canonical, "canonical_url": canonical,
              "title": title, "description": data.get("desc"), "duration_ms": int(duration) if str(duration).isdigit() else None,
              "cover_url": covers[0] if covers else og_cover.get("content") if og_cover else None,
              "published_at": published, "author": author, "stats": stats,
              "music": {"title": (data.get("music") or {}).get("title"), "author": (data.get("music") or {}).get("author")},
              "observed_at": datetime.now(UTC).isoformat()}
    sources["video_id"] = "url"
    if title:
        sources["title"] = "dom" if _text(soup.select_one(sel.VIDEO_TITLE)) else "meta" if soup.select_one('meta[property="og:title"]') else "inline_ssr"
    for key, value in author.items():
        if value is not None:
            sources[f"author.{key}"] = "inline_ssr"
    if nickname:
        sources["author.nickname"] = "dom" if _text(soup.select_one(sel.VIDEO_AUTHOR)) else "meta" if meta else "inline_ssr"
    for key in ("description", "duration_ms"):
        if result[key] is not None:
            sources[key] = "inline_ssr"
    if published:
        sources["published_at"] = "inline_ssr" if timestamp(data.get("create_time")) else "dom"
    if result["cover_url"]:
        sources["cover_url"] = "inline_ssr" if covers else "meta"
    for key, value in result["music"].items():
        if value is not None:
            sources[f"music.{key}"] = "inline_ssr"
    missing = [f"stats.{k}" for k, v in stats.items() if v is None]
    missing.extend(key for key in ("title", "description", "duration_ms", "cover_url", "published_at") if not result[key])
    missing.extend(f"author.{key}" for key, value in author.items() if value is None)
    result["extraction"] = {"source": "dom+inline_ssr" if data else "dom", "selector_pack_version": selector_pack,
                            "missing_fields": missing, "missing_reasons": {key: "not_exposed_or_not_recognized" for key in missing},
                            "field_sources": sources, "raw_stats": raw}
    return result


def parse_search_html(html: str, query: str, *, max_results: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    items, seen = [], set()
    for card in soup.select(sel.SEARCH_CARD):
        if not visible(card):
            continue
        link = card if card.name == "a" else card.select_one("a[href*='/video/']")
        if not link:
            continue
        href = str(link.get("href") or "")
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://www.douyin.com" + href
        try:
            canonical, vid = canonicalize_video_url(href)
        except UnsupportedUrl:
            continue
        if vid in seen:
            continue
        seen.add(vid)
        title_node = card.select_one(sel.SEARCH_TITLE)
        title = _text(title_node) or str(title_node.get("alt", "") if title_node else "") or _text(card)
        nick = _text(card.select_one(sel.VIDEO_AUTHOR))
        time_node = card.select_one("time[datetime]")
        items.append({"video_id": vid, "aweme_id": vid, "url": canonical, "canonical_url": canonical, "title": title,
                      "author": {**EMPTY_AUTHOR, "nickname": nick or None}, "stats": dict(EMPTY_STATS),
                      "published_at": timestamp(time_node.get("datetime")) if time_node else None})
        if len(items) >= max_results:
            break
    return {"kind": "douyin.search", "query": query, "items": items, "returned": len(items)}


def parse_comments_html(html: str, video: dict, *, redact: bool, max_comments: int,
                        include_replies: bool = False, max_replies_per_comment: int = 5) -> dict:
    soup = BeautifulSoup(html, "lxml")
    candidates = soup.select(f"{sel.COMMENT_ITEM}, {sel.REPLY_ITEM}")
    ids = {id(node) for node in candidates}
    roots = [node for node in candidates if not node.get("data-parent-id") and not any(id(parent) in ids for parent in node.parents)]

    def parse(node: Tag, parent: str | None = None):
        def own(css):
            return next((child for child in node.select(css) if next((id(p) for p in child.parents if id(p) in ids), id(node)) == id(node)), None)
        text = _text(own(sel.COMMENT_TEXT))
        nick = _text(own(sel.COMMENT_USER))
        date = own("time[datetime]")
        created = timestamp(date.get("datetime")) if date else timestamp(node.get("data-created-at"))
        cid = node.get("data-id") or node.get("data-e2e-cid")
        quality = "platform_id" if cid else "fingerprint"
        if not cid:
            signature = json.dumps([video.get("video_id"), parent, node.get("data-uid"), nick, text, created], ensure_ascii=False)
            cid = "fp:" + hashlib.sha256(signature.encode()).hexdigest()[:24]
        return {"comment_id": str(cid), "id_quality": quality, "parent_id": parent, "text": text,
                "like_count": parse_count(_text(own(sel.COMMENT_LIKE))),
                "reply_count": parse_count(node.get("data-reply-count") or _text(own(sel.REPLY_COUNT))),
                "created_at": created, "ip_label": None,
                "commenter": {"user_id": None if redact else node.get("data-uid"), "sec_uid": None,
                              "nickname": None if redact else nick or None, "avatar_url": None}, "replies": []}

    comments, seen = [], set()
    for node in roots:
        if not visible(node):
            continue
        item = parse(node)
        if not item["text"] or item["comment_id"] in seen:
            continue
        seen.add(item["comment_id"])
        if include_replies:
            reply_seen = set()
            children = [child for child in candidates if child is not node and (node in child.parents or child.get("data-parent-id") == item["comment_id"])]
            for child in children:
                reply = parse(child, item["comment_id"])
                if visible(child) and reply["text"] and reply["comment_id"] not in reply_seen and len(item["replies"]) < max_replies_per_comment:
                    reply_seen.add(reply["comment_id"])
                    item["replies"].append(reply)
        comments.append(item)
        if len(comments) >= max_comments:
            break
    end = has_visible(html, sel.COMMENT_END)
    limit = len(comments) >= max_comments
    return {"kind": "douyin.video_comments", "video": video, "comments": comments, "returned": len(comments),
            "exhausted": end and not limit, "limit_reached": limit, "has_more": False if end and not limit else None,
            "stop_reason": "limit" if limit else "end" if end else "snapshot", "observed_at": datetime.now(UTC).isoformat()}


def eligible(row: dict, params, *, id_key="video_id", time_key="published_at") -> bool:
    if row.get(id_key) in params.known_ids:
        return False
    if params.since:
        value = row.get(time_key)
        try:
            published = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if published.tzinfo is None:
                raise ValueError()
            return published >= datetime.fromisoformat(params.since.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            row["date_filter_unknown"] = True
    return True


class DouyinTool:
    def __init__(self, cfg: AppConfig, fetch, session_state=SessionState.MISSING, *, execution: ExecutionContext | None = None, navigator=None):
        self.cfg, self.fetch, self.session_state = cfg, fetch, session_state
        self.execution, self.navigator = execution, navigator
        self.last_html = ""

    @property
    def page(self):
        return self.navigator.runtime.active_page

    async def open(self, url: str, ready: str) -> PageSnapshot:
        if self.execution:
            self.execution.check()
        out = await self.navigator.open(url, "douyin", ready_selector=ready) if self.navigator else await self.fetch(url)
        snapshot = out if isinstance(out, PageSnapshot) else PageSnapshot(out, url, url)
        self.last_html = snapshot.html
        return snapshot

    async def snapshot(self, result: dict, stage: str) -> None:
        if self.execution:
            self.execution.snapshot(result)
            await self.execution.progress(stage, {"returned": result.get("returned", 0)})

    def restored(self, key: str) -> list:
        resume = getattr(self.execution, "resume", None) if self.execution else None
        return (resume or {}).get(key, [])

    async def changed(self, selector: str, before: str) -> None:
        try:
            await self.page.wait_for_function(
                "([s, before]) => [...document.querySelectorAll(s)].map(e => e.outerHTML).join('') !== before",
                arg=[selector, before], timeout=self.cfg.douyin.load_wait_s * 1000)
        except Exception:
            if self.execution:
                self.execution.check()

    async def scroll(self, selector: str, *, container: str | None = None) -> str:
        await self.navigator.load_action()
        before = await self.page.locator(selector).evaluate_all("els => els.map(e => e.outerHTML).join('')")
        if container:
            panel = self.page.locator(container).first
            if await panel.count():
                await panel.hover()
        await self.page.mouse.wheel(0, 650)
        await self.changed(selector, before)
        return await self.page.content()

    async def collect_posts(self, html: str, params, *, query: str, kind: str, max_results: int, end_selector: str, extra: dict | None = None) -> dict:
        items = {row["video_id"]: row for row in self.restored("items")}
        result = {"kind": kind, "query": query, "items": list(items.values()), "returned": len(items),
                  "exhausted": False, "limit_reached": False, "has_more": None, "scan_scope": "bounded_visible_dom", **(extra or {})}
        stalled = 0
        for _ in range(self.cfg.douyin.max_scroll_rounds):
            if self.execution:
                self.execution.check()
            if has_captcha(html):
                result["error_code"] = "CAPTCHA"
                break
            old = len(items)
            parsed = parse_search_html(html, query, max_results=1000)["items"]
            if has_login_wall(html) and not parsed:
                result["error_code"] = "LOGIN_WALL"
                break
            for row in parsed:
                if len(items) >= max_results:
                    break
                if eligible(row, params):
                    items[row["video_id"]] = row
            result.update(items=list(items.values()), returned=len(items))
            await self.snapshot(result, "posts")
            if len(items) >= max_results:
                result.update(limit_reached=True, stop_reason="limit")
                break
            if has_visible(html, end_selector):
                result.update(exhausted=True, has_more=False, stop_reason="end")
                break
            if not self.navigator:
                if not parsed:
                    result["error_code"] = "SELECTOR_DRIFT"
                result["stop_reason"] = "snapshot"
                break
            stalled = stalled + 1 if len(items) == old else 0
            if stalled >= self.cfg.douyin.stall_rounds:
                result.update(error_code="LOAD_STALL", stop_reason="load_stall")
                break
            html = await self.scroll(sel.SEARCH_CARD)
        else:
            result.update(error_code="LOAD_STALL", stop_reason="round_limit")
        result["observed_at"] = datetime.now(UTC).isoformat()
        return result

    async def search(self, params: DouyinSearchParams) -> dict:
        url = f"{self.cfg.douyin.base_url}/search/{quote(params.query, safe='')}"
        opened = await self.open(url, f"{sel.SEARCH_CARD}, {sel.LOGIN_WALL}, {sel.CAPTCHA}, {sel.SEARCH_END}")
        html = opened.html
        if params.sort != "general":
            if not self.navigator:
                return {"kind": "douyin.search", "query": params.query, "items": [], "returned": 0, "error_code": "UNSUPPORTED_SORT"}
            trigger = self.page.locator(sel.SORT_TRIGGER).first
            if await trigger.count() and await trigger.is_visible():
                await trigger.click()
            option = self.page.get_by_text(sel.SORT_LABELS[params.sort], exact=True).first
            if not await option.count() or not await option.is_visible():
                return {"kind": "douyin.search", "items": [], "error_code": "UNSUPPORTED_SORT"}
            before = await self.page.locator(sel.SEARCH_CARD).evaluate_all("els => els.map(e => e.outerHTML).join('')")
            await self.navigator.load_action()
            await option.click()
            await self.changed(sel.SEARCH_CARD, before)
            html = await self.page.content()
        return await self.collect_posts(html, params, query=params.query, kind="douyin.search", max_results=params.max_results, end_selector=sel.SEARCH_END)

    async def video_stats(self, params: DouyinVideoParams) -> dict:
        url = params.url or f"{self.cfg.douyin.base_url}/video/{params.video_id}"
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or parsed.hostname not in self.cfg.douyin.redirect_allowlist or "/note/" in parsed.path:
            raise UnsupportedUrl("unsupported video URL")
        vid = extract_video_id(url)
        if vid:
            canonical, vid = canonicalize_video_url(url, self.cfg.douyin.redirect_allowlist)
            if params.video_id and vid != params.video_id:
                raise UnsupportedUrl("ID conflict")
            url = canonical
        opened = await self.open(url, f"{sel.VIDEO_TITLE}, {sel.VIDEO_AUTHOR}, {sel.LOGIN_WALL}, {sel.CAPTCHA}")
        canonical, vid = canonicalize_video_url(opened.final_url, self.cfg.douyin.redirect_allowlist)
        if params.video_id and vid != params.video_id:
            raise UnsupportedUrl("ID conflict")
        result = parse_stats_html(opened.html, canonical, selector_pack=self.cfg.douyin.selector_pack_version, video_id=vid)
        if has_captcha(opened.html):
            result["error_code"] = "CAPTCHA"
        elif has_login_wall(opened.html) and not result["title"]:
            result["error_code"] = "LOGIN_WALL"
        await self.snapshot(result, "video")
        return result

    async def _expand(self, params, harvest) -> None:
        if not self.navigator or not params.include_replies or params.max_replies_per_comment == 0:
            return
        roots = self.page.locator(sel.COMMENT_ITEM)
        for index in range(min(await roots.count(), params.max_comments)):
            root = roots.nth(index)
            for _ in range(self.cfg.douyin.stall_rounds + params.max_replies_per_comment):
                if await root.locator(sel.REPLY_ITEM).count() >= params.max_replies_per_comment:
                    break
                button = root.locator(sel.EXPAND_REPLIES).first
                if not await button.count() or not await button.is_visible():
                    break
                await self.navigator.load_action()
                before = await self.page.locator(sel.COMMENT_ITEM).evaluate_all("els => els.map(e => e.outerHTML).join('')")
                await button.click()
                await self.changed(sel.COMMENT_ITEM, before)
                await harvest(await self.page.content())
                after = await self.page.locator(sel.COMMENT_ITEM).evaluate_all("els => els.map(e => e.outerHTML).join('')")
                if before == after:
                    break

    async def video_comments(self, params: DouyinVideoParams) -> dict:
        stats = await self.video_stats(params)
        code = stats.pop("error_code", None)
        comments = {row["comment_id"]: row for row in self.restored("comments")}
        result = {"kind": "douyin.video_comments", "video": stats, "comments": list(comments.values()),
                  "returned": len(comments), "exhausted": False, "limit_reached": False, "has_more": None,
                  "scan_scope": "bounded_visible_dom"}
        await self.snapshot(result, "video")
        if code:
            return {**result, "error_code": code}
        if self.navigator:
            self.session_state = await self.navigator.runtime.douyin_session()
        if self.session_state != SessionState.OK:
            return {**result, "error_code": "SESSION_EXPIRED" if self.session_state == SessionState.EXPIRED else "LOGIN_WALL"}
        html = self.last_html
        if self.navigator and not comment_panel_present(html):
            trigger = self.page.locator(sel.COMMENT_TRIGGER).first
            if await trigger.count() and await trigger.is_visible():
                await self.navigator.load_action()
                await trigger.click()
            try:
                await self.page.locator(sel.COMMENT_LIST).first.wait_for(state="visible", timeout=self.cfg.douyin.load_wait_s * 1000)
            except Exception:
                if self.execution:
                    self.execution.check()
            html = await self.page.content()
        if not comment_panel_present(html):
            return {**result, "error_code": "CAPTCHA" if has_captcha(html) else "LOGIN_WALL" if has_login_wall(html) else "EMPTY_RENDER"}

        async def harvest(current):
            batch = parse_comments_html(current, stats, redact=params.redact_commenters, max_comments=1000,
                                        include_replies=params.include_replies, max_replies_per_comment=params.max_replies_per_comment)
            for row in batch["comments"]:
                cid = row["comment_id"]
                if not eligible(row, params, id_key="comment_id", time_key="created_at"):
                    continue
                if cid in comments:
                    replies = {reply["comment_id"]: reply for reply in comments[cid].get("replies", [])}
                    replies.update({reply["comment_id"]: reply for reply in row["replies"]})
                    row["replies"] = list(replies.values())[:params.max_replies_per_comment]
                    comments[cid] = row
                elif len(comments) < params.max_comments:
                    comments[cid] = row
            result.update(comments=list(comments.values()), returned=len(comments))
            await self.snapshot(result, "comments")

        stalled = 0
        for _ in range(self.cfg.douyin.max_scroll_rounds):
            if has_captcha(html) or has_login_wall(html):
                result["error_code"] = "CAPTCHA" if has_captcha(html) else "LOGIN_WALL"
                break
            old = len(comments)
            await harvest(html)
            await self._expand(params, harvest)
            if self.navigator:
                html = await self.page.content()
                await harvest(html)
            if len(comments) >= params.max_comments:
                result.update(limit_reached=True, stop_reason="limit")
                break
            if has_visible(html, sel.COMMENT_END):
                result.update(exhausted=True, has_more=False, stop_reason="end")
                break
            if not self.navigator:
                result["stop_reason"] = "snapshot"
                break
            stalled = stalled + 1 if len(comments) == old else 0
            if stalled >= self.cfg.douyin.stall_rounds:
                result.update(error_code="COMMENT_LOAD_STALL", stop_reason="load_stall")
                break
            html = await self.scroll(sel.COMMENT_ITEM, container=sel.COMMENT_LIST)
        else:
            result.update(error_code="COMMENT_LOAD_STALL", stop_reason="round_limit")
        result["observed_at"] = datetime.now(UTC).isoformat()
        return result

    async def video_bundle(self, params: DouyinVideoParams) -> dict:
        result = await self.video_comments(params)
        result["kind"] = "douyin.video_bundle"
        return result
