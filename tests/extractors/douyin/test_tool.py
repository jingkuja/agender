import pytest

from agender.extractors.douyin.normalize import UnsupportedUrl, canonicalize_video_url, parse_count
from agender.extractors.douyin.tool import DouyinTool
from agender.protocol.schemas import DouyinSearchParams, DouyinVideoParams, SessionState


def test_parse_count():
    assert parse_count("1.2万") == 12000
    assert parse_count("86") == 86


def test_short_link_id():
    canonical, vid = canonicalize_video_url("https://www.iesdouyin.com/share/video/7123456789012345678")
    assert vid == "7123456789012345678"
    assert "/video/" in canonical


def test_note_unsupported():
    try:
        canonicalize_video_url("https://www.douyin.com/note/123")
        raise AssertionError
    except UnsupportedUrl:
        pass


@pytest.mark.asyncio
async def test_search_and_stats(cfg, html):
    async def fetch(url: str) -> str:
        if "/search/" in url:
            return html("douyin_search.html")
        return html("douyin_video.html")

    tool = DouyinTool(cfg, fetch, session_state=SessionState.MISSING)
    search = await tool.search(DouyinSearchParams(query="新能源汽车"))
    assert search["returned"] == 1
    stats = await tool.video_stats(DouyinVideoParams(video_id="7123456789012345678"))
    assert stats["title"] == "新能源汽车试驾"
    assert stats["author"]["nickname"] == "示例作者"
    assert stats["stats"]["like_count"] == 12000
    assert stats["stats"]["play_count"] is None


@pytest.mark.asyncio
async def test_comments_need_session(cfg, html):
    async def fetch(url: str) -> str:
        return html("douyin_comments.html")

    tool = DouyinTool(cfg, fetch, session_state=SessionState.MISSING)
    out = await tool.video_comments(DouyinVideoParams(url="https://www.douyin.com/video/7123456789012345678"))
    assert out["error_code"] == "LOGIN_WALL"
    assert out["comments"] == []

    tool.session_state = SessionState.OK
    out = await tool.video_comments(DouyinVideoParams(url="https://www.douyin.com/video/7123456789012345678"))
    assert out["returned"] == 2
    assert "error_code" not in out
