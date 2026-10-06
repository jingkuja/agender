from __future__ import annotations

from bs4 import BeautifulSoup

from agender.extractors.web.dom import SerpItem


def parse_baidu(html: str) -> list[SerpItem]:
    soup = BeautifulSoup(html, "lxml")
    items: list[SerpItem] = []
    for block in soup.select(".result, .c-container, .result-op"):
        a = block.select_one("h3 a") or block.select_one("a[data-click]")
        if not a or not a.get("href"):
            continue
        snippet_el = block.select_one(".c-abstract") or block.select_one(".content-right_8Zs40") or block.select_one("span")
        items.append(
            SerpItem(
                title=a.get_text(" ", strip=True),
                url=str(a.get("href")),
                snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "",
                source_hint=None,
            )
        )
    return items


def baidu_blocked(html: str) -> bool:
    low = html.lower()
    return "wappass" in low or "verify" in low and "baidu" in low or "id=\"captcha\"" in low
