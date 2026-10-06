import json
import sqlite3

from typer.testing import CliRunner

from agender.cli import app
from agender.store.content import SCHEMA


def test_content_cli_exports_and_protects_database(tmp_path):
    db = tmp_path / 'content.db'
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA)
        conn.execute('PRAGMA user_version=1')
        conn.execute('INSERT INTO contents VALUES (?,?,?,?,?,?,?)',
                     ('web', 'x', 'article', 'https://example.org', '{"title":"Test"}', '2026-10-06', '2026-10-06'))
    config = tmp_path / 'settings.yaml'
    config.write_text(f'store:\n  content_path: {db}\n')
    runner = CliRunner()
    result = runner.invoke(app, ['--config', str(config), 'content', 'status'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['contents'] == 1
    out = tmp_path / 'contents.jsonl'
    result = runner.invoke(app, ['--config', str(config), 'content', 'export', '--output', str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())['data']['title'] == 'Test'
    result = runner.invoke(app, ['--config', str(config), 'content', 'export', '--output', str(db)])
    assert result.exit_code != 0
    assert 'must not overwrite a database' in result.output
