# agender-worker

无人值守浏览采集 Worker：公开网页抽取抖音播放/评论数据，或检索热点新闻与知识文章，把结构化结果回传。服务端 LLM 负责是否续派任务；本仓库只执行、只报告。

CLI：**`agender-worker`**。同机若还有 `lot-agent/deploy/rust/agender`（机器信息），那是另一个二进制。

设计见 [`docs/DESIGN.md`](docs/DESIGN.md)，协议见 [`docs/protocol.md`](docs/protocol.md)，运维见 [`docs/ops.md`](docs/ops.md)。

## 快速开始

```bash
# uv (CI) or pip:
uv sync --extra dev
# python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'

uv run pytest          # or: .venv/bin/pytest
uv run agender-worker --help
uv run agender-worker doctor
```

无服务端时把 `examples/*.json` 拷到 `data/inbox/tasks/` 后运行 `agender-worker worker`。

## 范围

- 公开页已渲染 DOM，不逆向 aw音私有 API，不打码
- 抖音评论 best-effort；无会话报 `needs_human`
- v1 热榜只交付 `baidu_hot`
- 产物留本机；认证仅 Bearer
