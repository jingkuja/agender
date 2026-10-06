from __future__ import annotations

import time


class SiteHealth:
    def __init__(self, threshold: int = 5, cooldown_s: float = 1800, *, now=time.monotonic):
        self.threshold, self.cooldown_s, self.now = threshold, cooldown_s, now
        self.failures: dict[str, int] = {}
        self.until: dict[str, float] = {}

    def available(self, site: str) -> bool:
        return self.now() >= self.until.get(site, 0)

    def record(self, site: str, code: str | None) -> None:
        if code in {"CAPTCHA", "EMPTY_RENDER", "REGION_BLOCK", "RATE_LIMITED"}:
            n = self.failures.get(site, 0) + 1
            self.failures[site] = n
            if n >= self.threshold:
                self.until[site] = self.now() + self.cooldown_s
        elif code is None:
            self.failures.pop(site, None)
            self.until.pop(site, None)
