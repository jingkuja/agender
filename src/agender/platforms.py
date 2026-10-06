"""Platform identities shared by browser, protocol and extractors."""
from __future__ import annotations

from typing import Literal

SocialSite = Literal["xiaohongshu", "weibo", "bilibili"]
Site = Literal["web", "douyin", "xiaohongshu", "weibo", "bilibili"]
SOCIAL_SITES = ("xiaohongshu", "weibo", "bilibili")
SESSION_SITES = ("douyin", *SOCIAL_SITES)
SITES = ("web", *SESSION_SITES)
SOCIAL_ACTIONS = ("search", "detail", "comments", "bundle", "creator_profile", "creator_posts")
DOMAINS = {
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "weibo": ("weibo.com", "weibo.cn", "sina.com.cn"),
    "bilibili": ("bilibili.com", "b23.tv"),
}


def belongs_to_site(host: str, site: str) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in DOMAINS.get(site, ()))


def task_site(task_type: str, params: dict | None = None) -> str:
    return (params or {}).get("site", "douyin") if task_type == "session.login" else task_type.split(".", 1)[0]
