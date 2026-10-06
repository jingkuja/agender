# agender-worker 运维

## 安装与验证

Python 3.12+；本轮验证环境是 Python 3.12.13、macOS、本机 Chrome。CI 使用 Ubuntu 和 Playwright Chromium；Docker 默认仅 Web，镜像安装 Chromium 及系统依赖。CI/Docker 的远程执行结果需在发布环境复核。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.lock
pip install --no-deps -e .
playwright install chromium
cp config/config.example.yaml config/config.yaml
cp .env.example .env
agender-worker -c config/config.yaml doctor
ruff check src tests scripts
AGENDER_REQUIRE_BROWSER=1 pytest -q -m 'not live'
```

本机默认 CDP。运行 doctor/worker 前先按 [多平台与 CDP 文档](social-crawling.md) 启动带调试端口的专用 Chrome；或设置 `AGENDER_BROWSER_MODE=playwright` 使用上面的 Chromium 安装。环境变量 `AGENDER_CDP_ENDPOINT_URL` 指定已有 Chrome 地址。Docker 镜像显式默认 Playwright，保持容器内启动行为。

生产可用 requirements.lock。升级后在干净环境安装并验证，再用 `python scripts/freeze_dependencies.py` 更新两个锁文件；不要仅改锁而跳过回归。锁文件固定版本，不提供依赖包哈希。HTTP token 通过 AGENDER_WORKER_TOKEN 或 AGENDER_TOKEN_FILE 配置。

## 任务与恢复

FileClient：把合法任务 JSON 放入 `data/inbox/tasks/`。结果保存在 `data/outbox/results/{task_id}/{attempt}.json`，并维护兼容的 `{task_id}.json`。不要覆盖正在处理的 claimed 文件。未知类型有失败结果；损坏文件移入 quarantine。每次新的采集使用新 task_id 或更大的 attempt；重复同 attempt 只返回已有结果。

HTTP daemon 使用 AGENDER_SERVER_BASE_URL；单次 `run --file` 始终为本地执行。若运行库还有未完成任务，单次命令拒绝开始，需先用原传输方式处理待提交记录；禁止为排除故障直接删库或切换传输方式确认旧结果。

提交失败时 Worker 保留 submitting、退避重放并暂停领取。401/403/错误 ACK 会停止，先修复鉴权或服务端合同，再以相同运行库启动。409 终结旧租约。进程意外中断只重放已有结果，未产生结果的运行报告 WORKER_INTERRUPTED；不会自动重采。

## 内容库与断点

```yaml
store:
  content_enabled: true
  content_path: data/content.db
  checkpoint_ttl_s: 86400
