import json
from unittest.mock import AsyncMock, Mock

import pytest

from agender.browser.navigator import PageSnapshot
from agender.extractors.social import adapters as a
from agender.extractors.social.tool import SocialTool
from agender.protocol.results import validate_result
from agender.protocol.schemas import (
    SessionState,
    SocialContentParams,
    SocialCreatorParams,
    SocialSearchParams,
    TaskType,
)
from agender.worker.execution import ExecutionContext, ExecutionStopped
from agender.worker.router import Router
from agender.worker.status import evaluate_status


@pytest.mark.parametrize("site", a.BASES)
@pytest.mark.parametrize("action", ["search", "detail", "comments", "bundle", "creator_profile", "creator_posts"])
async def test_router_all_social_tasks(cfg, social_pages, site, action):
    page = social_pages[site]
    setattr(cfg.tools, site, True)
    getattr(cfg, site).tos_ack = True
    fetch = AsyncMock(return_value=page["html"])
    router = Router(cfg, None, fetch_social={site: fetch})
    params = {"query": "公开内容"} if action == "search" else {"creator_id": page["creator_id"]} if action.startswith("creator") else {"content_id": page["id"], "include_replies": True}
    task_type = TaskType(f"{site}.{action}")
    result = await router.execute(task_type, params, session=SessionState.OK, deadline=None)
    assert "error_code" not in result, result
    assert evaluate_status(task_type, result, None).value == "succeeded"
    validate_result(result)
    assert fetch.await_count == 1


async def test_incremental_resume_merges_pages_and_comment_replies(cfg, social_pages):
    page = social_pages["xiaohongshu"]
    execution = ExecutionContext()
    initial = {"comment_id": "old", "text": "Saved", "replies": []}
    execution.resume = {"comments": [initial]}
    tool = SocialTool(cfg, "xiaohongshu", AsyncMock(return_value=page["html"]), execution=execution)
    result = await tool.comments(SocialContentParams(content_id=page["id"], include_replies=True))
    assert [c["comment_id"] for c in result["comments"]] == ["old", "comment-1"]
    assert execution.result["returned"] == 2
    search = await tool.search(SocialSearchParams(query="q", known_ids=[page["id"]]))
    assert search["items"] == [] and search["exhausted"]


async def test_login_drift_filters_and_url_conflict(cfg, social_pages):
    for markup, code in [('<div class="login-wall">登录</div>', "LOGIN_WALL"), ("<div>changed</div>", "SELECTOR_DRIFT")]:
        tool = SocialTool(cfg, "xiaohongshu", AsyncMock(return_value=markup))
        result = await tool.search(SocialSearchParams(query="q"))
        assert result["error_code"] == code
    tool = SocialTool(cfg, "weibo", AsyncMock())
    assert (await tool.search(SocialSearchParams(query="q", sort="most_liked")))["error_code"] == "UNSUPPORTED_FILTER"
    tool.fetch.assert_not_awaited()
    drift = SocialTool(cfg, "bilibili", AsyncMock(return_value="<div>Unrecognized layout</div>"))
    result = await drift.detail(SocialContentParams(content_id="BV1xx411c7mD"))
    assert evaluate_status(TaskType.BILIBILI_DETAIL, result, result["error_code"]).value == "failed"
    login = PageSnapshot('<div class="login-wall">Login</div>', "https://space.bilibili.com/123456", "https://passport.bilibili.com/login")
    creator_tool = SocialTool(cfg, "bilibili", AsyncMock(return_value=login))
    result = await creator_tool.execute("creator_profile", SocialCreatorParams(creator_id="123456"))
    assert result["error_code"] == "LOGIN_WALL"
    result = await tool.execute("detail", SocialContentParams(content_id="123456", url="https://m.weibo.cn/detail/999999"))
    assert result["error_code"] == "UNSUPPORTED_URL"
    tool.fetch.assert_not_awaited()


async def test_short_url_and_cross_platform_redirect(cfg, social_pages):
    page = social_pages["bilibili"]
    fetch = AsyncMock(return_value=PageSnapshot(page["html"], "https://b23.tv/abcdef", a.content_url("bilibili", page["id"])))
    tool = SocialTool(cfg, "bilibili", fetch)
    result = await tool.detail(SocialContentParams(url="https://b23.tv/abcdef"))
    assert result["content"]["content_id"] == page["id"]
    fetch.return_value.final_url = "https://evil.example/video/" + page["id"]
    assert (await tool.execute("detail", SocialContentParams(url="https://b23.tv/abcdef")))["error_code"] == "UNSUPPORTED_URL"


async def test_dynamic_collection_keeps_partial_on_cancel(cfg, social_pages):
    page = social_pages["bilibili"]
    first = page["html"].replace('<div class="no-more">没有更多了</div>', "")
    execution = ExecutionContext()
    tool = SocialTool(cfg, "bilibili", None, execution=execution, navigator=Mock())
    async def advance(*args):
        execution.cancel("LEASE_LOST")
        execution.check()
    tool.advance = advance
    with pytest.raises(ExecutionStopped, match="LEASE_LOST"):
        await tool.collection(first, SocialSearchParams(query="q"), {"kind": "bilibili.search"}, limit=20)
    assert execution.result["returned"] == 1


async def test_only_expected_json_responses_are_observed(cfg):
    tool = SocialTool(cfg, "weibo", None)
    response = Mock(url="https://weibo.com/ajax/statuses/show?id=123456", headers={"content-type": "application/json"})
    response.body = AsyncMock(return_value=json.dumps({"data": {"text": "Public content"}}).encode())
    tool._on_response(response)
    import asyncio
    await asyncio.gather(*tool.response_tasks)
    assert len(tool.responses) == 1
    response.url = "https://weibo.com.evil.example/ajax/statuses/show"
    tool._on_response(response)
    assert not tool.response_tasks
