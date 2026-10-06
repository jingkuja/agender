# Agender Worker 协议

传输信封 `schema_version="1.0"` 保持兼容；本次抽取结果使用独立的 `result_schema_version="1.1"`。权威模型是 `src/agender/protocol/schemas.py` 和 `results.py`，JSON Schema 在同目录 `jsonschema/`。CI 检查导出文件与模型一致。

## 传输与确认

| 模式 | 条件 | 行为 |
| --- | --- | --- |
| FileClient | 未配置服务端 URL | inbox → 隐藏 claimed 文件 → 按 attempt 原子写入 outbox |
| HttpClient | 设置 `server.base_url` 或 `AGENDER_SERVER_BASE_URL` | Bearer HTTP，以下端点 |
| CLI `run --file` | 任意配置 | 一次本地执行，打印并保存结果；不向远端提交虚构租约 |

FileClient 保存 `data/outbox/results/{task_id}/{attempt}.json`；兼容文件 `{task_id}.json` 指向最大 attempt 的结果。同 attempt 重复写入返回原结果，不覆盖。损坏任务文件隔离到 inbox 的 `quarantine/`，产生 `INVALID_PARAMS` 失败结果；未知任务类型产生 `UNSUPPORTED_TYPE`；合法但暂不可用的类型留在队列。

`task_id` 仅接受字母、数字、`_ . : -`，首字符必须为字母或数字，最长 128 字符。`attempt` 为正整数；时间边界必须包含时区。未知主版本不执行，报告 `SCHEMA_MISMATCH`。

```
Authorization: Bearer <AGENDER_WORKER_TOKEN>
X-Worker-Id: worker-01
X-Worker-Version: 0.1.0
Idempotency-Key: {task_id}:{attempt}  # result
```

| 方法 | 路径 | 成功 body |
| --- | --- | --- |
| POST | `/v1/heartbeat` | `HeartbeatAck` |
| POST | `/v1/tasks/claim` | `TaskClaim`，task 可为 null |
| POST | `/v1/tasks/{id}/progress` | `ProgressAck` |
| POST | `/v1/tasks/{id}/result` | `ResultAck` |
| POST | `/v1/artifacts` | 预留；客户端不上传 |

服务端应支持 claim 的 `Idempotency-Key: claim:{worker_id}:{uuid}`：同一次 claim 请求的网络重试复用此键，返回同一任务/租约；下一次领取使用新键。结果幂等键仍是 `{task_id}:{attempt}`。任务传入的 `idempotency_key` 不覆盖客户端策略。

执行完成后先持久化，再提交。只有 `ok=true`、task_id、accepted_status、schema 主版本匹配的 ACK 才转为 `acked`。`ok=false` 保持 `submitting`，退避重放；不会重新采集，也不会领取下一任务。错误 ACK/401/403 停止 Worker 并保留待提交结果。409 标记 `superseded`，不再向已确认失效的租约提交。

HTTP：429 尊重 Retry-After（最大 60 秒）；5xx、网络错误最多重试 4 次；413 仅在有 artifacts 时移除它们重试；422 不重试。Worker 为网络阶段增加总时限；停机时受 `shutdown_grace_s` 约束。文件与 SQLite 无跨介质事务：claim 后崩溃通过隐藏文件恢复为 `WORKER_INTERRUPTED`，从不重新执行同 attempt。

## 租约与停止

`max_tasks=1`。busy 心跳必须返回同一 lease_id 且未来有效的 lease_until；缺失、不匹配、过期即取消任务。409 确认撤销后禁止继续浏览器操作。过期 `deadline_at` 不执行；软/硬截止、停止信号保留已采快照，有数据返回 `partial`，无数据返回失败或取消。验证码/登录墙仍为 `needs_human`，可同时附上先前已采数据。

进度 seq 每个 attempt 从 1 递增，导航/视频/滚动/文章等阶段最多每秒上报一次，计数同时写入本地事件。进度接口故障不抹掉已有数据。产物仍只保存在本机，上传接口保持 `stored=false`。

## 任务与结果

现有类型：`douyin.search`、`douyin.video_stats`、`douyin.video_comments`、`douyin.video_bundle`、`session.login`、`web.search`、`web.fetch_article`、`web.hot_list`、`web.research`。

新增 `douyin.creator_profile` / `douyin.creator_posts`，仅在 `douyin.creator_enabled=true` 时上报。参数传 `url`（www.douyin.com/user/...）或 `sec_uid`；作品参数 `max_posts` 默认 20、上限 200。Worker 不自行派发作品详情任务。

