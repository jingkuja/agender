from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"(?:/video/|/share/video/)(\d{5,})")
NOTE_RE = re.compile(r"/note/")
COUNT_RE = re.compile(r"^([\d.]+)\s*([万亿w])?$", re.I)

ALLOW_DEFAULT = ("v.douyin.com", "www.douyin.com", "www.iesdouyin.com")


class UnsupportedUrl(ValueError):
    code = "UNSUPPORTED_URL"


def parse_count(text: str | None) -> int | None:
    if text is None:
        return None
    raw = str(text).strip().replace(",", "").replace("+", "")
    if not raw or raw in {"-", "--"}:
        return None
    m = re.search(r"(?<![\d.-])(\d+(?:\.\d+)?)\s*([万亿wk千m]?)", raw, re.I)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in {"万", "w"}:
        num *= 10_000
    elif unit == "亿":
        num *= 100_000_000
    elif unit in {"k", "千"}:
        num *= 1_000
    elif unit == "m":
        num *= 1_000_000
    return int(num)


def extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    m = VIDEO_ID_RE.search(parsed.path)
    if m:
        return m.group(1)
    q = parse_qs(parsed.query)
    for key in ("modal_id", "aweme_id", "item_id"):
        if q.get(key) and re.fullmatch(r"\d{5,}", q[key][0]):
            return q[key][0]
    return None


def host_allowed(host: str, allowlist: list[str]) -> bool:
    h = (host or "").lower()
    return h in {a.lower() for a in allowlist}


def canonicalize_video_url(url: str, allowlist: list[str] | None = None) -> tuple[str, str]:
    allow = allowlist or list(ALLOW_DEFAULT)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise UnsupportedUrl("invalid URL")
    if NOTE_RE.search(parsed.path or ""):
        raise UnsupportedUrl("note posts are not supported in v1")
    if parsed.hostname and not host_allowed(parsed.hostname, allow):
        raise UnsupportedUrl(f"host not allowlisted: {parsed.hostname}")
    vid = extract_video_id(url)
    if not vid:
        raise UnsupportedUrl("cannot parse video id")
    canonical = f"https://www.douyin.com/video/{vid}"
    return canonical, vid
