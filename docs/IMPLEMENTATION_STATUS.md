# 功能补全实施记录

日期：2026-10-06。参照 `FEATURE_COMPLETION_PLAN.md` 在当前 Agender Worker 实现；没有拷贝 MediaCrawler 商业版或私有接口代码。Git 基线为 `a791033`，本次功能改动保留在工作区供审阅。

**结论：核心开发与本地回归已完成；尚未达到生产发布验收条件。** 真实站点、真实调度服务、8 小时长稳以及远程 CI/Docker 验证单独列为待办，不能由本地 fixture 成功推定通过。

## 后续增补：多平台与 CDP

同日增加小红书、微博、B 站的 18 种结构化任务，覆盖搜索、详情、评论/回复、bundle、作者资料/作品；接入 AI catalog、按站点心跳会话、后续任务建议、内容库与断点。默认浏览器改为 CDP，环境可切换 Playwright；CDP 仅关闭 Worker 标签页，保留原浏览器。新增配置、使用与验收边界见 [social-crawling.md](social-crawling.md)。以下 95 项与长稳数据为此前一轮记录，不替代多平台真实站点验收。

多平台增补的验证包含静态页面数据和本机真实 Chrome 的 CDP 生命周期、B 站 Shadow DOM 动态回复、页面 JSON 响应观测；未使用真实平台账号、未访问真实平台。新增三平台默认为关闭，启用时需要相应 tools 和 tos_ack。CDP 复用现有 Chrome 的网络配置，原受控出口描述仅适用于 Playwright 模式。

增补最终离线回归：`pytest -q -m 'not live'` **152 项通过**，`ruff check src tests scripts` 与 `git diff --check` 通过；18 个任务示例通过模型校验，catalog 可导出 29 种任务 Schema。

## 工作包落实情况

| 工作包 | 本轮交付 | 验收边界 |
| --- | --- | --- |
| PR01 基线/契约 | Git 基线；参数约束；独立 1.1 结果模型；全部参数与结果 JSON Schema；字段矩阵 | Schema 自动比对通过；真实样本仍需采集 |
| PR02 执行控制 | 截止、取消、有效租约、软/硬停止、内存及持久化部分结果 | 过期不执行、停止保留数据、抵抗取消时升级硬停止均有回归 |
| PR03 回传 | 正确 ACK 校验、待提交重放、禁止重复 attempt 执行、FileClient 原子/分 attempt 结果及坏文件恢复 | HTTP mock + 本地端到端通过；真实服务端合同待联调 |
| PR04 导航 | 逐跳校验、固定已校验 IP 的本机出口、动作限速、异步 robots、冷却撤下能力 | 本地 Chrome 重定向/禁止地址/取消通过；冷却按 web/douyin 分组，期满由下一任务探测 |
| PR05 会话 | 按站点 profile 锁、正确 cookie 来源、登录配置恢复、导入校验、关闭后重建及真实 readiness | 本地互斥/会话来源/浏览器恢复通过；登录仍需操作员 |
| PR06 抖音 | 分享短链、动态等待/滚动、回复展开、同页 bundle、参数执行、字段来源与完整性 | 静态 SSR + 本地 Chrome 虚拟列表通过；真实抖音选择器未验收 |
| PR07 Web | 空正文/验证页识别、URL 规范化、跨页去重、研究部分结果、受支持热榜注册 | 离线解析与真实 Chrome 本地正文通过；真实搜索/热榜页面待抽检 |
| PR08 内容/恢复 | 独立 content.db、实体 upsert/历史统计、事务、CLI JSONL/CSV、断点指纹/TTL/新 attempt、保留期保护 | 重复数据、回滚、导出、失效断点、崩溃恢复等回归通过 |
| PR09 作者 | creator_profile / creator_posts、作品配额、增量与恢复、能力开关、示例 | 本地作者样本通过；默认关闭，实站需验证后启用 |
| PR10 发布 | 阶段进度、脱敏 HTML、诊断限额、重试/等待/恢复指标、依赖锁、CI/Docker、协议/运维、长稳入口 | 本地检查与 wheel 构建通过；外部验收待完成 |

## 本轮验证证据

环境：macOS、Python 3.12.13、Playwright 1.62.0、本机 Google Chrome；测试浏览器临时 profile，受控出口只允许本地 fixture 服务器。

