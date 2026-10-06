# agender-worker

公开网页浏览采集 Worker，支持抖音、小红书、微博、B 站的搜索、详情、评论/回复、作者资料和作品，以及 Web 检索、文章和热榜。AI 调度端通过结构化任务下发指令；Worker 执行、保存数据并回传进度、结果和后续任务建议。

CLI：**`agender-worker`**。相邻项目的机器信息程序 `agender` 是另一个二进制。

## 快速开始

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.lock
pip install --no-deps -e .
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

默认 `CDP` 模式：连接已经开启远程调试的 Chrome，并复用该浏览器的登录态。macOS 可先启动专用 Chrome，再在这个窗口登录需要采集的平台：

```bash
open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$PWD/data/chrome-cdp"
agender-worker -c config/config.yaml doctor
```

普通未开启调试端口的 Chrome 无法直接连接；Chrome 136+ 需要独立的 `--user-data-dir`。CDP 只新建和关闭 Worker 标签页，保留已有标签页与浏览器。连接失败会报告配置提示，不自动切换模式。详见 [多平台、CDP 与 AI 对接](docs/social-crawling.md)。

通过环境在**项目启动时**选择浏览器模式（环境变量优先于 YAML）：

```bash
# 连接已有 Chrome，默认地址 http://127.0.0.1:9222
AGENDER_BROWSER_MODE=cdp AGENDER_CDP_ENDPOINT_URL=http://127.0.0.1:9222 agender-worker worker

# 由项目启动和管理 Playwright 浏览器
playwright install chromium
AGENDER_BROWSER_MODE=playwright agender-worker worker
```

先通过 FileClient 联调：

```bash
mkdir -p data/inbox/tasks
cp examples/web_search.json data/inbox/tasks/
agender-worker -c config/config.yaml worker
# 或单次本地执行：
agender-worker -c config/config.yaml run --file examples/web_search.json
```

开发环境使用固定版本的 requirements-dev.lock；生产安装 requirements.lock。锁文件覆盖 macOS/Linux 的依赖版本，未包含包哈希。`pytest -q -m 'not live'` 使用临时浏览器和本地页面验证 Playwright/CDP，不访问真实平台或用户现有 Chrome。

启用新平台时，在 YAML 设置 `tools.xiaohongshu` / `tools.weibo` / `tools.bilibili` 及相应的 `<平台>.tos_ack: true`。任务类型为 `<平台>.search/detail/comments/bundle/creator_profile/creator_posts`，`params` 模型与示例见 [协议](docs/protocol.md)。

```bash
agender-worker catalog # AI 可读取的任务模板、参数 Schema 与配置开关
agender-worker session login --site xiaohongshu
agender-worker run --file examples/xiaohongshu_search.json
```

## 新增能力

- 有界取消、租约失效停止、部分结果保留；未 ACK 的结果持久化重放，同 attempt 不重采。
- 动态评论滚动、回复展开、同页 bundle、短链解析、公开 DOM/内联 JSON 字段来源。
- 作者资料/作品、内容实体去重、互动历史、JSONL/CSV 导出、新 attempt 断点恢复。
- 逐跳导航检查、受控出口、可取消限速、异步 robots、站点冷却与阶段进度。

作者能力默认关闭，需开启 `tools.douyin`、`douyin.tos_ack`、`douyin.creator_enabled`。内容与断点需开启 `store.content_enabled`。登录用 `agender-worker session login --site douyin`，验证码/短信由操作员完成。

```bash
agender-worker -c config/config.yaml content status
agender-worker -c config/config.yaml content export --table contents --format jsonl --output data/exports/contents.jsonl
agender-worker -c config/config.yaml content export --table comments --format csv --output data/exports/comments.csv
```

实现与验证记录见 [IMPLEMENTATION_STATUS](docs/IMPLEMENTATION_STATUS.md)，使用约定见 [协议](docs/protocol.md) 和 [运维](docs/ops.md)。原 [设计](docs/DESIGN.md) 保留历史方案，本次语义修订以协议为准；需求依据见 [功能补全计划](docs/FEATURE_COMPLETION_PLAN.md)。

采集公开页渲染 DOM、页面内联状态和该页面自身请求的 JSON 数据，B 站评论支持展开的 Shadow DOM。评论和作者页面依赖登录状态及页面结构；三平台真实账号验收尚未完成。热榜仅启用有解析器的 baidu_hot；产物保存在本机。媒体下载、操作面板及 AI 服务端本身不在此 Worker 内实现。
