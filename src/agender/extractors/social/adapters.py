from __future__ import annotations

import hashlib
import json
import re
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from agender.extractors.douyin.normalize import UnsupportedUrl, parse_count
from agender.extractors.douyin.tool import timestamp, visible

BASES = {"xiaohongshu": "https://www.xiaohongshu.com", "weibo": "https://weibo.com", "bilibili": "https://www.bilibili.com"}
HOSTS = {
    "xiaohongshu": {"www.xiaohongshu.com", "xiaohongshu.com", "xhslink.com", "www.xhslink.com"},
    "weibo": {"weibo.com", "www.weibo.com", "m.weibo.cn"},
    "bilibili": {"www.bilibili.com", "bilibili.com", "space.bilibili.com", "b23.tv"},
}
SHORT_HOSTS = {"xhslink.com", "www.xhslink.com", "b23.tv"}
CARDS = {
    "xiaohongshu": ".note-item, [data-note-id]",
    "weibo": ".card-wrap[mid], article, [data-mid]",
    "bilibili": ".bili-video-card, .video-item, .small-item, [data-bvid]",
}
COMMENTS = {
    "xiaohongshu": ".comment-item, [data-comment-id]",
    "weibo": ".comment-item, .CommentItem, [data-comment-id]",
    "bilibili": "bili-comment-thread-renderer, .root-reply-container, .reply-item, [data-comment-id]",
}
REPLIES = ".reply-item, .sub-comment, .sub-reply-item, bili-comment-reply-renderer, [data-parent-id]"
END = ".end-container, .no-more, .no-results, .search-empty, .bili-search-empty, [data-agender-end]"
COMMENT_END = ".comment-end, .no-comment, .no-more, [data-comments-end]"


def text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def clean(value) -> str:
    return BeautifulSoup(str(value or ""), "lxml").get_text(" ", strip=True)


def first(data: dict, *keys, default=None):
    if not isinstance(data, dict):
        return default
    return next((data[k] for k in keys if data.get(k) is not None), default)


def mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def media_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username else None


def date(value) -> str | None:
    result = timestamp(value)
    if result:
        return result
    try:
        return parsedate_to_datetime(str(value)).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def weibo_id(value: str) -> str:
    if value.isdigit():
        return value
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    chunks = []
    while value:
        part, value = value[-4:], value[:-4]
        n = 0
        for ch in part:
            n = n * 62 + alphabet.index(ch)
        chunks.append(str(n).zfill(7) if value else str(n))
    return "".join(reversed(chunks))


def content_url(site: str, content_id: str) -> str:
    patterns = {"xiaohongshu": r"[a-fA-F0-9]{24}", "weibo": r"[A-Za-z0-9]{5,32}", "bilibili": r"(?:BV[A-Za-z0-9]{10}|av\d+)"}
    if not re.fullmatch(patterns[site], content_id):
        raise UnsupportedUrl("invalid content ID")
    if site == "xiaohongshu":
        return f"{BASES[site]}/explore/{content_id}"
    if site == "weibo":
        return f"https://m.weibo.cn/detail/{weibo_id(content_id)}"
    return f"{BASES[site]}/video/{content_id}"


def creator_url(site: str, creator_id: str) -> str:
    pattern = r"[a-fA-F0-9]{24}" if site == "xiaohongshu" else r"\d+"
    if not re.fullmatch(pattern, creator_id):
        raise UnsupportedUrl("invalid creator ID")
    if site == "xiaohongshu":
        return f"{BASES[site]}/user/profile/{creator_id}"
    if site == "weibo":
        return f"https://weibo.com/u/{creator_id}"
    return f"https://space.bilibili.com/{creator_id}"


