from typer.testing import CliRunner

from agender.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "worker" in result.stdout


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_catalog_without_browser_or_server():
    import json
    result = runner.invoke(app, ["catalog"])
    assert result.exit_code == 0
    catalog = json.loads(result.stdout)
    tasks = {entry["type"]: entry for entry in catalog["tasks"]}
    assert tasks["xiaohongshu.search"]["params_schema"]["required"] == ["query"]
    assert tasks["bilibili.comments"]["session_required_for_claim"]