```

运行库 agender.db 和内容库 content.db 独立。实体按平台/ID 更新；缺失字段不抹去已知值，field_observed_at 表示各字段的新鲜度。统计按观测时间保留历史。开启内容库不意味着自动从中生成任务或过滤全部旧数据；增量边界由请求的 known_ids/since 指定。

```bash
agender-worker -c config/config.yaml content status
agender-worker -c config/config.yaml content export --table contents --output data/exports/contents.jsonl
agender-worker -c config/config.yaml content export --table metric_snapshots --format csv --output data/exports/metrics.csv
```

导出表可选 contents/comments/creators/metric_snapshots。JSONL 内含 data 对象；CSV 内含 data_json 列，适合保留嵌套字段。文件通过临时文件替换，既有目标会被新快照覆盖。内容库不随任务历史 GC 自动删除。

服务端收到 checkpoint_id 后，使用同 task_id、更大 attempt、原查询参数，加 `resume_from` 续采。只修改 timeout/capture 等非采集选项。参数、版本、有效期不匹配明确失败。没有页面位置保证：重新导航并去重扫描。

## 抖音与会话

1. 设置 tools.douyin=true 和 douyin.tos_ack=true；作者任务再设 creator_enabled=true。
2. 停止使用同一抖音 profile 的 Worker，再运行 `agender-worker -c config/config.yaml session login --site douyin`；登录强制有头，完成后恢复配置。
3. 评论需要有效会话；不能访问时返回 needs_human，而不是虚构空评论成功。
4. creator 示例中的 sec_uid 为占位符，使用操作员提供的公开主页值替换。

Playwright 模式下 Worker 和 CLI 共用每站点 profile 锁；每个运行库也有 Worker 锁。不同进程可以使用不同站点/不同 profile，但不能争用同一 profile。Web cookies 不参与抖音会话判定，站点切换时缓存站点会话信息。备份仅在首次空 profile 启动或显式导入时使用，不覆盖已有 profile。

CDP 连接的 Chrome 拥有 profile，Worker 不获取该 profile 锁，也不关闭共享 context/Chrome；每个站点的 Cookie 读取和导出按域名限定。小红书、微博、B 站使用相同会话命令，如 `agender-worker session login --site weibo`；启用对应 `tools.<site>` 和 `<site>.tos_ack` 后可以下发六类采集任务。完整清单用 `agender-worker catalog` 查询。

## 导航、冷却与诊断

导航及显式加载动作按 host 限速，示例配置的社交平台主页面域名为 6 次/分钟，其它 10 次/分钟；静态子资源不占此动作配额。Playwright 模式的浏览器网络经本机临时端口的校验出口，连接固定到已检查的 IP；不提供代理轮换。Playwright 禁媒体、Service Worker 和 WebSocket；CDP 仅对 Worker 页施加 URL/重定向、媒体和 WebSocket 拦截，复用 Chrome 原有网络及 Service Worker 配置，不能声明与 Playwright 相同的进程级出口保证。

Web robots 为异步读取，失败时返回 ROBOTS_UNAVAILABLE，缓存 6 小时。当前熔断按 web/douyin/xiaohongshu/weibo/bilibili 分组；连续 5 次指定阻断错误触发默认 30 分钟冷却，心跳撤下能力，期满允许下一项有界任务探测。重启清空内存冷却计数。

capture_html 可设 never/on_failure/always；HTML 脱敏并限 2 MiB，session.login 禁止保存截图/HTML。截图不会自动遮蔽页面个人信息，按现有本地诊断权限管理。GC 默认清理 7 天产物、30 天已终结运行、14 天日志，并清除过期断点；未 ACK 运行及其产物受保护。

```bash
agender-worker -c config/config.yaml gc
```

指标仅绑定 loopback 的 127.0.0.1:9108/metrics。新增提交重试、断点恢复、限速等待指标；任务耗时、成功数、阻断及最近成功时间继续保留。事件库记录进度阶段与提交错误码，不记录任务密码。

## 持续运行验收

测试仅使用本地动态页面和临时 profile，不访问真实抖音：

```bash
AGENDER_REQUIRE_BROWSER=1 AGENDER_SOAK_SECONDS=28800 pytest -q -s tests/browser/test_pipeline.py
```

该循环持续执行真实浏览器采集、内容 upsert、提交断网注入、原结果重放，并周期性关闭/重开浏览器，检查无未 ACK 结果及单页面约束。它提供 8 小时验收入口；短时通过不等同于长稳通过。真实页面样本清单见 IMPLEMENTATION_STATUS.md。

## 发布与回滚

按本地测试 → HTTP mock → 真实服务端单 Worker → 人工站点样本推进。部署示例在 deploy/agender-worker.service 和 Dockerfile；Docker 有头抖音登录需要额外桌面环境，不包含在默认镜像使用范围。

回滚前停止领取并等待有界收尾，保留待提交结果，以 SQLite backup API 或停机后的完整文件副本备份两库（包含尚未 checkpoint 的 WAL，不能只复制正在写的主文件）。运行库版本仍为 1，旧表未破坏；内容库为独立版本 1。遇到未来版本拒绝启动，不偷偷降级。关闭 creator_enabled/content_enabled 可停用新增能力；若回退到不认识新任务的旧程序，服务端同时停止下发新任务和新结果字段要求。恢复会话不要随回滚删除 profile。
