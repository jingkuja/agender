from agender.config import load_config


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
