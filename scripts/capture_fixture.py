#!/usr/bin/env python3
"""Manual headed capture. Never run against live sites in CI."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup


def sanitize(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        if tag.get("id") == "RENDER_DATA":
            if tag.string:
                tag.string = re.sub(r"(sessionid|sid_tt|uid_tt)=[^&\"']+", r"\1=redacted", tag.string)
            continue
        tag.decompose()
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and "?" in str(src):
            img["src"] = str(src).split("?", 1)[0]
    text = str(soup)
    text = re.sub(r"(sessionid|sid_tt|uid_tt|ttwid)=[^;\"']+", r"\1=redacted", text)
    return text[:200_000]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--site", choices=["douyin", "bing", "baidu", "article", "baidu_hot"], required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    async def _go() -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False, channel="chrome")
            page = await browser.new_page()
            await page.goto(args.url, wait_until="domcontentloaded")
            input("Adjust the page, then press Enter to save a sanitized snapshot...")
            html = await page.content()
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(sanitize(html), encoding="utf-8")
            await browser.close()

    import asyncio

    asyncio.run(_go())
    print(f"wrote {args.out} for {args.site}")


if __name__ == "__main__":
    main()
