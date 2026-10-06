from __future__ import annotations

# Fixture-backed CSS. PR6a matrix may replace these after a live capture.

SEARCH_CARD = "[data-e2e='search-card'], .search-result-card, a[href*='/video/']"
SEARCH_TITLE = "[data-e2e='search-card-desc'], .title, img[alt]"
VIDEO_TITLE = "[data-e2e='video-desc'], .video-info-detail h1, h1, [data-title]"
VIDEO_AUTHOR = "[data-e2e='video-author-name'], a.author-name, .author-name, [data-e2e='feed-author-name']"
VIDEO_LIKE = "[data-e2e='like-count'], [data-e2e='video-like-count']"
VIDEO_COMMENT_COUNT = "[data-e2e='comment-count'], [data-e2e='video-comment-count']"
VIDEO_SHARE = "[data-e2e='share-count']"
VIDEO_COLLECT = "[data-e2e='collect-count']"
VIDEO_PLAY = "[data-e2e='play-count'], [data-e2e='video-play-count']"
COMMENT_LIST = "[data-e2e='comment-list'], .comment-list, ul.comments"
COMMENT_ITEM = "[data-e2e='comment-item'], .comment-item, li.comment"
COMMENT_TEXT = "[data-e2e='comment-text'], .comment-text, p"
COMMENT_LIKE = "[data-e2e='comment-like-count']"
COMMENT_USER = "[data-e2e='comment-user-name'], .comment-user"
LOGIN_WALL = "[data-e2e='login-guide'], .login-guide, .login-panel"
CAPTCHA = "[class*='captcha'], [id*='captcha']"
