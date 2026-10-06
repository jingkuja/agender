from agender.config import load_config


def test_cdp_default_and_environment_overrides(tmp_path, monkeypatch):
    from agender.config import AppConfig
    assert AppConfig().browser.mode == "cdp"
    config = tmp_path / "config.yaml"
    config.write_text("browser:\n  mode: playwright\n")
    monkeypatch.setenv("AGENDER_BROWSER_MODE", "cdp")
    monkeypatch.setenv("AGENDER_CDP_ENDPOINT_URL", "http://127.0.0.1:9333")
    monkeypatch.setenv("AGENDER_CDP_TIMEOUT_MS", "5000")
    monkeypatch.setenv("AGENDER_BROWSER_HEADLESS", "false")
    cfg = load_config(config)
    assert cfg.browser.mode == "cdp"
    assert cfg.browser.cdp_endpoint_url.endswith(":9333")
    assert cfg.browser.cdp_timeout_ms == 5000
    assert cfg.browser.headless is False


def test_invalid_browser_mode_and_platform_ack():
    import pytest

    from agender.config import AppConfig, BrowserConfig
    with pytest.raises(ValueError):
        BrowserConfig(mode="automatic")
    with pytest.raises(ValueError):
        BrowserConfig(cdp_endpoint_url="file:///tmp/socket")
    for site in ("xiaohongshu", "weibo", "bilibili"):
        with pytest.raises(ValueError, match="tos_ack"):
            AppConfig.model_validate({"tools": {site: True}})


def test_example_config_loads():
    cfg = load_config()
    assert cfg.web.default_search_engine == "auto"
    assert cfg.limits.douyin_comments_default == 20
    assert cfg.observability.metrics_addr.startswith("127.0.0.1")


def test_douyin_requires_tos(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("tools:\n  douyin: true\ndouyin:\n  tos_ack: false\n", encoding="utf-8")
    try:
        load_config(p)
        raise AssertionError("expected error")
    except Exception as exc:
        assert "tos_ack" in str(exc)