def normalize_url(site: str, url: str, *, creator=False, allow_short=True) -> tuple[str, str | None]:
    try:
        url = urljoin(BASES[site], url)
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsupportedUrl("invalid URL") from exc
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in HOSTS[site] or parsed.username or parsed.password or port not in (None, 80, 443):
        raise UnsupportedUrl("URL does not belong to platform")
    if parsed.hostname in SHORT_HOSTS and allow_short:
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, "")), None
    path = parsed.path.rstrip("/")
    if creator:
        pattern = {"xiaohongshu": r"/user/profile/([a-fA-F0-9]{24})", "weibo": r"/(?:u/|profile/)?(\d+)", "bilibili": r"/(\d+)(?:/.*)?"}[site]
        if site == "bilibili" and parsed.hostname != "space.bilibili.com":
            raise UnsupportedUrl("creator must use space.bilibili.com")
    else:
        pattern = {"xiaohongshu": r"/(?:explore|discovery/item|search_result)/([a-fA-F0-9]{24})", "weibo": r"/(?:detail|status|\d+)/([A-Za-z0-9]{5,32})", "bilibili": r"/video/(BV[A-Za-z0-9]{10}|av\d+)"}[site]
    match = re.fullmatch(pattern, path)
    if not match:
        raise UnsupportedUrl("unsupported platform page")
    cid = match[1]
    if site == "weibo" and not creator:
        cid = weibo_id(cid)
    canonical = creator_url(site, cid) if creator else content_url(site, cid)
    # XHS detail links need the page's public xsec token to remain navigable.
    if site == "xiaohongshu":
        query = parse_qs(parsed.query)
        kept = {k: query[k][0] for k in ("xsec_token", "xsec_source") if query.get(k)}
        if kept:
            canonical += "?" + urlencode(kept)
    return canonical, cid


def search_url(site: str, query: str, sort="general") -> str:
    if site == "xiaohongshu":
        return BASES[site] + "/search_result?" + urlencode({"keyword": query, "source": "web_explore_feed"})
    if site == "weibo":
        return "https://s.weibo.com/weibo?" + urlencode({"q": query, **({"xsort": "time"} if sort == "latest" else {})})
    return "https://search.bilibili.com/all?" + urlencode({"keyword": query, "order": {"general": "totalrank", "latest": "pubdate"}[sort]})


def objects(html: str):
    """Read serialized state without evaluating arbitrary script text."""
    for script in BeautifulSoup(html, "lxml").select("script"):
        source = script.string or ""
        if len(source) > 2 * 1024 * 1024:
            continue
        match = re.search(r"(?:window\.)?(?:__INITIAL_STATE__|__NEXT_DATA__|\$render_data)\s*=\s*", source)
        try:
            if script.get("type") == "application/json" or script.get("id") in {"__NEXT_DATA__", "RENDER_DATA"}:
                data = json.loads(source if source.lstrip().startswith(("{", "[")) else unquote(source))
            elif match:
                data, _ = json.JSONDecoder().raw_decode(source[match.end():].lstrip())
            else:
                continue
        except (ValueError, RecursionError):
            continue
        stack, count = [data], 0
        while stack and count < 10000:
            obj = stack.pop()
            count += 1
            if isinstance(obj, dict):
                yield obj
                stack.extend(reversed([v for v in obj.values() if isinstance(v, (dict, list))]))
            elif isinstance(obj, list):
                stack.extend(reversed(obj))


def author(data: dict) -> dict:
    return {"user_id": str(first(data, "user_id", "userId", "id", "mid", default="")) or None,
            "nickname": first(data, "nickname", "nick_name", "nickName", "screen_name", "name", "uname"),
            "follower_count": parse_count(first(data, "followers_count", "fans", "follower"))}


