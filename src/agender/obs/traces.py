from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass
class RunTrace:
    task_id: str
    task_type: str
    steps: list[str] = field(default_factory=list)
    t0: float = field(default_factory=monotonic)

    def step(self, name: str) -> None:
        self.steps.append(name)

    def elapsed_s(self) -> float:
        return monotonic() - self.t0
