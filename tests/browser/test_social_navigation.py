import json
from types import SimpleNamespace

from agender.browser.navigator import PageSnapshot
from agender.extractors.social.tool import SocialTool
from agender.protocol.schemas import SocialContentParams


async def test_bili_dynamic_shadow_replies_expand_at_root_limit(browser_runtime, local_site):
    base, pages, _ = local_site
    pages["/social"] = '''<html><body>
    <h1 class="video-title">Shadow video</h1>
    <bili-comment-thread-renderer data-id="root"></bili-comment-thread-renderer>
    <script>
    window.__INITIAL_STATE__ = { videoData: {
      bvid: 'BV1xx411c7mD', title: 'Shadow video', owner: { mid: '123456', name: 'Creator' }, stat: { reply: 1 }
    }, missing: undefined };
    const root = document.querySelector('bili-comment-thread-renderer').attachShadow({mode:'open'});
    root.innerHTML = '<div id="content">Root comment</div><button class="expand-replies">展开回复</button>';
    root.querySelector('button').onclick = function() {
      const reply = document.createElement('bili-comment-reply-renderer');
      reply.dataset.id = 'reply';
      reply.attachShadow({mode:'open'}).innerHTML = '<div id="content">Nested reply</div>';
      root.appendChild(reply); this.remove();
    };
    </script></body></html>'''
    browser_runtime.cfg.bilibili.load_wait_s = 0.05
    async def page(site):
        return await browser_runtime.page("web")
    async def open_local(url, site):
        snap = await browser_runtime.navigator.open(base + "/social", "web")
        return PageSnapshot(snap.html, url, url)
    driver = SimpleNamespace(runtime=SimpleNamespace(page=page, active_page=browser_runtime.active_page),
                             open=open_local, load_action=browser_runtime.navigator.load_action)
    tool = SocialTool(browser_runtime.cfg, "bilibili", None, navigator=driver)
    result = await tool.execute("bundle", SocialContentParams(content_id="BV1xx411c7mD", max_comments=1, include_replies=True))
    assert "error_code" not in result
    assert result["content"]["title"] == "Shadow video"
    assert result["comments"][0]["text"] == "Root comment"
    assert result["comments"][0]["replies"][0]["text"] == "Nested reply"
    assert result["limit_reached"] and not result["exhausted"]


async def test_bili_json_loaded_by_page_is_observed(browser_runtime, local_site):
    base, pages, _ = local_site
    pages["/json-social"] = '''<html><body><script>
      fetch('https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD');
    </script></body></html>'''
    payload = {"data": {"bvid": "BV1xx411c7mD", "title": "Observed video", "stat": {"like": 42}}}
    async def respond(route):
        await route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"}, body=json.dumps(payload))
    # Entirely in-memory response: this test never contacts bilibili.com.
    await browser_runtime.active_page.route("https://api.bilibili.com/**", respond)
    browser_runtime.cfg.bilibili.load_wait_s = 0.1
    async def page(site):
        return await browser_runtime.page("web")
    async def open_local(url, site):
        snap = await browser_runtime.navigator.open(base + "/json-social", "web")
        return PageSnapshot(snap.html, url, url)
    driver = SimpleNamespace(runtime=SimpleNamespace(page=page, active_page=browser_runtime.active_page),
                             open=open_local, load_action=browser_runtime.navigator.load_action)
    tool = SocialTool(browser_runtime.cfg, "bilibili", None, navigator=driver)
    result = await tool.execute("detail", SocialContentParams(content_id="BV1xx411c7mD"))
    assert result["content"]["title"] == "Observed video"
    assert result["content"]["stats"]["like_count"] == 42
