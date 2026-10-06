from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SerpItem:
    title: str
    url: str
    snippet: str = ""
    source_hint: str | None = None

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_hint": self.source_hint,
        }
