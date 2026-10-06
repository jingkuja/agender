# 抖音公开页字段矩阵

自动化和 CI 不访问 douyin.com。当前选择器包为 **2026-10-06**。下表是已实现的来源和本地验证，真实页面可用性尚待操作员样本验收，不能据此宣称真实站点字段均可取得。

| 字段/行为 | 实现来源 | 本地覆盖 | 缺失处理 |
| --- | --- | --- | --- |
| 视频 ID / URL | 输入与最终允许域 URL | 数字 ID、短链、冲突检测 | UNSUPPORTED_URL |
| title / nickname | 可见 DOM、meta、匹配 aweme_id 的内联 JSON | 最小 DOM 与 SSR 样本 | 空值和 missing_fields |
| 用户 ID / sec_uid / 粉丝数 | 内联 author | SSR 样本 | null |
| 点赞/评论/分享/收藏/播放 | 可见 DOM 或 statistics | 万/亿/千/W/K/M、标签数字、SSR | 未暴露保持 null，不造 0 |
| 发布时间/时长/封面/音乐 | 内联 video/create_time/music；time 与 og:image 后备 | SSR 与日期单测 | null；无时区不猜测时区 |
| 评论与 parent_id | 可见评论/回复节点 | 动态 Chrome 虚拟列表与展开控件 | 稳定 ID 或标记指纹 |
| reply_count | 页面公开数量 | 已知/未知/限制样本 | 未知 null |
| 完整性 | 明确末尾标记、限额、加载变化 | 动态滚动、停滞、配额 | 限额不代表耗尽 |
| 作者资料/作品 | user 公开页 DOM/内联 JSON | 静态作者 fixture、同页复用 | 开关控制，实站待验收 |

视频 extraction 包含 selector_pack_version、field_sources、missing_fields、missing_reasons 和 raw_stats。列表中的稀疏卡片不自动补发详情请求；后续任务由服务端决定。精确时间缺失时 since 过滤保留记录并标记 date_filter_unknown。

现有 `tests/fixtures/html/douyin_*.html` 是最小化人工 fixture；补充 SSR/作者样本在 `tests/extractors/douyin/test_completion.py`，动态 DOM 位于 `tests/browser/test_navigation.py`。它们不冒充实站截图或 page.content()。

人工采集可用 `scripts/capture_fixture.py` 有头运行，检查脱敏后再提交样本；不能把含账号数据的原始页面直接加入仓库。至少 20 个实站样本的覆盖清单见 IMPLEMENTATION_STATUS.md。
