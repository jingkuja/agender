import json

import pytest


@pytest.fixture
def social_pages():
    xhs = "65abcdef0123456789abcdef"
    xhs_user = "64abcdef0123456789abcdef"
    pages = {
        "xiaohongshu": {
            "id": xhs, "creator_id": xhs_user,
            "post": {"note_id": xhs, "title": "公开笔记", "desc": "笔记正文", "time": 1728144000000,
                     "user": {"user_id": xhs_user, "nickname": "作者甲"},
                     "interact_info": {"liked_count": "1.2万", "comment_count": "2", "collected_count": "0"}},
            "comment": {"id": "comment-1", "content": "评论内容", "user_info": {"user_id": "reader-1", "nickname": "读者"},
                        "create_time": 1728144000000, "sub_comment_count": 1,
                        "sub_comments": [{"id": "reply-1", "content": "回复内容", "user_info": {"user_id": "reader-2", "nickname": "回复者"}}]},
            "creator": {"user_id": xhs_user, "nickname": "作者甲", "desc": "公开简介"},
        },
        "weibo": {
            "id": "5099999999999999", "creator_id": "12345678",
            "post": {"id": "5099999999999999", "text": "<p>微博正文</p>", "created_at": "Sun Oct 06 09:30:00 +0800 2024",
                     "user": {"id": "12345678", "screen_name": "作者乙"}, "attitudes_count": 20, "comments_count": 2, "reposts_count": 0},
            "comment": {"id": "comment-1", "text": "<b>评论内容</b>", "user": {"id": "reader-1", "screen_name": "读者"},
                        "comments": [{"id": "reply-1", "text": "回复内容", "user": {"id": "reader-2", "screen_name": "回复者"}}]},
            "creator": {"id": "12345678", "screen_name": "作者乙", "description": "公开简介", "followers_count": 100},
        },
        "bilibili": {
            "id": "BV1xx411c7mD", "creator_id": "12345678",
            "post": {"bvid": "BV1xx411c7mD", "aid": 123456, "title": "视频标题", "desc": "视频简介", "pubdate": 1728144000,
                     "owner": {"mid": "12345678", "name": "作者丙"}, "stat": {"view": 1000, "like": 12, "reply": 2, "coin": 0}},
            "comment": {"rpid": "comment-1", "root": 0, "content": {"message": "评论内容"}, "member": {"mid": "reader-1", "uname": "读者"},
                        "replies": [{"rpid": "reply-1", "root": "comment-1", "content": {"message": "回复内容"}, "member": {"mid": "reader-2", "uname": "回复者"}}]},
            "creator": {"mid": "12345678", "name": "作者丙", "sign": "公开简介", "fans": 100},
        },
    }
    for page in pages.values():
        state = {"items": [page["post"]], "creator": page["creator"], "comments": [page["comment"]]}
        source = json.dumps(state, ensure_ascii=False).replace("<", "\\u003c")
        page["html"] = f'<html><body><script>window.__INITIAL_STATE__={source};</script><div class="no-more">没有更多了</div></body></html>'
    return pages
