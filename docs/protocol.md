# Agender Worker 协议 1.0

本文冻结 Worker 面向未来服务端的 HTTP 合同。权威模型在 `src/agender/protocol/schemas.py`，导出 JSON Schema 在 `src/agender/protocol/jsonschema/`。

`schema_version` 当前为 `"1.0"`。主版本 >1 的任务提交 `failed` / `SCHEMA_MISMATCH`，不执行。

## Transport

| 模式 | 条件 | 实现 |
| --- | --- | --- |
| FileClient | `server.base_url` 空且未设 `AGENDER_SERVER_BASE_URL` | `data/inbox/tasks/*.json` → `data/outbox/results/{task_id}.json` |
| HttpClient | Base URL 非空 | 下列端点；`Authorization: Bearer` |

v1 两种都实现。HttpClient 不是 stub。

## 认证

```
Authorization: Bearer <AGENDER_WORKER_TOKEN>
X-Worker-Id: worker-01
X-Worker-Version: 0.1.0
Idempotency-Key: {task_id}:{attempt}   # 仅 result
```

任务 JSON 里的 `idempotency_key` 字段 **忽略**。v1 仅 Bearer。

## 端点

| 方法 | 路径 | 成功 body |
| --- | --- | --- |
| POST | `/v1/heartbeat` | `HeartbeatAck` |
| POST | `/v1/tasks/claim` | `TaskClaim`（`task` 可 null） |
| POST | `/v1/tasks/{id}/progress` | `ProgressAck` |
| POST | `/v1/tasks/{id}/result` | `ResultAck` |
| POST | `/v1/artifacts` | `ArtifactUploadResponse`（v1 客户端 **不调用**） |

HTTP 映射：401/403 → 停机 `UNAUTHORIZED`；**409 → `LEASE_LOST`**；413 去掉 artifacts 再试；422 `INVALID_PARAMS`；429 退避；5xx 指数退避最多 4 次。

## 租约

默认 claim 租约 240s。busy 心跳 **必须** 带回 `HeartbeatAck.lease.lease_until`。缺失、过短或 409 视为租约丢失：停止导航；若尚未 409 到 result 端点则提交 `partial`/`failed`。FileClient 每次心跳本地 +240s，无 409。

`max_tasks` 唯一合法值：`1`。

claim 到未知 `type` / 坏 params / 坏 schema：**必须** `submit_result`，禁止静默丢弃。

## Task types

`douyin.search` `douyin.video_stats` `douyin.video_comments` `douyin.video_bundle` `session.login` `web.search` `web.fetch_article` `web.hot_list` `web.research`

结果 `result.kind` 等于任务 type。成功矩阵见 `docs/DESIGN.md` Data Model。

## 密码

`session.login` 的 `password` 只允许出现在内存中的一次 payload。写入 SQLite、inbox、日志、结果前删除该键。

## JSON Schema 导出

```bash
python -m agender.protocol.export_schema
```
