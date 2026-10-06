from __future__ import annotations

from bs4 import BeautifulSoup

from agender.extractors.web.dom import SerpItem


def parse_bing(html: str) -> list[SerpItem]:
    soup = BeautifulSoup(html, "lxml")
    items: list[SerpItem] = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        snippet_el = li.select_one("p") or li.select_one(".b_caption p")
        source = None
        cite = li.select_one("cite")
        if cite:
            source = cite.get_text(" ", strip=True)
        items.append(
            SerpItem(
                title=a.get_text(" ", strip=True),
                url=str(a.get("href")),
                snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "",
                source_hint=source,
            )
        )
    return items


def bing_blocked(html: str) -> bool:
    low = html.lower()
    return "captcha" in low and ("bing" in low or "sorry" in low) or "id=\"b_captcha\"" in low