def content_from_data(site: str, raw: dict) -> dict | None:
    data = raw
    media = {"cover_url": None, "image_urls": [], "tags": [], "duration_s": None}
    if site == "xiaohongshu":
        data = mapping(first(raw, "note_card", "noteCard", default=raw))
        cid = first(raw, "note_id", "noteId") or first(data, "note_id", "noteId")
        if not cid and any(k in data for k in ("interact_info", "interactInfo", "display_title")):
            cid = raw.get("id") or data.get("id")
        if not cid:
            return None
        stats = mapping(first(data, "interact_info", "interactInfo", default={}))
        fields = {"like_count": first(stats, "liked_count", "likedCount"), "collect_count": first(stats, "collected_count", "collectedCount"),
                  "comment_count": first(stats, "comment_count", "commentCount"), "share_count": first(stats, "share_count", "shareCount")}
        title, body = first(data, "title", "display_title", "displayTitle", default=""), data.get("desc", "")
        user, published = data.get("user") or {}, data.get("time")
        images = first(data, "image_list", "imageList", default=[])
        for item in images if isinstance(images, list) else []:
            image = media_url(first(item, "url_default", "urlDefault", "url"))
            if image:
                media["image_urls"].append(image)
        media["cover_url"] = media_url(first(data.get("cover"), "url_default", "urlDefault", "url")) or next(iter(media["image_urls"]), None)
        tags = first(data, "tag_list", "tagList", default=[])
        media["tags"] = [str(t["name"]) for t in tags if isinstance(t, dict) and t.get("name")] if isinstance(tags, list) else []
        media["content_type"] = data.get("type") or "note"
    elif site == "weibo":
        data = mapping(raw.get("mblog") or raw)
        if "text" not in data or not any(k in data for k in ("attitudes_count", "reposts_count", "mblogid")):
            return None
        cid = str(first(data, "idstr", "id", "mid", "mblogid", default=""))
        fields = {"like_count": data.get("attitudes_count"), "comment_count": data.get("comments_count"), "share_count": data.get("reposts_count")}
        body = first(data, "text_raw", default=mapping(data.get("longText")).get("longTextContent") or data["text"])
        title = clean(body)[:120]
        user, published = data.get("user") or {}, data.get("created_at")
        pictures = data.get("pics") or []
        for item in pictures if isinstance(pictures, list) else []:
            image = media_url(first(mapping(item).get("large"), "url") or first(item, "url"))
            if image:
                media["image_urls"].append(image)
        media["cover_url"] = next(iter(media["image_urls"]), None)
        media["content_type"] = "post"
    else:
        cid = data.get("bvid") or (f"av{data['aid']}" if data.get("aid") else None)
        if not cid or not data.get("title"):
            return None
        stats = mapping(data.get("stat") or data)
        fields = {"play_count": first(stats, "view", "play"), "like_count": stats.get("like"), "comment_count": first(stats, "reply", "review"),
                  "share_count": stats.get("share"), "collect_count": first(stats, "favorite", "favorites"), "coin_count": stats.get("coin"), "danmaku_count": first(stats, "danmaku", "video_review")}
        title, body = data.get("title", ""), first(data, "desc", "description", default="")
        user = data.get("owner") or {"mid": data.get("mid"), "name": data.get("author")}
        published = first(data, "pubdate", "created")
        media.update(cover_url=media_url(data.get("pic")), content_type="video")
        duration = data.get("duration")
        if isinstance(duration, (int, float)) and duration >= 0:
            media["duration_s"] = duration
    try:
        url = content_url(site, str(cid))
    except UnsupportedUrl:
        return None
    cid = weibo_id(str(cid)) if site == "weibo" else str(cid)
    if site == "xiaohongshu" and first(raw, "xsec_token", "xsecToken"):
        url += "?" + urlencode({"xsec_token": first(raw, "xsec_token", "xsecToken"), "xsec_source": "pc_search"})
    return {"content_id": cid, "url": url, "title": clean(title), "text": clean(body), "author": author(user), **media,
            "stats": {k: parse_count(v) for k, v in fields.items()}, "published_at": date(published),
            "source": "page_data", "aid": str(data["aid"]) if site == "bilibili" and data.get("aid") else None}