- `ruff check src tests scripts`：通过。
- `pytest -q -rs`：**95 项通过，无跳过**（原基线 32 项）。
- 30 秒持续本地管线：**234 个任务、234 次提交故障注入和恢复**通过；周期性关闭/重建浏览器，断言只有一个页面、无未 ACK 运行、同 URL 只有一个内容实体。此结果不是 8 小时长稳结论。
- `pip check`：依赖一致。
- `pip wheel --index-url https://pypi.org/simple --no-deps .`：成功构建 `agender_worker-0.1.0-py3-none-any.whl`。当前镜像未返回构建依赖，构建阶段使用官方 PyPI 临时环境。
- CLI 帮助、内容 status/export、数据库覆盖保护、Schema 漂移以及 Git 差异空白检查通过。

关键测试：`tests/worker/test_reliability.py`、`tests/browser/test_navigation.py`、`test_pipeline.py`、`test_session.py`、`tests/store/test_content.py`、`test_content_cli.py`、`test_retention.py`。本轮没有访问真实抖音站点，没有使用实际账号 cookie。

## 使用入口

在配置中按需开启：

```yaml
tools:
  douyin: true
douyin:
  tos_ack: true
  creator_enabled: true
  selector_pack_version: "2026-10-06"
store:
  content_enabled: true
```

完整默认配置见 `config/config.example.yaml`。作者示例为 `examples/douyin_creator_profile.json`、`douyin_creator_posts.json`，需要替换公开 sec_uid。`examples/resume_search.json` 展示新 attempt 恢复格式，需要替换原 task_id/真实 checkpoint_id，并保持原采集参数一致。

```bash
agender-worker -c config/config.yaml content status
agender-worker -c config/config.yaml content export --table contents --output data/exports/contents.jsonl
agender-worker -c config/config.yaml content export --table comments --format csv --output data/exports/comments.csv
```

传输信封保持 1.0，结果元数据为 1.1；服务端需要接受新字段、正确解释 exhausted/limit_reached，支持新任务及 claim 幂等键。断点恢复依赖 content_enabled，仅服务端下发更大的 attempt 才继续采集；任务结束后的回传重试不涉及重新采集。

## 待发布验收

| 项目 | 所需输入/执行 | 当前状态 |
| --- | --- | --- |
| 至少 20 个真实公开页面 | 由操作员确认可访问的 URL 与会话；记录成功数、分母、失败分类和必填字段 | 待提供样本并执行 |
| 真实 HTTP 服务端 | 测试地址/凭据；核对 ACK、lease 续期、claim/result 幂等、进度、可选字段 | 仅本地 mock 验证 |
| 8 小时本地长稳 | `AGENDER_REQUIRE_BROWSER=1 AGENDER_SOAK_SECONDS=28800 pytest -q -s tests/browser/test_pipeline.py` | 已提供入口，仅运行了 30 秒试验 |
| Ubuntu CI / Docker | 执行仓库 CI 和 `docker build -f deploy/Dockerfile -t agender-worker .` | 配置已更新，尚未远程运行 |
| 发布回滚演练 | 停机备份两库/profile；验证旧任务兼容，关闭新能力，恢复待提交结果 | 版本拒绝降级/数据保留已单测，生产演练未执行 |

建议真实样本分组共 20 条：普通详情 3、分享短链 2、有评论与无评论 3、折叠回复 2、搜索及排序 2、作者主页/作品 2、人工确认的登录墙/验证码 2、Web 正文/重定向/搜索/热榜 4。只记录自然出现的阻断，不人为触发挑战。每条记录 URL、时间、登录态、预期、实际状态、返回量、必填字段、脱敏 fixture 路径。

## 当前限制与设计取舍

- DOM 恢复重新导航并去重，不是服务端分页游标；无限列表只承诺请求配额与扫描边界。
- 未公开或未识别的字段保留 null；缺乏精确时区的发布时间不作强制 since 排除。旧已知值的字段观测时间可从 content.db 的 field_observed_at 查询。
- 内容库是可选本地沉淀；不会自动创建任务。现有实体历史不随运行 GC 删除，需按实际保留策略管理。
- robots 获取失败保守报错；浏览器出口阻止私网、媒体、WebSocket 和 Service Worker，因此部分依赖这些机制的站点尚不兼容。
- 熔断为进程内状态；增补后按五个平台分组，并非逐域持久化故障治理。依赖锁固定 macOS/Linux 包版本，不包含包哈希。
- 多平台已在上述增补中实现；操作面板、媒体下载、集中数据库、HTTP 产物上传仍为后续范围。
