from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime

from agender.platforms import SOCIAL_SITES
from agender.protocol.schemas import TaskType, parse_params
from agender.worker.execution import ExecutionStopped

SUPPORTED = {"douyin.search", "douyin.video_comments", "douyin.video_bundle", "douyin.creator_posts", "web.search", "web.fetch_article", "web.research"}
SUPPORTED.update(f"{site}.{action}" for site in SOCIAL_SITES for action in ("search", "comments", "bundle", "creator_posts"))
IGNORED_PARAMS = {"resume_from", "timeout_s", "capture_screenshot", "capture_html", "upload_artifacts"}


def fingerprint(task) -> str:
    params = parse_params(TaskType(task.type), task.params).model_dump(mode="json")
    params = {key: value for key, value in params.items() if key not in IGNORED_PARAMS}
    raw = json.dumps({"type": task.type, "params": params, "schema": task.schema_version}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


async def save(store, task, result, extractor_version: str, ttl_s: int) -> str | None:
    if task.type not in SUPPORTED or result.get("exhausted") or result.get("limit_reached"):
        return None
    if not any(result.get(key) for key in ("items", "comments", "serp", "documents")):
        return None
    digest = fingerprint(task)
    if task.type.startswith("web."):
        extractor_version = "web-1.1"
    checkpoint_id = "cp_" + hashlib.sha256(f"{task.task_id}:{task.attempt}:{digest}".encode()).hexdigest()
    payload = copy.deepcopy(result)
    for field in ("checkpoint_id", "error_code", "stop_reason"):
        payload.pop(field, None)
    await store.save_checkpoint(checkpoint_id=checkpoint_id, task_id=task.task_id, attempt=task.attempt,
                                task_type=task.type, fingerprint=digest, extractor_version=extractor_version,
                                result=payload, ttl_s=ttl_s)
    return checkpoint_id


async def restore(store, task, params, extractor_version: str) -> dict:
    if task.type not in SUPPORTED:
        raise ExecutionStopped("UNSUPPORTED_RESUME")
    if task.type.startswith("web."):
        extractor_version = "web-1.1"
    row = await store.checkpoint(params["resume_from"])
    if row is None:
        raise ExecutionStopped("CHECKPOINT_NOT_FOUND")
    if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
        raise ExecutionStopped("CHECKPOINT_EXPIRED")
    if row["task_id"] != task.task_id or task.attempt <= row["attempt"]:
        raise ExecutionStopped("CHECKPOINT_ATTEMPT_MISMATCH")
    if row["task_type"] != task.type or row["fingerprint"] != fingerprint(task) or row["extractor_version"] != extractor_version:
        raise ExecutionStopped("CHECKPOINT_MISMATCH")
    return json.loads(row["result_json"])
