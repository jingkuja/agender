from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

from agender.browser.navigator import PageSnapshot
from agender.extractors.douyin.normalize import UnsupportedUrl
from agender.extractors.douyin.tool import eligible
from agender.extractors.social import adapters as a
from agender.protocol.schemas import SessionState

# Observe JSON already requested by the public page; do not forge signed APIs.
RESPONSE_PATHS = {
    "xiaohongshu": ("/api/sns/web/",),
    "weibo": ("/ajax/statuses/", "/ajax/comments/", "/ajax/profile/", "/api/container/", "/api/statuses/", "/comments/"),
    "bilibili": ("/x/web-interface/", "/x/v2/reply", "/x/space/"),
}
RESPONSE_DOMAINS = {"xiaohongshu": "xiaohongshu.com", "weibo": "weibo.com", "bilibili": "bilibili.com"}
SNAPSHOT_JS = """() => {
  const html = document.documentElement.cloneNode(true);
  const copyShadows = (from, to) => {
    if (from.shadowRoot) {
      const fragment = document.createElement('div');
      for (const child of from.shadowRoot.children) {
        const clone = child.cloneNode(true);
        copyShadows(child, clone); fragment.appendChild(clone);
      }
      to.appendChild(fragment);
    }
    const sources = [...from.children], targets = [...to.children];
    sources.forEach((child, i) => { if (targets[i]) copyShadows(child, targets[i]); });
  };
  copyShadows(document.documentElement, html);
  const states = [];
  for (const key of ['__INITIAL_STATE__', '__NEXT_DATA__', '$render_data']) {
    try { if (window[key]) states.push(JSON.parse(JSON.stringify(window[key]))); } catch {}
  }
  return {html: html.outerHTML, states};
}"""


