from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any


def remaining_seconds(value: str) -> float:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return (dt - datetime.now(UTC)).total_seconds()


class ExecutionStopped(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ExecutionContext:
    """Task-local cancellation, deadlines and durable-progress boundaries.

    Snapshots contain only already-extracted data. They survive cancellation of
    the browser coroutine, so a timed-out bundle can still return its video.
    """

    def __init__(
        self, *, deadline: float | None = None, hard_deadline: float | None = None,
        on_progress: Callable[[str, dict[str, int]], Awaitable[None]] | None = None,
    ):
        self.deadline = deadline
        self.hard_deadline = hard_deadline
        self.lease_deadline: float | None = None
        self.reason: str | None = None
        self.changed = asyncio.Event()
        self.result: dict[str, Any] | None = None
        self.on_progress = on_progress
        self._progress_at = 0.0

    def cancel(self, reason: str) -> None:
        if self.reason is None:
            self.reason = reason
        self.changed.set()

    def renew(self, lease_until: str) -> None:
        self.lease_deadline = time.monotonic() + remaining_seconds(lease_until)
        self.changed.set()

    def check(self) -> None:
        if self.reason:
            raise ExecutionStopped(self.reason)
        now = time.monotonic()
        for end, code in (
            (self.lease_deadline, "LEASE_LOST"),
            (self.hard_deadline, "DEADLINE_HARD"),
            (self.deadline, "DEADLINE_SOFT"),
        ):
            if end is not None and now >= end:
                raise ExecutionStopped(code)

    def snapshot(self, result: dict[str, Any]) -> None:
        self.result = copy.deepcopy(result)

    async def progress(self, stage: str, counts: dict[str, int] | None = None, *, force: bool = False) -> None:
        self.check()
        now = time.monotonic()
        if self.on_progress and (force or now - self._progress_at >= 1):
            self._progress_at = now
            await self.on_progress(stage, counts or {})

    async def sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0, seconds)
        while True:
            self.changed.clear()
            self.check()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            limits = [remaining]
            for cutoff in (self.deadline, self.hard_deadline, self.lease_deadline):
                if cutoff is not None:
                    limits.append(max(0, cutoff - time.monotonic()))
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=min(limits))
            except TimeoutError:
                continue

    async def watch(self) -> str:
        try:
            while True:
                await self.sleep(3600)
        except ExecutionStopped as exc:
            return exc.code
