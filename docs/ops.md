# agender-worker 运维

CLI 名是 **`agender-worker`**，避免与 `lot-agent/deploy/rust/agender`（机器信息，`:8080`）混淆。`python -m agender` 指向同一入口，文档不主推。

## 安装

```bash
uv sync --extra dev
uv run playwright install chromium   # 本机 Chrome 优先，失败才用捆绑 Chromium
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

不要把 token 写进 yaml。使用 `AGENDER_WORKER_TOKEN` 或 `AGENDER_TOKEN_FILE`。

## 联调（无服务端）

```bash
mkdir -p data/inbox/tasks
cp examples/web_search.json data/inbox/tasks/
uv run agender-worker doctor
uv run agender-worker worker
# 结果：data/outbox/results/ex_web_search.json
```

单次：

```bash
uv run agender-worker run --file examples/web_search.json
```

有 `AGENDER_SERVER_BASE_URL` 时改走 HttpClient。

## 抖音

1. `douyin.tos_ack: true` 且 `tools.douyin: true`
2. 默认匿名；评论需要会话
3. 登录：`uv run agender-worker session login --site douyin`（有头窗口，验证码/短信由人完成）
4. 或 LLM 下发 `session.login` 任务

评论无会话 → `needs_human` / `LOGIN_WALL`，不是空列表成功。

## GC 与指标

```bash
uv run agender-worker gc
```

指标只绑 `127.0.0.1:9108/metrics`。改成非 loopback 时 doctor 失败、daemon 拒绝暴露。

## systemd

见 `deploy/agender-worker.service`。建议仅在确认 Web 任务稳定后再开 `tools.douyin`。Docker 镜像名 `agender-worker`，默认只跑 `web.*`。

## 回滚

停进程。`tools.douyin: false` 后重启。删除 `data/profiles/douyin` 与 `data/sessions/` 即掉登录态。
