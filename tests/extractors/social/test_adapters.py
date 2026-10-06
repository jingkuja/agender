import json

import pytest

from agender.extractors.douyin.normalize import UnsupportedUrl
from agender.extractors.social import adapters as a
from agender.protocol.schemas import SocialContentParams


@pytest.mark.parametrize("site", a.BASES)
def test_realistic_page_shapes(site, social_pages):
    page = social_pages[site]
    rows = a.parse_items(site, page["html"])
    assert len(rows) == 1
    assert rows[0]["content_id"] == page["id"]
    assert rows[0]["title"] and rows[0]["published_at"]
    assert rows[0]["author"]["user_id"] == page["creator_id"]
    assert rows[0]["stats"]["like_count"] is not None
    comments = a.parse_comments(site, page["html"], SocialContentParams(content_id=page["id"], include_replies=True))
    assert [c["comment_id"] for c in comments] == ["comment-1"]
    assert comments[0]["text"] == "评论内容"
    assert [c["comment_id"] for c in comments[0]["replies"]] == ["reply-1"]
    assert comments[0]["replies"][0]["parent_id"] == "comment-1"
    creator = a.parse_creator(site, page["html"], a.creator_url(site, page["creator_id"]), page["creator_id"])
    assert creator["nickname"]


def test_xhs_search_card_preserves_public_link_and_zero(social_pages):
    page = social_pages["xiaohongshu"]
    card = {"id": page["id"], "xsec_token": "public-link-token", "note_card": {
        "display_title": "搜索卡片", "interact_info": {"liked_count": "0"}, "user": {"nickname": "作者"}}}
    html = '<script type="application/json">' + json.dumps({"items": [card]}) + '</script>'
    result = a.parse_items("xiaohongshu", html)[0]
    assert result["title"] == "搜索卡片"
    assert result["stats"]["like_count"] == 0
    assert "xsec_token=public-link-token" in result["url"]


def test_search_result_order_and_deduplication(social_pages):
    first = social_pages["bilibili"]["post"]
    second = {**first, "bvid": "BV2xx411c7mD"}
    html = '<script type="application/json">' + json.dumps({"items": [first, second, first]}) + '</script>'
    assert [r["content_id"] for r in a.parse_items("bilibili", html)] == [first["bvid"], second["bvid"]]


@pytest.mark.parametrize("site", a.BASES)
def test_url_rejects_other_hosts_paths_and_credentials(site, social_pages):
    for url in ("https://evil.example/video/BV1xx411c7mD", a.BASES[site] + ".evil.example/explore/test", a.BASES[site] + "/account/settings", "https://user:pass@" + a.BASES[site].split("//")[1]):
        with pytest.raises(UnsupportedUrl):
            a.normalize_url(site, url)
    cid = social_pages[site]["id"]
    assert a.normalize_url(site, a.content_url(site, cid))[1] == cid


def test_weibo_bid_and_bilibili_av_normalization(social_pages):
    assert a.normalize_url("weibo", "https://weibo.com/1234567890/z0JH2lOMb")[1] == "3501756485200075"
    row = a.parse_detail("bilibili", social_pages["bilibili"]["html"], "https://www.bilibili.com/video/av123456", "av123456")
    assert row["content_id"] == "BV1xx411c7mD"


@pytest.mark.parametrize("site", a.BASES)
def test_reply_limits_and_redaction(site, social_pages):
    params = SocialContentParams(content_id=social_pages[site]["id"], include_replies=True, max_replies_per_comment=0, redact_commenters=True)
    rows = a.parse_comments(site, social_pages[site]["html"], params)
    assert rows[0]["replies"] == []
    assert rows[0]["commenter"] == {"user_id": None, "nickname": None}


def test_bili_shadow_dom_snapshot_markup():
    html = '<bili-comment-thread-renderer data-id="root"><div id="content">Root</div><bili-comment-reply-renderer data-id="reply"><div id="content">Reply</div></bili-comment-reply-renderer></bili-comment-thread-renderer>'
    rows = a.parse_comments("bilibili", html, SocialContentParams(content_id="BV1xx411c7mD", include_replies=True))
    assert rows[0]["text"] == "Root"
    assert rows[0]["replies"][0]["text"] == "Reply"


def test_hidden_login_does_not_block_and_unknown_is_not_empty():
    assert a.block_reason('<div class="login-modal" style="display:none">登录</div>') is None
    assert a.block_reason('<div class="login-modal">登录</div>') == "LOGIN_WALL"
    assert not a.ended('<div>Unrecognized layout</div>')
    assert a.data_ended('<script type="application/json">{"data":{"comments":[],"has_more":false}}</script>', comments=True)


def test_detail_media_metadata_and_malformed_side_data(social_pages):
    post = {**social_pages["xiaohongshu"]["post"], "image_list": [{"url_default": "https://images.example/note.jpg"}],
            "tag_list": [{"name": "露营"}]}
    row = a.content_from_data("xiaohongshu", post)
    assert row["image_urls"] == ["https://images.example/note.jpg"]
    assert row["cover_url"] == row["image_urls"][0]
    assert row["tags"] == ["露营"]
    assert a.content_from_data("xiaohongshu", {"note_card": "unexpected"}) is None
    assert a.content_from_data("weibo", {"mblog": ["unexpected"]}) is None
