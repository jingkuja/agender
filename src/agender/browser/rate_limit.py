from __future__ import annotations

import asyncio
import time

from agender.obs.metrics import RATE_WAIT
from agender.worker.execution import ExecutionContext


class RateLimiter:
    """Spaces user-initiated navigations/load actions; not static subresources."""

    def __init__(self, rates: dict[str, int], *, now=time.monotonic):
        self.rates, self.now = rates, now
        self._next: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, host: str, execution: ExecutionContext | None = None) -> None:
        async with self._lock:
            rpm = self.rates.get(host, self.rates.get("default", 10))
            if rpm <= 0:
                raise ValueError("per_host_rpm must be positive")
            wait = max(0, self._next.get(host, 0) - self.now())
            started = self.now()
            if execution:
                await execution.sleep(wait)
                execution.check()
            else:
                await asyncio.sleep(wait)
            self._next[host] = self.now() + 60 / rpm
            RATE_WAIT.observe(max(0, self.now() - started))
