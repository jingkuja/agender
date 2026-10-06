from __future__ import annotations

from pathlib import Path

import pytest

from agender.config import load_config

FIXTURES = Path(__file__).parent / "fixtures" / "html"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    example = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"
    c = load_config(example)
    c.root = tmp_path
    c.browser.mode = "playwright"
    c.tools.web = True
    c.tools.douyin = True
    c.douyin.tos_ack = True
    return c


@pytest.fixture
def html():
    def _load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _load
