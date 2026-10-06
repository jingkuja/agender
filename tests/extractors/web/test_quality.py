import asyncio
from unittest.mock import AsyncMock

from agender.extractors.web.article import extract_article
from agender.extractors.web.policy import clear_robots_cache, robots_allowed_async
from agender.extractors.web.tool import WebTool
from agender.protocol.schemas import WebFetchParams, WebSearchParams
from agender.worker.execution import ExecutionContext


def test_blank_and_captcha_are_not_successful_articles():
    blank = extract_article('<html><body></body></html>', 'https://example.org', max_body_chars=1000, include_body=True)
    assert blank['fetch_status'] == 'failed'
    assert blank['error']['code'] == 'EMPTY_ARTICLE'
    captcha = extract_article('<div id="captcha">Please verify</div>', 'https://example.org', max_body_chars=1000, include_body=True)
    assert captcha['fetch_status'] == 'blocked'


async def test_slow_robots_does_not_block_event_loop():
    clear_robots_cache()
    entered = asyncio.Event()
    release = asyncio.Event()
    async def slow(url):
        entered.set()
        await release.wait()
        return 'User-agent: *\nDisallow: /private\n'
    work = asyncio.create_task(robots_allowed_async('https://example.org/private', fetch=slow, respect=True))
    await asyncio.wait_for(entered.wait(), 0.2)
    ticks = 0
    for _ in range(5):
        await asyncio.sleep(0)
        ticks += 1
    assert ticks == 5 and not work.done()
    release.set()
    assert await work == (False, True)


async def test_search_cross_page_dedup_and_unknown_markup(cfg, html):
    fetch = AsyncMock(return_value=html('bing_serp.html'))
    tool = WebTool(cfg, fetch)
    tool._gate = AsyncMock(return_value=(True, None, False))
    result = await tool.search(WebSearchParams(query='q', search_engine='bing_cn', max_pages=2))
    assert len({row['canonical_url'] for row in result['serp']}) == len(result['serp'])
    assert result['limit_reached'] and not result['exhausted']
    fetch.return_value = '<html><body><p>new structure</p></body></html>'
    result = await tool.search(WebSearchParams(query='q', search_engine='bing_cn'))
    assert result['error_code'] == 'SELECTOR_DRIFT'


async def test_known_and_restored_documents_are_not_fetched(cfg):
    execution = ExecutionContext()
    execution.resume = {'documents': [{'url': 'https://example.org/a', 'canonical_url': 'https://example.org/a', 'fetch_status': 'ok'}]}
    fetch = AsyncMock()
    tool = WebTool(cfg, fetch, execution=execution)
    result = await tool.fetch_articles(WebFetchParams(urls=['https://example.org/a', 'https://example.org/b?utm_source=x'], known_ids=['https://example.org/b']))
    fetch.assert_not_awaited()
    assert len(result['documents']) == 2
    assert result['documents'][1]['error']['code'] == 'KNOWN_ID'
    assert result['exhausted']
