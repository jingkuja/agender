"""Typed extractor payloads, independent of the transport envelope version."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Record(BaseModel):
    model_config = ConfigDict(extra="allow")


class Author(Record):
    user_id: str | None = None
    sec_uid: str | None = None
    nickname: str | None = None
    follower_count: int | None = Field(default=None, ge=0)


class Stats(Record):
    play_count: int | None = Field(default=None, ge=0)
    like_count: int | None = Field(default=None, ge=0)
    comment_count: int | None = Field(default=None, ge=0)
    share_count: int | None = Field(default=None, ge=0)
    collect_count: int | None = Field(default=None, ge=0)


class Video(Record):
    video_id: str
    url: str = ""
    title: str = ""
    author: Author = Field(default_factory=Author)
    stats: Stats = Field(default_factory=Stats)
    published_at: str | None = None


class Reply(Record):
    comment_id: str
    parent_id: str | None = None
    text: str
    like_count: int | None = Field(default=None, ge=0)
    created_at: str | None = None
    commenter: Author = Field(default_factory=Author)


class Comment(Reply):
    reply_count: int | None = Field(default=None, ge=0)
    replies: list[Reply] = Field(default_factory=list)


class Payload(Record):
    result_schema_version: Literal["1.1"] = "1.1"
    observed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    stop_reason: str | None = None
    checkpoint_id: str | None = None


class Collection(Payload):
    returned: int = Field(default=0, ge=0)
    exhausted: bool = False
    has_more: bool | None = None
    limit_reached: bool = False


class DouyinSearchResult(Collection):
    kind: Literal["douyin.search"]
    query: str = ""
    items: list[Video] = Field(default_factory=list)


class DouyinStatsResult(Video, Payload):
    kind: Literal["douyin.video_stats"]


class DouyinCommentsResult(Collection):
    kind: Literal["douyin.video_comments", "douyin.video_bundle"]
    video: Video
    comments: list[Comment] = Field(default_factory=list)


class Creator(Record):
    creator_id: str
    nickname: str | None = None
    url: str = ""
    follower_count: int | None = Field(default=None, ge=0)


class CreatorProfileResult(Payload):
    kind: Literal["douyin.creator_profile"]
    creator: Creator


class CreatorPostsResult(Collection):
    kind: Literal["douyin.creator_posts"]
    creator: Creator
    items: list[Video] = Field(default_factory=list)


class SearchHit(Record):
    url: str
    title: str = ""
    snippet: str = ""


class Document(Record):
    url: str
    canonical_url: str
    title: str = ""
    fetch_status: Literal["ok", "skipped", "blocked", "failed"]
    body_text: str = ""
    body_chars: int = Field(default=0, ge=0)
    published_at: str | None = None
    error: dict[str, Any] | None = None


class WebSearchResult(Collection):
    kind: Literal["web.search"]
    query: str = ""
    serp: list[SearchHit] = Field(default_factory=list)
    engine_used: str | None = None
    engine_fallback: bool = False


class WebArticlesResult(Payload):
    kind: Literal["web.fetch_article"]
    documents: list[Document] = Field(default_factory=list)


class WebResearchResult(Payload):
    kind: Literal["web.research"]
    query: str = ""
    serp: list[SearchHit] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    engine_used: str | None = None
    engine_fallback: bool = False


class HotSource(Record):
    id: str
    status: Literal["ok", "skipped", "blocked", "failed"]
    items: list[SearchHit] = Field(default_factory=list)


class WebHotListResult(Payload):
    kind: Literal["web.hot_list"]
    sources: list[HotSource] = Field(default_factory=list)


class LoginResult(Payload):
    kind: Literal["session.login"]
    site: Literal["douyin", "xiaohongshu", "weibo", "bilibili"] = "douyin"
    sessions: dict[str, Literal["ok", "expired", "missing", "unknown"]] = Field(default_factory=dict)


class SocialContent(Record):
    content_id: str
    url: str
    title: str = ""
    text: str = ""
    author: Author = Field(default_factory=Author)
    stats: Stats = Field(default_factory=Stats)
    published_at: str | None = None
    content_type: str | None = None
    cover_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    duration_s: float | None = Field(default=None, ge=0)


class SocialSearchResult(Collection):
    kind: Literal["xiaohongshu.search", "weibo.search", "bilibili.search"]
    query: str = ""
    items: list[SocialContent] = Field(default_factory=list)


class SocialDetailResult(Payload):
    kind: Literal["xiaohongshu.detail", "weibo.detail", "bilibili.detail"]
    content: SocialContent


class SocialCommentsResult(Collection):
    kind: Literal["xiaohongshu.comments", "weibo.comments", "bilibili.comments", "xiaohongshu.bundle", "weibo.bundle", "bilibili.bundle"]
    content: SocialContent
    comments: list[Comment] = Field(default_factory=list)


class SocialCreatorProfileResult(Payload):
    kind: Literal["xiaohongshu.creator_profile", "weibo.creator_profile", "bilibili.creator_profile"]
    creator: Creator


class SocialCreatorPostsResult(Collection):
    kind: Literal["xiaohongshu.creator_posts", "weibo.creator_posts", "bilibili.creator_posts"]
    creator: Creator
    items: list[SocialContent] = Field(default_factory=list)


ResultPayload = Annotated[
    DouyinSearchResult | DouyinStatsResult | DouyinCommentsResult | CreatorProfileResult | CreatorPostsResult |
    WebSearchResult | WebArticlesResult | WebResearchResult | WebHotListResult | LoginResult |
    SocialSearchResult | SocialDetailResult | SocialCommentsResult | SocialCreatorProfileResult | SocialCreatorPostsResult,
    Field(discriminator="kind"),
]
RESULT_ADAPTER = TypeAdapter(ResultPayload)


def validate_result(value: dict) -> dict:
    return RESULT_ADAPTER.validate_python(value).model_dump(mode="json")
