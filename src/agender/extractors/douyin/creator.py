from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from agender.extractors.douyin import selectors as sel
from agender.extractors.douyin.normalize import UnsupportedUrl, parse_count
from agender.extractors.douyin.tool import _text, has_captcha, has_login_wall, inline_objects


def creator_url(params) -> tuple[str, str]:
    uid = params.sec_uid
    if params.url:
        parsed = urlparse(params.url)
        match = re.fullmatch(r"/user/([A-Za-z0-9_-]+)/?", parsed.path)
        if parsed.scheme not in {"https", "http"} or parsed.hostname != "www.douyin.com" or parsed.username or parsed.password or not match:
            raise UnsupportedUrl("unsupported creator URL")
        if uid and uid != match[1]:
            raise UnsupportedUrl("creator ID conflicts with URL")
        uid = match[1]
    return f"https://www.douyin.com/user/{uid}", uid


class CreatorTool:
    def __init__(self, tool):
        self.tool = tool

    async def profile(self, params) -> dict:
        url, uid = creator_url(params)
        opened = await self.tool.open(url, f"{sel.CREATOR_NAME}, {sel.LOGIN_WALL}, {sel.CAPTCHA}")
        soup = BeautifulSoup(opened.html, "lxml")
        data = next((item for item in inline_objects(opened.html) if item.get("sec_uid") == uid), {})
        creator = {
            "creator_id": uid, "sec_uid": uid, "url": url,
            "user_id": str(data["uid"]) if data.get("uid") is not None else None,
            "nickname": _text(soup.select_one(sel.CREATOR_NAME)) or data.get("nickname"),
            "bio": _text(soup.select_one(sel.CREATOR_BIO)) or data.get("signature"),
            "follower_count": parse_count(_text(soup.select_one(sel.CREATOR_FOLLOWERS))) if soup.select_one(sel.CREATOR_FOLLOWERS) else parse_count(data.get("follower_count")),
            "following_count": parse_count(data.get("following_count")),
            "total_likes": parse_count(data.get("total_favorited")),
            "observed_at": datetime.now(UTC).isoformat(),
        }
        result = {"kind": "douyin.creator_profile", "creator": creator,
                  "missing_fields": [key for key, value in creator.items() if value is None]}
        if has_captcha(opened.html):
            result["error_code"] = "CAPTCHA"
        elif not creator["nickname"]:
            result["error_code"] = "LOGIN_WALL" if has_login_wall(opened.html) else "SELECTOR_DRIFT"
        await self.tool.snapshot(result, "creator")
        return result

    async def posts(self, params) -> dict:
        profile = await self.profile(params)
        if profile.get("error_code"):
            return {**profile, "kind": "douyin.creator_posts", "items": [], "returned": 0}
        return await self.tool.collect_posts(
            self.tool.last_html, params, query=profile["creator"]["sec_uid"], kind="douyin.creator_posts",
            max_results=params.max_posts, end_selector=sel.CREATOR_POSTS_END, extra={"creator": profile["creator"]},
        )