def parse_items(site: str, html: str) -> list[dict]:
    rows = {}
    for data in objects(html):
        row = content_from_data(site, data)
        if row:
            existing = rows.get(row["content_id"], {})
            rows[row["content_id"]] = {**row, **{k: v for k, v in existing.items() if v not in (None, "", {}, [])}}
    soup = BeautifulSoup(html, "lxml")
    for card in soup.select(CARDS[site]):
        if not visible(card):
            continue
        for link in card.select("a[href]"):
            try:
                url, cid = normalize_url(site, link.get("href", ""), allow_short=False)
            except (ValueError, UnsupportedUrl):
                continue
            if cid in rows:
                if site == "xiaohongshu" and "xsec_token=" in url:
                    rows[cid]["url"] = url
                break
            title = text(card.select_one(".title, .bili-video-card__info--tit, [node-type='feed_list_content_full'], [node-type='feed_list_content'], .txt")) or link.get("title") or text(link)
            uid = card.get("data-user-id")
            rows[cid] = {"content_id": cid, "url": url, "title": title, "text": title,
                         "author": {"nickname": text(card.select_one(".author .name, .name, .bili-video-card__info--author")) or None, "user_id": uid},
                         "stats": {"like_count": parse_count(text(card.select_one(".like-wrapper .count, [data-like-count]")))},
                         "published_at": date(card.get("data-published-at")), "source": "dom"}
            break
    return list(rows.values())


def parse_detail(site: str, html: str, url: str, cid: str) -> dict:
    row = next((r for r in parse_items(site, html) if r["content_id"] == cid or site == "bilibili" and cid == f"av{r.get('aid')}"), None)
    if row:
        if site == "xiaohongshu" and "xsec_token=" in url:
            row["url"] = url
        return row
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("#detail-title, h1.video-title, h1, [data-content-title]")
    body = soup.select_one("#detail-desc, .note-text, .detail_wbtext, .wbpro-feed-content, .basic-desc-info, [data-content-text]")
    og = soup.select_one('meta[property="og:title"]')
    title = text(node) or text(body)[:120] or (str(og.get("content", "")) if og else "")
    return {"content_id": cid, "url": url, "title": title, "text": text(body), "source": "dom",
            "author": {"nickname": text(soup.select_one(".author-container .username, .author .name, .up-name, [data-author]")) or None},
            "stats": {"like_count": parse_count(text(soup.select_one(".like-wrapper .count, .video-like .video-toolbar-item-text, [data-like-count]"))),
                      "comment_count": parse_count(text(soup.select_one(".chat-wrapper .count, .total-reply, [data-comment-count]")))}}


def parse_creator(site: str, html: str, url: str, cid: str) -> dict:
    for obj in objects(html):
        candidate = obj.get("basicInfo") or obj.get("userInfo") or obj
        if not isinstance(candidate, dict):
            continue
        user = author(candidate)
        identity = user["user_id"] or str(first(obj, "userId", "user_id", "mid", default=""))
        if identity == cid and user.get("nickname"):
            return {**user, "creator_id": cid, "url": url, "bio": first(candidate, "desc", "description", "sign"), "source": "page_data"}
    soup = BeautifulSoup(html, "lxml")
    return {"creator_id": cid, "url": url,
            "nickname": text(soup.select_one(".user-name, .username, #h-name, .nickname, [data-creator-name]")) or None,
            "bio": text(soup.select_one(".user-desc, #h-sign, .description, [data-creator-bio]")),
            "follower_count": parse_count(text(soup.select_one("[data-follower-count], #n-fs, .fans-count"))), "source": "dom"}


