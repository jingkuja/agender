from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag

from agender.config import AppConfig
from agender.extractors.douyin import selectors as sel
from agender.extractors.douyin.normalize import (
    UnsupportedUrl,
    canonicalize_video_url,
    parse_count,
)
from agender.protocol.schemas import DouyinSearchParams, DouyinVideoParams, SessionState

EMPTY_AUTHOR = {
    "user_id": None,
    "sec_uid": None,
    "unique_id": None,
    "nickname": None,
    "follower_count": None,
}
EMPTY_STATS = {
    "play_count": None,
    "like_count": None,
    "comment_count": None,
    "share_count": None,
    "collect_count": None,
}


def _text(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _first(soup: BeautifulSoup, css: str) -> Tag | None:
    return soup.select_one(css)


def parse_stats_html(html: str, url: str, *, selector_pack: str, video_id: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    try:
        canonical, vid = canonicalize_video_url(url) if url else ("", video_id or "")
    except UnsupportedUrl:
        canonical, vid = url, video_id or ""
    if video_id:
        vid = video_id
        canonical = f"https://www.douyin.com/video/{vid}"
    title = _text(_first(soup, sel.VIDEO_TITLE))
    nickname = _text(_first(soup, sel.VIDEO_AUTHOR))
    if not title:
        og = soup.select_one('meta[property="og:title"]')
        if og and og.get("content"):
            title = str(og.get("content"))
    if not nickname:
        oga = soup.select_one('meta[name="author"]')
        if oga and oga.get("content"):
            nickname = str(oga.get("content"))
    inline = soup.select_one("#RENDER_DATA, script#RENDER_DATA, script[id*='RENDER']")
    aweme_id = vid
    if inline and inline.string:
        m = re.search(r'"aweme_id"\s*:\s*"(\d+)"', inline.string)
        if m:
            aweme_id = m.group(1)
            vid = vid or aweme_id
    stats = dict(EMPTY_STATS)
    raw: dict[str, str] = {}
    mapping = {
        "like_count": sel.VIDEO_LIKE,
        "comment_count": sel.VIDEO_COMMENT_COUNT,
        "share_count": sel.VIDEO_SHARE,
        "collect_count": sel.VIDEO_COLLECT,
        "play_count": sel.VIDEO_PLAY,
    }
    for key, css in mapping.items():
        t = _text(_first(soup, css))
        if t:
            raw[key] = t
            stats[key] = parse_count(t)
    missing = [f"stats.{k}" for k, v in stats.items() if v is None]
    if not nickname:
        missing.append("author.nickname")
    if not title:
        missing.append("title")
    return {
        "kind": "douyin.video_stats",
        "video_id": vid,
        "aweme_id": aweme_id or vid,
        "url": canonical or url,
        "canonical_url": canonical or url,
        "title": title,
        "description": None,
        "duration_ms": None,
        "cover_url": None,
        "published_at": None,
        "author": {**EMPTY_AUTHOR, "nickname": nickname or None},
        "stats": stats,
        "music": {"title": None, "author": None},
        "extraction": {
            "source": "dom",
            "selector_pack_version": selector_pack,
            "missing_fields": missing,
            "raw_stats": raw,
        },
    }


def parse_search_html(html: str, query: str, *, max_results: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    cards = soup.select(sel.SEARCH_CARD)
    for card in cards:
        href = ""
        a = card if card.name == "a" else card.select_one("a[href*='/video/']")
        if a:
            href = str(a.get("href") or "")
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("/"):
            href = "https://www.douyin.com" + href
        try:
            canonical, vid = canonicalize_video_url(href) if href else ("", "")
        except UnsupportedUrl:
            continue
        if not vid or any(it["video_id"] == vid for it in items):
            continue
        title = _text(card.select_one(sel.SEARCH_TITLE)) or _text(card)
        nick = _text(card.select_one(sel.VIDEO_AUTHOR))
        items.append(
            {
                "video_id": vid,
                "aweme_id": vid,
                "url": canonical,
                "title": title,
                "author": {**EMPTY_AUTHOR, "nickname": nick or None},
                "stats": dict(EMPTY_STATS),
            }
        )
        if len(items) >= max_results:
            break
    return {"kind": "douyin.search", "query": query, "items": items, "returned": len(items)}


def parse_comments_html(html: str, video: dict[str, Any], *, redact: bool, max_comments: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    comments = []
    for item in soup.select(sel.COMMENT_ITEM):
        text = _text(item.select_one(sel.COMMENT_TEXT)) or _text(item)
        nick = _text(item.select_one(sel.COMMENT_USER))
        like = parse_count(_text(item.select_one(sel.COMMENT_LIKE)))
        cid = item.get("data-id") or item.get("data-e2e-cid") or f"c_{len(comments)+1}"
        commenter = {
            "user_id": None if redact else (item.get("data-uid") or None),
            "sec_uid": None,
            "nickname": nick or None,
            "avatar_url": None,
        }
        comments.append(
            {
                "comment_id": str(cid),
                "text": text,
                "like_count": like,
                "reply_count": 0,
                "created_at": None,
                "ip_label": None,
                "commenter": commenter,
                "replies": [],
            }
        )
        if len(comments) >= max_comments:
            break
    return {
        "kind": "douyin.video_comments",
        "video": video,
        "comments": comments,
        "returned": len(comments),
        "exhausted": True,
    }


def has_login_wall(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return soup.select_one(sel.LOGIN_WALL) is not None


def has_captcha(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return soup.select_one(sel.CAPTCHA) is not None


def comment_panel_present(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return soup.select_one(sel.COMMENT_LIST) is not None or bool(soup.select(sel.COMMENT_ITEM))


class DouyinTool:
    def __init__(self, cfg: AppConfig, fetch, session_state: SessionState = SessionState.MISSING):
        self.cfg = cfg
        self.fetch = fetch
        self.session_state = session_state

    def search_url(self, query: str) -> str:
        return f"{self.cfg.douyin.base_url}/search/{quote(query)}"

    async def search(self, params: DouyinSearchParams) -> dict[str, Any]:
        url = self.search_url(params.query)
        html = await self.fetch(url)
        if has_captcha(html):
            return {"kind": "douyin.search", "query": params.query, "items": [], "returned": 0, "error_code": "CAPTCHA"}
        if has_login_wall(html) and not parse_search_html(html, params.query, max_results=1)["items"]:
            return {"kind": "douyin.search", "query": params.query, "items": [], "returned": 0, "error_code": "LOGIN_WALL"}
        return parse_search_html(html, params.query, max_results=params.max_results)

    async def video_stats(self, params: DouyinVideoParams) -> dict[str, Any]:
        url = params.url or f"{self.cfg.douyin.base_url}/video/{params.video_id}"
        canonical, vid = canonicalize_video_url(url, self.cfg.douyin.redirect_allowlist)
        html = await self.fetch(canonical)
        if has_captcha(html):
            return {**parse_stats_html(html, canonical, selector_pack=self.cfg.douyin.selector_pack_version, video_id=vid), "error_code": "CAPTCHA"}
        return parse_stats_html(html, canonical, selector_pack=self.cfg.douyin.selector_pack_version, video_id=vid)

    async def video_comments(self, params: DouyinVideoParams) -> dict[str, Any]:
        stats = await self.video_stats(params)
        if self.session_state != SessionState.OK:
            return {
                "kind": "douyin.video_comments",
                "video": {k: v for k, v in stats.items() if k != "error_code"},
                "comments": [],
                "returned": 0,
                "exhausted": False,
                "error_code": "LOGIN_WALL",
            }
        url = stats.get("canonical_url") or params.url
        html = await self.fetch(url)
        if has_captcha(html):
            return {
                "kind": "douyin.video_comments",
                "video": stats,
                "comments": [],
                "returned": 0,
                "exhausted": False,
                "error_code": "CAPTCHA",
            }
        if not comment_panel_present(html):
            return {
                "kind": "douyin.video_comments",
                "video": stats,
                "comments": [],
                "returned": 0,
                "exhausted": False,
                "error_code": "LOGIN_WALL" if has_login_wall(html) else "EMPTY_RENDER",
            }
        out = parse_comments_html(
            html,
            stats,
            redact=params.redact_commenters,
            max_comments=params.max_comments,
        )
        return out

    async def video_bundle(self, params: DouyinVideoParams) -> dict[str, Any]:
        comments = await self.video_comments(params)
        comments["kind"] = "douyin.video_bundle"
        return comments
