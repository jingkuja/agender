from __future__ import annotations

from typing import Any

from agender.browser.login import run_session_login
from agender.browser.runtime import BrowserRuntime
from agender.config import AppConfig
from agender.extractors.douyin.normalize import UnsupportedUrl
from agender.extractors.douyin.tool import DouyinTool
from agender.extractors.web.tool import WebTool
from agender.platforms import SOCIAL_SITES, task_site
from agender.protocol.schemas import SessionState, TaskType, parse_params


class Router:
    def __init__(self, cfg: AppConfig, runtime: BrowserRuntime | None, fetch_web=None, fetch_douyin=None, fetch_social=None):
        self.cfg, self.runtime = cfg, runtime
        self.fetch_web, self.fetch_douyin = fetch_web, fetch_douyin
        self.fetch_social = fetch_social or {}
        self.execution = None

    async def _playwright_fetch(self, url: str, site: str):
        if self.runtime is None:
            raise RuntimeError("browser unavailable")
        selector = "li.b_algo, .result, .c-container, .no-results, [id*='captcha']" if ("bing.com/search" in url or "baidu.com/s?" in url) else None
        return await self.runtime.navigator.open(url, site, ready_selector=selector)

    async def execute(self, task_type: TaskType, params_raw: dict[str, Any], *, session: SessionState, deadline: float | None) -> dict[str, Any]:
        params = parse_params(task_type, params_raw)
        incremental = {TaskType.DOUYIN_SEARCH, TaskType.DOUYIN_VIDEO_COMMENTS, TaskType.DOUYIN_VIDEO_BUNDLE,
                       TaskType.DOUYIN_CREATOR_POSTS, TaskType.WEB_SEARCH, TaskType.WEB_FETCH_ARTICLE, TaskType.WEB_RESEARCH}
        incremental.update(TaskType(f"{site}.{action}") for site in SOCIAL_SITES for action in ("search", "comments", "bundle", "creator_posts"))
        if (params.since or params.known_ids) and task_type not in incremental:
            return {"kind": task_type.value, "error_code": "UNSUPPORTED_FILTER"}
        if task_type == TaskType.SESSION_LOGIN:
            params_raw.pop("password", None)
        web = task_type.value.startswith("web.")
        site = task_site(task_type.value, params_raw)
        if not getattr(self.cfg.tools, site) or (not web and not getattr(self.cfg, site).tos_ack):
            return {"kind": task_type.value, "error_code": "POLICY_DENIED"}
        if self.runtime:
            self.runtime.navigator.configure(site, execution=self.execution,
                                              allowlist=getattr(params, "allowlist", []), denylist=getattr(params, "denylist", []))
        if web:
            fetch = self.fetch_web or (lambda url: self._playwright_fetch(url, "web"))
            tool = WebTool(self.cfg, fetch, execution=self.execution, robots_fetch=self.runtime.navigator.robots if self.runtime and not self.fetch_web else None)
            tool.deadline = deadline
            dispatch = {
                TaskType.WEB_SEARCH: tool.search,
                TaskType.WEB_FETCH_ARTICLE: tool.fetch_articles,
                TaskType.WEB_RESEARCH: tool.research,
                TaskType.WEB_HOT_LIST: tool.hot_list,
            }
            return await dispatch[task_type](params)
        if task_type == TaskType.SESSION_LOGIN:
            if self.runtime is None:
                return {"kind": "session.login", "error_code": "LOGIN_WALL", "site": site}
            return await run_session_login(self.runtime, params, timeout_s=params.timeout_s or 300)
        if site in SOCIAL_SITES:
            from agender.extractors.social.tool import SocialTool
            action = task_type.value.split(".", 1)[1]
            if action.startswith("creator_") and not getattr(self.cfg, site).creator_enabled:
                return {"kind": task_type.value, "error_code": "UNSUPPORTED_TYPE"}
            fetch = self.fetch_social.get(site)
            navigator = self.runtime.navigator if self.runtime and not fetch else None
            tool = SocialTool(self.cfg, site, fetch or (lambda url: self._playwright_fetch(url, site)),
                              navigator=navigator, execution=self.execution, session_state=session)
            return await tool.execute(action, params)
        fetch = self.fetch_douyin or (lambda url: self._playwright_fetch(url, "douyin"))
        tool = DouyinTool(self.cfg, fetch, session_state=session, execution=self.execution,
                          navigator=self.runtime.navigator if self.runtime and not self.fetch_douyin else None)
        dispatch = {
            TaskType.DOUYIN_SEARCH: tool.search,
            TaskType.DOUYIN_VIDEO_STATS: tool.video_stats,
            TaskType.DOUYIN_VIDEO_COMMENTS: tool.video_comments,
            TaskType.DOUYIN_VIDEO_BUNDLE: tool.video_bundle,
        }
        try:
            if task_type in {TaskType.DOUYIN_CREATOR_PROFILE, TaskType.DOUYIN_CREATOR_POSTS}:
                if not self.cfg.douyin.creator_enabled:
                    return {"kind": task_type.value, "error_code": "UNSUPPORTED_TYPE"}
                from agender.extractors.douyin.creator import CreatorTool
                creator = CreatorTool(tool)
                return await (creator.profile(params) if task_type == TaskType.DOUYIN_CREATOR_PROFILE else creator.posts(params))
            return await dispatch[task_type](params)
        except UnsupportedUrl:
            return {"kind": task_type.value, "error_code": "UNSUPPORTED_URL"}