def parse_comments(site: str, html: str, params) -> list[dict]:
    rows, child_ids = {}, set()

    def from_data(data, parent=None, depth=0):
        cid = first(data, "rpid_str", "rpid", "id", "comment_id")
        body = data.get("content")
        body = body.get("message") if isinstance(body, dict) else first(data, "text", "content")
        if cid is None or not body or not any(k in data for k in ("user_info", "member", "user")):
            return None
        # A Weibo post is not a comment.
        if "attitudes_count" in data or "reposts_count" in data:
            return None
        parent = parent or first(data, "root_str", "root", "parent_id") or first(data.get("target_comment"), "id")
        row = {"comment_id": str(cid), "parent_id": str(parent) if parent and str(parent) != "0" else None,
               "text": clean(body), "like_count": parse_count(first(data, "like_count", "like", "like_counts")),
               "created_at": date(first(data, "create_time", "ctime", "created_at")),
               "commenter": author(first(data, "user_info", "member", "user", default={}) or {}),
               "reply_count": parse_count(first(data, "sub_comment_count", "subCommentCount", "rcount", "total_number")), "replies": []}
        if depth == 0:
            for key in ("sub_comments", "subComments", "replies", "comments"):
                children = data.get(key)
                if not isinstance(children, list):
                    continue
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    reply = from_data(child, row["comment_id"], 1)
                    if reply:
                        child_ids.add(reply["comment_id"])
                        if params.include_replies and len(row["replies"]) < params.max_replies_per_comment:
                            row["replies"].append(reply)
        return row

    for obj in objects(html):
        row = from_data(obj)
        if row:
            old = rows.get(row["comment_id"])
            if not old or len(row["replies"]) > len(old["replies"]):
                rows[row["comment_id"]] = row
    # Responses may expose second-level comments separately from their root.
    for row in list(rows.values()):
        parent = row.get("parent_id")
        if parent and parent in rows and row["comment_id"] != parent:
            child_ids.add(row["comment_id"])
            replies = rows[parent]["replies"]
            if params.include_replies and len(replies) < params.max_replies_per_comment and row["comment_id"] not in {r["comment_id"] for r in replies}:
                replies.append(row)

    def from_dom(node, parent=None):
        body = text(node.select_one(".note-text, .comment-text, .reply-content, #content, [data-comment-text]"))
        name = text(node.select_one(".name, .user-name, .username, #user-name, [data-comment-user]"))
        cid = node.get("data-comment-id") or node.get("data-id") or node.get("data-rpid")
        cid = str(cid or "dom:" + hashlib.sha256(f"{parent}:{name}:{body}".encode()).hexdigest()[:24])
        return {"comment_id": cid, "parent_id": parent, "text": body, "commenter": {"nickname": name or None},
                "created_at": date(node.get("data-created-at")), "like_count": parse_count(text(node.select_one(".like .count, .like-count, [data-like-count]"))),
                "replies": [], "source": "dom"}

    soup = BeautifulSoup(html, "lxml")
    candidates = soup.select(COMMENTS[site])
    for node in candidates:
        if not visible(node) or any(p in candidates for p in node.parents) or node.get("data-parent-id"):
            continue
        row = from_dom(node)
        if not row["text"] or row["comment_id"] in rows:
            continue
        if params.include_replies:
            row["replies"] = [from_dom(n, row["comment_id"]) for n in node.select(REPLIES) if visible(n)][:params.max_replies_per_comment]
        rows[row["comment_id"]] = row
    result = [r for key, r in rows.items() if key not in child_ids and not r.get("parent_id")]
    if params.redact_commenters:
        for row in result:
            for item in (row, *row["replies"]):
                item["commenter"] = {"user_id": None, "nickname": None}
    return result


def block_reason(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for css, code in (
        ("[class*='captcha'], [id*='captcha'], .geetest_panel, iframe[src*='captcha']", "CAPTCHA"),
        (".login-modal, .login-container, .login-wall, .passport-login-container, [data-login-wall]", "LOGIN_WALL"),
        ("[data-rate-limited]", "RATE_LIMITED"),
    ):
        if any(visible(n) for n in soup.select(css)):
            return code
    return None


def ended(html: str, comments=False) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return any(visible(n) for n in soup.select(COMMENT_END if comments else END))


def data_ended(html: str, comments=False) -> bool:
    for obj in objects(html):
        collection_keys = ("comments", "replies") if comments else ("items", "notes", "archives", "vlist")
        if not any(isinstance(obj.get(key), list) for key in collection_keys):
            continue
        if obj.get("has_more") is False or obj.get("hasMore") is False:
            return True
        if comments and mapping(obj.get("cursor")).get("is_end") is True:
            return True
    return False
