from __future__ import annotations

from urllib.parse import urlparse

import trafilatura
from bs4 import BeautifulSoup
from readability import Document


def extract_article(html: str, url: str, *, max_body_chars: int, include_body: bool) -> dict:
    downloaded = html
    extracted = trafilatura.extract(
        downloaded,
        url=url,
        include_comments=False,
        include_tables=False,
        output_format="txt",
        with_metadata=True,
    )
    meta = trafilatura.extract_metadata(downloaded, default_url=url)
    title = (meta.title if meta else None) or ""
    byline = meta.author if meta else None
    published = meta.date if meta else None
    language = meta.language if meta else None
    source = meta.sitename if meta else None
    summary = None
    body = extracted or ""
    if not body:
        doc = Document(html)
        title = title or doc.short_title()
        soup = BeautifulSoup(doc.summary(), "lxml")
        body = soup.get_text("\n", strip=True)
    if not title:
        soup = BeautifulSoup(html, "lxml")
        if soup.title:
            title = soup.title.get_text(" ", strip=True)
    truncated = False
    if include_body and len(body) > max_body_chars:
        body = body[:max_body_chars]
        truncated = True
    if not include_body:
        summary = body[:280]
        body = ""
    soup = BeautifulSoup(html, "lxml")
    related: list[str] = []
    host = urlparse(url).netloc
    for a in soup.select("a[href]"):
        href = str(a.get("href") or "")
        if href.startswith("http") and urlparse(href).netloc == host and href not in related and href != url:
            related.append(href)
        if len(related) >= 10:
            break
    return {
        "title": title or url,
        "url": url,
        "canonical_url": url,
        "source": source,
        "source_domain": host,
        "published_at": published,
        "byline": byline,
        "summary": summary,
        "body_text": body if include_body else "",
        "body_chars": len(body) if include_body else 0,
        "truncated": truncated,
        "language": language or "zh",
        "related_urls": related,
        "fetch_status": "ok",
    }
