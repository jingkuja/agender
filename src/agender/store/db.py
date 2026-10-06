from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from agender.protocol.redact import strip_secrets

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA user_version=1;
CREATE TABLE IF NOT EXISTS task_runs (
  id            TEXT PRIMARY KEY,
  task_id       TEXT NOT NULL,
  task_type     TEXT NOT NULL,
  attempt       INTEGER NOT NULL DEFAULT 1,
  status        TEXT NOT NULL,
  lease_id      TEXT,
  lease_until   TEXT,
  payload_json  TEXT NOT NULL,
  result_json   TEXT,
  error_code    TEXT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  duration_ms   INTEGER,
  submitted_at  TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE(task_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_task_runs_created ON task_runs(created_at);
CREATE TABLE IF NOT EXISTS artifacts (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES task_runs(id),
  kind          TEXT NOT NULL,
  path          TEXT NOT NULL,
  sha256        TEXT,
  bytes         INTEGER,
  content_type  TEXT,
  uploaded      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    TEXT,
  ts        TEXT NOT NULL,
  level     TEXT NOT NULL,
  event     TEXT NOT NULL,
  data_json TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def insert_run(
        self,
        *,
        task_id: str,
        task_type: str,
        attempt: int,
        lease_id: str | None,
        lease_until: str | None,
        payload: dict[str, Any],
    ) -> str:
        run_id = str(uuid.uuid4())
        now = _now()
        safe = strip_secrets(payload)
        await self.db.execute(
            """INSERT INTO task_runs
               (id, task_id, task_type, attempt, status, lease_id, lease_until,
                payload_json, started_at, created_at)
               VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)""",
            (run_id, task_id, task_type, attempt, lease_id, lease_until, json.dumps(safe, ensure_ascii=False), now, now),
        )
        await self.db.commit()
        return run_id

    async def set_result(self, run_id: str, result: dict[str, Any], status: str, error_code: str | None) -> None:
        now = _now()
        safe = strip_secrets(result)
        await self.db.execute(
            """UPDATE task_runs SET result_json=?, status=?, error_code=?, finished_at=?,
               duration_ms=CAST((julianday(?) - julianday(started_at)) * 86400000 AS INTEGER)
               WHERE id=?""",
            (json.dumps(safe, ensure_ascii=False), status, error_code, now, now, run_id),
        )
        await self.db.commit()

    async def mark(self, run_id: str, status: str, **fields: Any) -> None:
        sets = ["status=?"]
        vals: list[Any] = [status]
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(run_id)
        await self.db.execute(f"UPDATE task_runs SET {', '.join(sets)} WHERE id=?", vals)
        await self.db.commit()

    async def unfinished(self) -> list[aiosqlite.Row]:
        cur = await self.db.execute(
            "SELECT * FROM task_runs WHERE status IN ('running','submitting')"
        )
        return list(await cur.fetchall())

    async def add_event(self, run_id: str | None, level: str, event: str, data: dict[str, Any] | None = None) -> None:
        await self.db.execute(
            "INSERT INTO events (run_id, ts, level, event, data_json) VALUES (?, ?, ?, ?, ?)",
            (run_id, _now(), level, event, json.dumps(strip_secrets(data or {}), ensure_ascii=False)),
        )
        await self.db.commit()

    async def add_artifact(
        self,
        run_id: str,
        *,
        kind: str,
        path: str,
        sha256: str | None,
        size: int,
        content_type: str,
    ) -> str:
        art_id = str(uuid.uuid4())
        await self.db.execute(
            """INSERT INTO artifacts (id, run_id, kind, path, sha256, bytes, content_type, uploaded, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (art_id, run_id, kind, path, sha256, size, content_type, _now()),
        )
        await self.db.commit()
        return art_id

    async def blocked_rate_1h(self) -> float:
        cur = await self.db.execute(
            """SELECT
                 SUM(CASE WHEN error_code IS NOT NULL OR status='acked' THEN 1 ELSE 0 END) AS n,
                 SUM(CASE WHEN json_extract(result_json, '$.status') = 'blocked' THEN 1 ELSE 0 END) AS b
               FROM task_runs
               WHERE status='acked' AND started_at >= datetime('now', '-1 hour')"""
        )
        row = await cur.fetchone()
        n = (row["n"] if row else 0) or 0
        b = (row["b"] if row else 0) or 0
        return (b / n) if n else 0.0

    async def acked_count(self) -> int:
        cur = await self.db.execute("SELECT COUNT(*) AS n FROM task_runs WHERE status='acked'")
        row = await cur.fetchone()
        return int(row["n"] if row else 0)
