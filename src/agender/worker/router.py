from __future__ import annotations

from typing import Any

from agender.browser.login import run_session_login
from agender.browser.runtime import BrowserRuntime
from agender.config import AppConfig
from agender.extractors.douyin.normalize import UnsupportedUrl
from agender.extractors.douyin.tool import DouyinTool
from agender.extractors.web.tool import SoftDeadline, WebTool
from agender.protocol.schemas import (
    SessionLoginParams,
    SessionState,
    TaskType,
    parse_params,
)


class Router:
    def __init__(self, cfg: AppConfig, runtime: BrowserRuntime | None, fetch_web=None, fetch_douyin=None):
        self.cfg = cfg
        self.runtime = runtime
        self.fetch_web = fetch_web
        self.fetch_douyin = fetch_douyin

    async def _playwright_fetch(self, url: str, site: str) -> str:
        assert self.runtime is not None
        page = await self.runtime.page(site)  # type: ignore[arg-type]
        await page.goto(url, wait_until="domcontentloaded")
        await self.runtime.pause()
        return await page.content()

    async def execute(self, task_type: TaskType, params_raw: dict[str, Any], *, session: SessionState, deadline: float | None) -> dict[str, Any]:
        params = parse_params(task_type, params_raw)
        if task_type.value.startswith("web."):
            fetch = self.fetch_web or (lambda u: self._playwright_fetch(u, "web"))
            tool = WebTool(self.cfg, fetch)
            tool.deadline = deadline
            try:
                if task_type == TaskType.WEB_SEARCH:
                    return await tool.search(params)  # type: ignore[arg-type]
                if task_type == TaskType.WEB_FETCH_ARTICLE:
                    return await tool.fetch_articles(params)  # type: ignore[arg-type]
                if task_type == TaskType.WEB_RESEARCH:
                    return await tool.research(params)  # type: ignore[arg-type]
                if task_type == TaskType.WEB_HOT_LIST:
                    return await tool.hot_list(params)  # type: ignore[arg-type]
            except SoftDeadline:
                return {"kind": task_type.value, "error_code": "DEADLINE_SOFT"}
        if task_type.value.startswith("douyin.") or task_type == TaskType.SESSION_LOGIN:
            if not self.cfg.tools.douyin or not self.cfg.douyin.tos_ack:
                return {"kind": task_type.value, "error_code": "POLICY_DENIED"}
        if task_type == TaskType.SESSION_LOGIN:
            assert isinstance(params, SessionLoginParams)
            if self.runtime is None:
                return {"kind": "session.login", "error_code": "LOGIN_WALL", "site": "douyin"}
            return await run_session_login(self.runtime, params, timeout_s=params.timeout_s or 300)
        fetch_d = self.fetch_douyin or (lambda u: self._playwright_fetch(u, "douyin"))
        dtool = DouyinTool(self.cfg, fetch_d, session_state=session)
        try:
            if task_type == TaskType.DOUYIN_SEARCH:
                return await dtool.search(params)  # type: ignore[arg-type]
            if task_type == TaskType.DOUYIN_VIDEO_STATS:
                return await dtool.video_stats(params)  # type: ignore[arg-type]
            if task_type == TaskType.DOUYIN_VIDEO_COMMENTS:
                return await dtool.video_comments(params)  # type: ignore[arg-type]
            if task_type == TaskType.DOUYIN_VIDEO_BUNDLE:
                return await dtool.video_bundle(params)  # type: ignore[arg-type]
        except UnsupportedUrl as exc:
            return {"kind": task_type.value, "error_code": "UNSUPPORTED_URL", "message": str(exc)}
        return {"kind": task_type.value, "error_code": "UNSUPPORTED_TYPE"}
