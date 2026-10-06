from __future__ import annotations

import re

from bs4 import BeautifulSoup


def sanitize_html(html: str) -> str:
    """Diagnostic DOM only; never persist scripts, form values or URL secrets."""
    soup = BeautifulSoup(html, "lxml")
    for node in soup.select("script, style, noscript, form, input, textarea, iframe"):
        node.decompose()
    for node in soup.find_all(True):
        for key in list(node.attrs):
            if key.startswith("on") or any(word in key.lower() for word in ("token", "password", "cookie", "session", "secret")):
                del node.attrs[key]
            elif key in {"href", "src", "action"}:
                node.attrs[key] = str(node.attrs[key]).split("?", 1)[0].split("#", 1)[0]
    result = str(soup)
    result = re.sub(r"(?i)(sessionid|sid_tt|uid_tt|ttwid|token|password)\s*[=:]\s*[^\s<;]+", r"\1=[redacted]", result)
    return result
