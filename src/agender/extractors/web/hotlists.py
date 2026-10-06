from __future__ import annotations

from bs4 import BeautifulSoup


def parse_baidu_hot(html: str, source_url: str, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []
    cards = soup.select(".category-wrap_iQLou a.title_dIF3B, a.title-content, .hot-index_1Bl1a, .list-item a")
    rank = 1
    seen: set[str] = set()
    for a in cards:
        title = a.get_text(" ", strip=True)
        href = str(a.get("href") or "")
        if not title or len(title) < 2:
            continue
        if href in seen or title in seen:
            continue
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http"):
            continue
        seen.add(title)
        seen.add(href)
        items.append({"rank": rank, "title": title, "url": href, "hot_score_raw": None})
        rank += 1
        if rank > limit:
            break
    return items


HOTLIST_PARSERS = {"baidu_hot": parse_baidu_hot}
