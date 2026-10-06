import json
from urllib.parse import quote

import pytest

from agender.browser.navigator import PageSnapshot
from agender.extractors.douyin.creator import CreatorTool
from agender.extractors.douyin.normalize import parse_count
from agender.extractors.douyin.tool import DouyinTool, parse_comments_html, parse_stats_html
from agender.protocol.schemas import DouyinCreatorParams, DouyinSearchParams, DouyinVideoParams, SessionState


@pytest.mark.parametrize('value,expected', [('点赞1.2万', 12000), ('回复 12', 12), ('1.5亿', 150000000), ('3.2K', 3200), ('--', None), ('-123', None)])
def test_labelled_counts(value, expected):
    assert parse_count(value) == expected


def test_comment_limit_and_unknown_reply_count(html):
    result = parse_comments_html(html('douyin_comments.html'), {'video_id': 'v'}, redact=False, max_comments=1)
    assert result['limit_reached'] and not result['exhausted']
    assert result['comments'][0]['reply_count'] is None


def test_reply_tree_redaction_and_stable_fingerprint():
    html = '''<div data-e2e="comment-item" data-uid="u1"><span class="comment-user">姓名</span>
      <p class="comment-text">正文</p><div class="reply-item" data-id="r"><p class="comment-text">回复</p></div></div>'''
    one = parse_comments_html(html, {'video_id': 'v'}, redact=True, max_comments=10, include_replies=True)
    two = parse_comments_html('<div>before</div>'+html, {'video_id': 'v'}, redact=False, max_comments=10, include_replies=True)
    assert len(one['comments']) == 1
    comment = one['comments'][0]
    assert comment['text'] == '正文'
    assert comment['comment_id'] == two['comments'][0]['comment_id']
    assert comment['id_quality'] == 'fingerprint'
    assert comment['commenter']['nickname'] is None
    assert comment['replies'][0]['parent_id'] == comment['comment_id']


def test_percent_encoded_inline_data_matches_target_video():
    data = {'items': [{'aweme_id': '99999', 'desc': 'wrong'}, {'aweme_id': '12345', 'desc': 'right', 'author': {'nickname': 'author', 'uid': 12},
                      'statistics': {'digg_count': 12}, 'video': {'duration': 3000, 'cover': {'url_list': ['https://example.org/cover.jpg']}}}]}
    html = '<script id="RENDER_DATA">'+quote(json.dumps(data))+'</script>'
    result = parse_stats_html(html, 'https://www.douyin.com/video/12345', selector_pack='test')
    assert result['title'] == 'right'
    assert result['duration_ms'] == 3000
    assert result['stats']['like_count'] == 12
    assert result['stats']['play_count'] is None
    assert result['extraction']['field_sources']['stats.like_count'] == 'inline_ssr'


@pytest.mark.asyncio
async def test_short_link_uses_resolved_destination(cfg, html):
    opened = []
    async def fetch(url):
        opened.append(url)
        return PageSnapshot(html('douyin_video.html'), url, 'https://www.douyin.com/video/7123456789012345678')
    out = await DouyinTool(cfg, fetch).video_stats(DouyinVideoParams(url='https://v.douyin.com/AbCd/'))
    assert out['video_id'] == '7123456789012345678'
    assert opened == ['https://v.douyin.com/AbCd/']


@pytest.mark.asyncio
async def test_bundle_uses_one_snapshot(cfg, html):
    calls = []
    async def fetch(url):
        calls.append(url)
        return html('douyin_comments.html')
    result = await DouyinTool(cfg, fetch, SessionState.OK).video_bundle(DouyinVideoParams(video_id='7123456789012345678'))
    assert len(calls) == 1
    assert result['returned'] == 2


@pytest.mark.asyncio
async def test_unsupported_sort_is_reported(cfg, html):
    async def fetch(url):
        return html('douyin_search.html')
    result = await DouyinTool(cfg, fetch).search(DouyinSearchParams(query='q', sort='latest'))
    assert result['error_code'] == 'UNSUPPORTED_SORT'


@pytest.mark.asyncio
async def test_creator_profile_and_posts(cfg):
    async def fetch(url):
        return '''<h1 data-e2e="creator-name">作者</h1><span class="follower-count">1.2万</span>
        <div data-e2e="search-card"><a href="/video/12345"><span class="title">作品</span></a></div>
        <div data-e2e="creator-posts-end">end</div>'''
    result = await CreatorTool(DouyinTool(cfg, fetch)).posts(DouyinCreatorParams(sec_uid='MS4wLjABauthor'))
    assert result['creator']['follower_count'] == 12000
    assert result['items'][0]['video_id'] == '12345'
    assert result['exhausted'] is True
