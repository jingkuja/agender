from agender.config import AppConfig
from agender.extractors.web.policy import check_url, clear_robots_cache, robots_allowed


def test_deny_localhost():
    cfg = AppConfig()
    d = check_url("http://127.0.0.1/", cfg, resolve=True)
    assert not d.ok
    assert d.code == "POLICY_DENIED"


def test_deny_govcn():
    cfg = AppConfig()
    d = check_url("https://www.gov.cn/", cfg, resolve=False)
    assert not d.ok


def test_allow_explicit_govcn():
    cfg = AppConfig()
    d = check_url("https://www.gov.cn/", cfg, allowlist=["www.gov.cn"], resolve=False)
    assert d.ok


def test_robots_bypass_still_detects(monkeypatch):
    clear_robots_cache()

    def fetch(_url: str) -> str:
        return "User-agent: *\nDisallow: /\n"

    allowed, would = robots_allowed("https://example.com/x", fetch=fetch, respect=True)
    assert allowed is False and would is True
    allowed, would = robots_allowed("https://example.com/x", fetch=fetch, respect=False)
    assert allowed is True and would is True
