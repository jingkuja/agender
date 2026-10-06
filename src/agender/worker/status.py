from __future__ import annotations

from typing import Any

from agender.protocol.schemas import ResultStatus, TaskType


def evaluate_status(task_type: TaskType, result: dict[str, Any] | None, error_code: str | None) -> ResultStatus:
    if error_code in {"CAPTCHA", "LOGIN_WALL", "SESSION_EXPIRED"}:
        return ResultStatus.NEEDS_HUMAN
    if error_code in {"EMPTY_RENDER", "REGION_BLOCK", "RATE_LIMITED"}:
        return ResultStatus.BLOCKED
    if error_code == "WORKER_SHUTDOWN":
        return ResultStatus.PARTIAL if _has_records(task_type, result) else ResultStatus.CANCELLED
    if error_code in {"DEADLINE_SOFT", "DEADLINE_HARD", "LEASE_LOST", "SELECTOR_DRIFT", "COMMENT_LOAD_STALL"}:
        return ResultStatus.PARTIAL if _has_records(task_type, result) else ResultStatus.FAILED
    if error_code:
        return ResultStatus.FAILED
    if result is None:
        return ResultStatus.FAILED
    if task_type == TaskType.DOUYIN_SEARCH:
        return ResultStatus.SUCCEEDED
    if task_type == TaskType.DOUYIN_VIDEO_STATS:
        video = result
        if video.get("video_id") and video.get("title") and (video.get("author") or {}).get("nickname"):
            return ResultStatus.SUCCEEDED
        if video.get("video_id"):
            return ResultStatus.PARTIAL
        return ResultStatus.FAILED
    if task_type in {TaskType.DOUYIN_VIDEO_COMMENTS, TaskType.DOUYIN_VIDEO_BUNDLE}:
        return ResultStatus.SUCCEEDED
    if task_type == TaskType.WEB_SEARCH:
        return ResultStatus.SUCCEEDED
    if task_type == TaskType.WEB_FETCH_ARTICLE:
        docs = result.get("documents") or []
        oks = [d for d in docs if d.get("fetch_status") == "ok"]
        fails = [d for d in docs if d.get("fetch_status") in {"failed", "blocked"}]
        if oks and not fails:
            return ResultStatus.SUCCEEDED
        if oks and fails:
            return ResultStatus.PARTIAL
        if docs and all(d.get("fetch_status") == "skipped" for d in docs):
            return ResultStatus.SUCCEEDED
        return ResultStatus.FAILED
    if task_type == TaskType.WEB_HOT_LIST:
        sources = result.get("sources") or []
        oks = [s for s in sources if s.get("status") == "ok"]
        fails = [s for s in sources if s.get("status") in {"failed", "blocked"}]
        if oks and not fails:
            return ResultStatus.SUCCEEDED
        if oks:
            return ResultStatus.PARTIAL
        return ResultStatus.FAILED
    if task_type == TaskType.WEB_RESEARCH:
        docs = result.get("documents") or []
        fails = [d for d in docs if d.get("fetch_status") in {"failed", "blocked"}]
        skipped_deadline = any((d.get("error") or {}).get("code") == "DEADLINE_SOFT" for d in docs)
        if skipped_deadline or fails:
            if any(d.get("fetch_status") == "ok" for d in docs) or result.get("serp"):
                return ResultStatus.PARTIAL
            return ResultStatus.FAILED
        return ResultStatus.SUCCEEDED
    if task_type == TaskType.SESSION_LOGIN:
        sessions = result.get("sessions") or {}
        if sessions.get("douyin") == "ok" and not error_code:
            return ResultStatus.SUCCEEDED
        return ResultStatus.NEEDS_HUMAN
    return ResultStatus.SUCCEEDED


def _has_records(task_type: TaskType, result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    if result.get("items") or result.get("serp") or result.get("comments"):
        return True
    if result.get("documents"):
        return any(d.get("fetch_status") == "ok" for d in result["documents"])
    if result.get("video_id") and result.get("title"):
        return True
    if (result.get("video") or {}).get("video_id"):
        return True
    if result.get("sources") and any((s.get("items") or []) for s in result["sources"]):
        return True
    return False
