# 抖音公开页字段矩阵（PR6a）

CI 与自动化 **不得** 访问 douyin.com。本表在 live headed 捕获前是工作假设；`scripts/capture_fixture.py` 跑完后以实测覆盖。

当前仓库 fixture：`tests/fixtures/html/douyin_*.html`（最小化 DOM，不是 full `page.content()`）。

| 字段 | 未登录（假设） | 已登录（假设） | fixture `douyin_video.html` |
| --- | --- | --- | --- |
| title | 常可得 | 可得 | 可得 |
| author.nickname | 常可得 | 可得 | 可得 |
| stats.like/comment/share | 常可得 | 可得 | like=12000 |
| stats.play_count | 常常缺失 → null | 可能可见 | **null** |
| 评论列表节点 | 面板常不挂载 | 可滚动 | 见 `douyin_comments.html` |

选择器包版本：`2026-09-11`（`extractors/douyin/selectors.py`）。

短链 allowlist：`v.douyin.com`、`www.douyin.com`、`www.iesdouyin.com`。`/note/` → `UNSUPPORTED_URL`。
