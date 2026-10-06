from __future__ import annotations

from agender.config import AppConfig
from agender.protocol.schemas import SessionState, TaskType, WorkerStatus

WEB_TYPES = [
    TaskType.WEB_SEARCH.value,
    TaskType.WEB_FETCH_ARTICLE.value,
    TaskType.WEB_RESEARCH.value,
]


def capabilities(cfg: AppConfig, *, browser_ready: bool, douyin_session: SessionState) -> tuple[list[str], list[str], WorkerStatus]:
    caps: list[str] = []
    disabled: list[str] = []
    status = WorkerStatus.IDLE
    if cfg.tools.web:
        caps.extend(WEB_TYPES)
        hot = any(h.enabled and h.url for h in cfg.web.hot_lists.values())
        if hot:
            caps.append(TaskType.WEB_HOT_LIST.value)
    if cfg.tools.douyin and cfg.douyin.tos_ack:
        caps.extend([TaskType.DOUYIN_SEARCH.value, TaskType.DOUYIN_VIDEO_STATS.value, TaskType.SESSION_LOGIN.value])
        if douyin_session == SessionState.OK:
            caps.extend([TaskType.DOUYIN_VIDEO_COMMENTS.value, TaskType.DOUYIN_VIDEO_BUNDLE.value])
        else:
            disabled.extend([TaskType.DOUYIN_VIDEO_COMMENTS.value, TaskType.DOUYIN_VIDEO_BUNDLE.value])
            status = WorkerStatus.DEGRADED
    if not browser_ready:
        status = WorkerStatus.DEGRADED
    return caps, disabled, status