小红书、微博、B 站新增共 18 种任务：`xiaohongshu` / `weibo` / `bilibili` × `search` / `detail` / `comments` / `bundle` / `creator_profile` / `creator_posts`。详情使用 `url` 或 `content_id`，作者使用 `url` 或 `creator_id`；搜索 `max_results`、评论 `max_comments`、作品 `max_posts` 均默认 20、上限 200。`include_replies` 默认 false，`max_replies_per_comment` 默认 5、范围 0–50。详情结果为 `content`，评论/bundle 为 `content` + `comments`，列表为 `items`。规范字段使用 content_id，保留原平台可访问链接。

`session.login.params.site` 现支持四个社交平台，返回 `sessions[site]`。心跳报告启用平台的会话与能力，未登录的 comments/bundle 不进入 claim 能力列表。`agender-worker catalog` 导出各 task type 的参数 Schema、启用状态和任务模板，供 AI 规划器发现接口。

`hints_for_planner` 添加可选 `suggested_tasks: [{type, params}]`：搜索成功可建议详情，needs_human 可建议对应站点登录。Worker 不自动派发这些模板，服务端仍分配 task_id / attempt。浏览器模式读取启动环境，CDP 连接失败使用 `BROWSER_UNAVAILABLE`；详情参见 [多平台对接](social-crawling.md)。

`result.kind` 与任务类型一致；成功/部分成功结果按 `ResultPayload` 的 kind 联合类型校验。旧服务端需允许新元数据字段；若它严格固定旧 result 字段或把 `exhausted` 当作“本次结束”，应先升级消费逻辑。信封不强制把动态 result 变成新的必填类型，便于兼容旧失败回执。

| 字段 | 语义 |
| --- | --- |
| observed_at | 本次观测时间，UTC ISO 格式 |
| exhausted | 抖音列表仅在明确末尾标记时为 true；Web 文章批次表示输入已处理完毕 |
| limit_reached | 达到本次数量或页数配额，不表示全站/整条视频已采全 |
| has_more | true/false/null；无法确认时用 null |
| stop_reason | limit/end/page_limit/load_stall/deadline 等原因 |
| scan_scope | 抖音使用 `bounded_visible_dom`，不承诺完整历史 |
| checkpoint_id | 已持久化的可恢复快照引用，可能为 null |

未知统计值用 null；缺 ID 的评论用含视频及父评论上下文的指纹，并标记 `id_quality`。二级回复保留 parent_id；reply_count 未知时不能填 0。达到 max_comments 只完成本次限额，exhausted 保持 false。搜索排序 general/latest/most_liked 通过可见控件生效；无对应控件报告 `UNSUPPORTED_SORT`。

Web URL 去除常见跟踪参数与 fragment；正文结果包含 requested_url、canonical_url、body_sha256、extraction_version。空正文/验证码/登录墙不会计作正文成功。未知热榜来源不使用普通链接列表伪装结果。

## 内容与恢复

`store.content_enabled=true` 启用独立内容库。可恢复：抖音及三个新平台的搜索、评论/bundle、作者作品，以及 Web 搜索、文章、research。内容/评论/作者以 platform 与实体 ID 联合去重。服务端下发同 task_id 的更大 attempt，在 params 中携带 `resume_from`。其它采集参数、信封版本及抽取版本必须匹配；timeout 和 capture 等执行选项可调整。

断点只在内容已落库后保存；默认有效 24 小时。错误有 `CONTENT_STORE_DISABLED`、`UNSUPPORTED_RESUME`、`CHECKPOINT_NOT_FOUND`、`CHECKPOINT_EXPIRED`、`CHECKPOINT_ATTEMPT_MISMATCH`、`CHECKPOINT_MISMATCH`。达到配额或明确完成的最终结果不再新建断点；执行期间可能已有阶段断点。

恢复重新打开页面，以已见 ID 去重继续有界扫描，不保证回到原 DOM 位置。`known_ids`：抖音视频/评论 ID 或 Web 规范 URL；`since`：带时区的 ISO 起始时间。缺失或无时区的公开发布时间保留记录并标记 `date_filter_unknown=true`，不会误删未知时间记录。不因遇到一条旧记录而停止无序页面。资料/统计/登录/热榜不支持增量过滤。

## 凭据与 Schema

session.login 的 password 只在一次执行的内存中使用；持久化任务、claimed 文件、日志、结果时移除。CLI 读取包含 password 的 JSON 后立即原子改写为脱敏副本。登录任务不保存截图/HTML。普通诊断 HTML 去除脚本、表单及敏感属性，限制 2 MiB；截图仅作为操作诊断，本地保留。

```bash
python -m agender.protocol.export_schema
pytest -q tests/protocol/test_schemas.py
```
