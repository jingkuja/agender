import pytest

from agender.extractors.web.search_bing import bing_blocked, parse_bing
from agender.extractors.web.tool import WebTool
from agender.protocol.schemas import WebHotListParams, WebResearchParams, WebSearchParams


def test_parse_bing(html):
    items = parse_bing(html("bing_serp.html"))
    assert items[0].url.endswith("solid-state")
    assert "固态" in items[0].title


def test_bing_captcha(html):
    assert bing_blocked(html("bing_captcha.html"))


@pytest.mark.asyncio
async def test_auto_fallback_to_baidu(cfg, html, monkeypatch):
    monkeypatch.setattr("agender.extractors.web.policy.resolve_host", lambda h: ["203.0.113.10"])

    async def fetch(url: str) -> str:
        if "bing" in url:
            return html("bing_captcha.html")
        if "baidu.com/s" in url:
            return html("baidu_serp.html")
        return html("article.html")

    tool = WebTool(cfg, fetch, now_mono=lambda: 0)
    tool._robots_fetch = lambda u: "User-agent: *\nAllow: /\n"  # type: ignore[method-assign]
    out = await tool.search(WebSearchParams(query="固态电池", max_pages=1))
    assert out["engine_used"] == "baidu"
    assert out["engine_fallback"] is True
    assert out["returned"] == 2


@pytest.mark.asyncio
async def test_research(cfg, html, monkeypatch):
    monkeypatch.setattr("agender.extractors.web.policy.resolve_host", lambda h: ["203.0.113.10"])

    async def fetch(url: str) -> str:
        if "bing" in url:
            return html("bing_serp.html")
        return html("article.html")

    tool = WebTool(cfg, fetch, now_mono=lambda: 0)
    tool._robots_fetch = lambda u: "User-agent: *\nAllow: /\n"  # type: ignore[method-assign]
    out = await tool.research(WebResearchParams(query="固态电池", max_articles=1))
    assert out["kind"] == "web.research"
    assert out["documents"][0]["fetch_status"] == "ok"
    assert "固态" in (out["documents"][0]["body_text"] or out["documents"][0]["title"])


@pytest.mark.asyncio
async def test_hot_list(cfg, html, monkeypatch):
    monkeypatch.setattr("agender.extractors.web.policy.resolve_host", lambda h: ["203.0.113.10"])

    async def fetch(url: str) -> str:
        return html("baidu_hot.html")

    tool = WebTool(cfg, fetch, now_mono=lambda: 0)
    tool._robots_fetch = lambda u: "User-agent: *\nAllow: /\n"  # type: ignore[method-assign]
    out = await tool.hot_list(WebHotListParams(sources=["baidu_hot", "zhihu_hot"]))
    assert out["sources"][0]["status"] == "ok"
    assert out["sources"][0]["items"]
    assert out["sources"][1]["status"] == "skipped"
