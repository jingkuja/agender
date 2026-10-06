from __future__ import annotations

# Playwright default-flag cleanup, not stealth. Do not add fingerprint JS.

IGNORE_DEFAULT_ARGS = ["--enable-automation"]
EXTRA_ARGS = ["--disable-blink-features=AutomationControlled"]
ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
