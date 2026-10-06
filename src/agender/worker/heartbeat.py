from __future__ import annotations

from agender.config import AppConfig
from agender.extractors.web.hotlists import HOTLIST_PARSERS
from agender.platforms import SOCIAL_ACTIONS, SOCIAL_SITES
from agender.protocol.schemas import SessionState, TaskType, WorkerStatus

WEB_TYPES = [
    TaskType.WEB_SEARCH.value,
    TaskType.WEB_FETCH_ARTICLE.value,
    TaskType.WEB_RESEARCH.value,
]


def capabilities(cfg: AppConfig, *, browser_ready: bool, douyin_session: SessionState,
                 sessions: dict[str, SessionState] | None = None) -> tuple[list[str], list[str], WorkerStatus]:
    caps: list[str] = []
    disabled: list[str] = []
    status = WorkerStatus.IDLE
    if cfg.tools.web:
        caps.extend(WEB_TYPES)
        hot = any(h.enabled and h.url and key in HOTLIST_PARSERS for key, h in cfg.web.hot_lists.items())
        if hot:
            caps.append(TaskType.WEB_HOT_LIST.value)
    if cfg.tools.douyin and cfg.douyin.tos_ack:
        caps.extend([TaskType.DOUYIN_SEARCH.value, TaskType.DOUYIN_VIDEO_STATS.value, TaskType.SESSION_LOGIN.value])
        if cfg.douyin.creator_enabled:
            caps.extend([TaskType.DOUYIN_CREATOR_PROFILE.value, TaskType.DOUYIN_CREATOR_POSTS.value])
        if douyin_session == SessionState.OK:
            caps.extend([TaskType.DOUYIN_VIDEO_COMMENTS.value, TaskType.DOUYIN_VIDEO_BUNDLE.value])
        else:
            disabled.extend([TaskType.DOUYIN_VIDEO_COMMENTS.value, TaskType.DOUYIN_VIDEO_BUNDLE.value])
            status = WorkerStatus.DEGRADED
    for site in SOCIAL_SITES:
        settings = getattr(cfg, site)
        if not getattr(cfg.tools, site) or not settings.tos_ack:
            continue
        if TaskType.SESSION_LOGIN.value not in caps:
            caps.append(TaskType.SESSION_LOGIN.value)
        for action in SOCIAL_ACTIONS:
            if action.startswith("creator_") and not settings.creator_enabled:
                continue
            capability = f"{site}.{action}"
            if action in {"comments", "bundle"} and (sessions or {}).get(site) != SessionState.OK:
                disabled.append(capability)
                status = WorkerStatus.DEGRADED
            else:
                caps.append(capability)
    if not browser_ready:
        disabled.extend(caps)
        caps = []
        status = WorkerStatus.DEGRADED
    return caps, disabled, status