class SocialTool:
    def __init__(self, cfg, site, fetch, *, navigator=None, execution=None, session_state=SessionState.UNKNOWN):
        self.cfg, self.site, self.fetch = cfg, site, fetch
        self.settings = getattr(cfg, site)
        self.navigator, self.execution, self.session_state = navigator, execution, session_state
        self.responses: list[dict | list] = []
        self.response_tasks: set[asyncio.Task] = set()
        self.response_bytes = 0
        self.listening_page = None
        self.expanded: set[str] = set()

    @property
    def page(self):
        return self.navigator.runtime.active_page

    async def execute(self, action: str, params) -> dict:
        try:
            return await getattr(self, action)(params)
        except UnsupportedUrl:
            return {"kind": f"{self.site}.{action}", "error_code": "UNSUPPORTED_URL"}
        finally:
            if self.listening_page is not None:
                self.listening_page.remove_listener("response", self._on_response)
            pending = list(self.response_tasks)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    def _on_response(self, response) -> None:
        if len(self.response_tasks) >= 20 or self.response_bytes >= 8 * 1024 * 1024:
            return
        parsed = urlsplit(response.url)
        domain = RESPONSE_DOMAINS[self.site]
        hosts = {domain, "m.weibo.cn"} if self.site == "weibo" else {domain}
        if not any(parsed.hostname == h or (parsed.hostname or "").endswith("." + h) for h in hosts):
            return
        if not any(parsed.path.startswith(path) for path in RESPONSE_PATHS[self.site]):
            return
        if "json" not in response.headers.get("content-type", ""):
            return
        task = asyncio.create_task(self._read_response(response))
        self.response_tasks.add(task)
        task.add_done_callback(self.response_tasks.discard)

    async def _read_response(self, response) -> None:
        try:
            declared = int(response.headers.get("content-length", "0"))
            if declared > 2 * 1024 * 1024:
                return
            async with asyncio.timeout(3):
                body = await response.body()
            if len(body) > 2 * 1024 * 1024 or self.response_bytes + len(body) > 8 * 1024 * 1024:
                return
            payload = json.loads(body)
            if isinstance(payload, (list, dict)):
                self.responses.append(payload)
                self.response_bytes += len(body)
        except Exception:
            # A cancelled/failed optional observation cannot fail DOM extraction.
            return

    async def snapshot_html(self) -> str:
        if self.response_tasks:
            await asyncio.wait(list(self.response_tasks), timeout=0.2)
        snapshot = await self.page.evaluate(SNAPSHOT_JS)
        html = snapshot["html"]
        for payload in [*snapshot["states"], *self.responses]:
            source = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
            if len(source) <= 2 * 1024 * 1024:
                html += f'<script type="application/json">{source}</script>'
        return html

    async def open(self, url: str) -> PageSnapshot:
        if self.execution:
            self.execution.check()
        if not self.navigator:
            result = await self.fetch(url)
            return result if isinstance(result, PageSnapshot) else PageSnapshot(result, url, url)
        await self.navigator.runtime.page(self.site)
        if self.listening_page is None:
            self.listening_page = self.page
            self.page.on("response", self._on_response)
        self.responses.clear()
        self.response_bytes = 0
        snapshot = await self.navigator.open(url, self.site)
        await self.sleep()
        snapshot.html = await self.snapshot_html()
        return snapshot

    async def sleep(self):
        if self.execution:
            await self.execution.sleep(self.settings.load_wait_s)
        else:
            await asyncio.sleep(self.settings.load_wait_s)

    async def publish(self, result, stage):
        if self.execution:
            self.execution.snapshot(result)
            await self.execution.progress(stage, {"returned": result.get("returned", 0)})

    async def advance(self, comments=False, params=None):
        await self.navigator.load_action()
        # Search pagination on Weibo/Bilibili is explicit; XHS is infinite scroll.
        if not comments and self.site in {"weibo", "bilibili"}:
            next_button = self.page.locator("a.next, .s-pagination-item:has-text('下一页'), button:has-text('下一页')").first
            if await next_button.count() and await next_button.is_visible() and await next_button.is_enabled():
                await next_button.click(timeout=3000)
                await self.sleep()
                return await self.snapshot_html()
        panel = self.page.locator(".note-scroller, .comments-container, .comment-list").first if comments else None
        if panel is not None and await panel.count():
            await panel.hover()
        await self.page.mouse.wheel(0, 900)
        await self.sleep()
        return await self.snapshot_html()

    async def expand_replies(self, params) -> bool:
        if not params.include_replies or not params.max_replies_per_comment:
            return False
        buttons = self.page.locator(".show-more, .view-more, .expand-replies, .reply-more, button:has-text('更多回复'), button:has-text('展开')")
        changed = False
        # Snapshot handles so removing a button does not skip the next root.
        for button in (await buttons.all())[:params.max_comments]:
            try:
                key = await button.evaluate("el => { const root = el.closest('[data-comment-id], [data-id], [data-rpid]'); return (root?.getAttribute('data-comment-id') || root?.getAttribute('data-id') || root?.getAttribute('data-rpid') || '') + ':' + el.outerHTML; }")
                if key in self.expanded or not await button.is_visible():
                    continue
                self.expanded.add(key)
                await self.navigator.load_action()
                await button.click(timeout=2000)
                changed = True
            except Exception:
                if self.execution:
                    self.execution.check()
        if changed:
            await self.sleep()
        return changed

    async def collection(self, html, params, result, *, comments=False, limit):
        key, id_key = ("comments", "comment_id") if comments else ("items", "content_id")
        resume = getattr(self.execution, "resume", None) or {}
        rows = {row[id_key]: row for row in resume.get(key, [])}
        stalled = 0
        result.update(returned=len(rows), exhausted=False, has_more=None, limit_reached=False, **{key: list(rows.values())})
        for _ in range(self.settings.max_scroll_rounds):
            if self.execution:
                self.execution.check()
            code = a.block_reason(html)
            if code:
                result.update(error_code=code, stop_reason=code.lower())
                break
            if comments and self.navigator and await self.expand_replies(params):
                html = await self.snapshot_html()
            previous = json.dumps(rows, sort_keys=True, ensure_ascii=False)
            parsed = a.parse_comments(self.site, html, params) if comments else a.parse_items(self.site, html)
            for row in parsed:
                if not eligible(row, params, id_key=id_key, time_key="created_at" if comments else "published_at"):
                    continue
                cid = row[id_key]
                if cid in rows and comments:
                    replies = {r["comment_id"]: r for r in rows[cid].get("replies", [])}
                    replies.update({r["comment_id"]: r for r in row.get("replies", [])})
                    row["replies"] = list(replies.values())[:params.max_replies_per_comment] if params.include_replies else []
                if cid in rows or len(rows) < limit:
                    rows[cid] = row
            result.update(**{key: list(rows.values())}, returned=len(rows))
            result["limit_reached"] = len(rows) >= limit
            result["exhausted"] = (a.ended(html, comments) or a.data_ended(html, comments)) and not result["limit_reached"]
            result["has_more"] = False if result["exhausted"] else None
            await self.publish(result, "comments" if comments else "search")
            if result["limit_reached"] or result["exhausted"]:
                result["stop_reason"] = "limit" if result["limit_reached"] else "end"
                break
            if not self.navigator:
                result.update(error_code="LOAD_STALL" if rows else "SELECTOR_DRIFT", stop_reason="snapshot")
                break
            stalled = stalled + 1 if previous == json.dumps(rows, sort_keys=True, ensure_ascii=False) else 0
            if stalled >= self.settings.stall_rounds:
                result.update(error_code="COMMENT_LOAD_STALL" if comments else "LOAD_STALL", stop_reason="stall")
                break
            html = await self.advance(comments, params)
        else:
            result.update(error_code="COMMENT_LOAD_STALL" if comments else "LOAD_STALL", stop_reason="round_limit")
        await self.publish(result, "collection_complete")
        return result

    async def search(self, params):
        result = {"kind": f"{self.site}.search", "query": params.query, "items": []}
        if params.sort == "most_liked" and self.site != "xiaohongshu":
            return {**result, "error_code": "UNSUPPORTED_FILTER"}
        snapshot = await self.open(a.search_url(self.site, params.query, params.sort))
        if self.site == "xiaohongshu" and params.sort != "general":
            if not self.navigator:
                return {**result, "error_code": "UNSUPPORTED_FILTER"}
            try:
                await self.navigator.load_action()
                await self.page.get_by_text("综合", exact=True).first.click(timeout=3000)
                await self.page.get_by_text("最新" if params.sort == "latest" else "最多点赞", exact=True).first.click(timeout=3000)
                self.responses.clear()
                await self.sleep()
                snapshot.html = await self.snapshot_html()
            except Exception:
                if self.execution:
                    self.execution.check()
                return {**result, "error_code": "UNSUPPORTED_FILTER"}
        return await self.collection(snapshot.html, params, result, limit=params.max_results)

    async def load_content(self, params):
        url = params.url or a.content_url(self.site, params.content_id)
        url, cid = a.normalize_url(self.site, url)
        if params.content_id and cid and cid != params.content_id and not (self.site == "weibo" and cid == a.weibo_id(params.content_id)):
            raise UnsupportedUrl("content_id conflicts with URL")
        snapshot = await self.open(url)
        code = a.block_reason(snapshot.html)
        if code:
            return snapshot, None, code
        final_url, final_id = a.normalize_url(self.site, snapshot.final_url, allow_short=False)
        if cid and final_id != cid or params.content_id and cid is None and final_id != params.content_id:
            raise UnsupportedUrl("unexpected content redirect")
        content = a.parse_detail(self.site, snapshot.html, final_url, final_id)
        return snapshot, content, None if content.get("title") or content.get("text") else "SELECTOR_DRIFT"

    async def detail(self, params):
        _, content, code = await self.load_content(params)
        result = {"kind": f"{self.site}.detail", "content": content}
        if code:
            result["error_code"] = code
        else:
            await self.publish(result, "detail")
        return result

    async def comments(self, params):
        snapshot, content, code = await self.load_content(params)
        result = {"kind": f"{self.site}.comments", "content": content, "comments": [], "returned": 0}
        if code:
            return {**result, "error_code": code}
        await self.publish(result, "detail")
        return await self.collection(snapshot.html, params, result, comments=True, limit=params.max_comments)

    async def bundle(self, params):
        result = await self.comments(params)
        result["kind"] = f"{self.site}.bundle"
        return result

    async def load_creator(self, params):
        url, cid = a.normalize_url(self.site, params.url or a.creator_url(self.site, params.creator_id), creator=True, allow_short=False)
        if params.creator_id and params.creator_id != cid:
            raise UnsupportedUrl("creator_id conflicts with URL")
        snapshot = await self.open(url)
        if a.block_reason(snapshot.html):
            return snapshot, {"creator_id": cid, "url": url}
        final_url, final_id = a.normalize_url(self.site, snapshot.final_url, creator=True, allow_short=False)
        if final_id != cid:
            raise UnsupportedUrl("unexpected creator redirect")
        return snapshot, a.parse_creator(self.site, snapshot.html, final_url, cid)

    async def creator_profile(self, params):
        snapshot, creator = await self.load_creator(params)
        result = {"kind": f"{self.site}.creator_profile", "creator": creator}
        code = a.block_reason(snapshot.html) or (None if creator.get("nickname") else "SELECTOR_DRIFT")
        if code:
            result["error_code"] = code
        return result

    async def creator_posts(self, params):
        snapshot, creator = await self.load_creator(params)
        if self.site == "bilibili" and not a.block_reason(snapshot.html) and self.navigator:
            snapshot = await self.open(creator["url"] + "/upload/video")
        result = {"kind": f"{self.site}.creator_posts", "creator": creator, "items": []}
        return await self.collection(snapshot.html, params, result, limit=params.max_posts)
