from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from agender.extractors.web.urls import normalize_url
from agender.protocol.redact import strip_secrets

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS contents (
 platform TEXT NOT NULL, content_id TEXT NOT NULL, kind TEXT NOT NULL, url TEXT,
 data_json TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 PRIMARY KEY(platform, content_id)
);
CREATE TABLE IF NOT EXISTS comments (
 platform TEXT NOT NULL, content_id TEXT NOT NULL, comment_id TEXT NOT NULL, parent_id TEXT,
 data_json TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 PRIMARY KEY(platform, content_id, comment_id)
);
CREATE TABLE IF NOT EXISTS creators (
 platform TEXT NOT NULL, creator_id TEXT NOT NULL, data_json TEXT NOT NULL,
 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, PRIMARY KEY(platform, creator_id)
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
 platform TEXT NOT NULL, content_id TEXT NOT NULL, observed_at TEXT NOT NULL, data_json TEXT NOT NULL,
 PRIMARY KEY(platform, content_id, observed_at)
);
CREATE TABLE IF NOT EXISTS checkpoints (
 checkpoint_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt INTEGER NOT NULL, task_type TEXT NOT NULL,
 fingerprint TEXT NOT NULL, extractor_version TEXT NOT NULL, result_json TEXT NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS checkpoint_expiry ON checkpoints(expires_at);
"""
TABLES = {"contents", "comments", "creators", "metric_snapshots"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _merge(old: dict, new: dict) -> dict:
    merged = dict(old)
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        elif value is not None and value != "" and value != [] or key not in merged:
            merged[key] = value
    return merged


def _field_times(data: dict, observed: str, prefix="") -> dict:
    result = {}
    for key, value in data.items():
        if key == "field_observed_at":
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_field_times(value, observed, path))
        elif value is not None and value != "" and value != []:
            result[path] = observed
    return result


class ContentStore:
    """Local query/export store; never a source of scheduled tasks."""

    def __init__(self, path: Path):
        self.path = path
        self.db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        version = (await (await self.db.execute("PRAGMA user_version")).fetchone())[0]
        if version not in (0, 1):
            await self.close()
            raise RuntimeError(f"unsupported content database version: {version}")
        await self.db.executescript(SCHEMA)
        if version == 0:
            await self.db.execute("PRAGMA user_version=1")
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()
            self.db = None

    async def _upsert(self, table: str, keys: dict, data: dict, extra: dict | None = None) -> None:
        where = " AND ".join(f"{key}=?" for key in keys)
        row = await (await self.db.execute(f"SELECT data_json FROM {table} WHERE {where}", tuple(keys.values()))).fetchone()
        observed = data.get("observed_at") or _now()
        value = strip_secrets(data)
        old = json.loads(row[0]) if row else {}
        merged = _merge(old, value)
        merged["field_observed_at"] = {**old.get("field_observed_at", {}), **_field_times(value, observed)}
        fields = {**keys, **(extra or {}), "data_json": json.dumps(merged, ensure_ascii=False), "first_seen": observed, "last_seen": observed}
        updates = [f"{key}=excluded.{key}" for key in fields if key not in keys and key != "first_seen"]
        await self.db.execute(
            f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)}) "
            f"ON CONFLICT({','.join(keys)}) DO UPDATE SET {','.join(updates)}", tuple(fields.values()),
        )

    async def _content(self, platform: str, row: dict, kind: str, observed: str) -> str | None:
        url = row.get("canonical_url") or row.get("url")
        cid = row.get("video_id") or row.get("content_id")
        if not cid and url:
            cid = "url:" + hashlib.sha256(normalize_url(url).encode()).hexdigest()
        if not cid:
            return None
        value = {**row, "observed_at": row.get("observed_at") or observed}
        await self._upsert("contents", {"platform": platform, "content_id": str(cid)}, value, {"kind": kind, "url": url})
        stats = value.get("stats") or {}
        if any(value is not None for value in stats.values()):
            await self.db.execute("INSERT OR IGNORE INTO metric_snapshots VALUES (?,?,?,?)",
                                  (platform, str(cid), value["observed_at"], json.dumps(stats, ensure_ascii=False)))
        author = row.get("author") or {}
        aid = author.get("sec_uid") or author.get("user_id")
        if aid:
            await self._upsert("creators", {"platform": platform, "creator_id": str(aid)}, {**author, "observed_at": observed})
        return str(cid)

    async def _comments(self, platform: str, cid: str, comments: list, observed: str) -> None:
        for row in comments:
            if not row.get("comment_id"):
                continue
            await self._upsert("comments", {"platform": platform, "content_id": cid, "comment_id": row["comment_id"]},
                               {**row, "observed_at": row.get("observed_at") or observed}, {"parent_id": row.get("parent_id")})
            await self._comments(platform, cid, row.get("replies", []), observed)

    async def save_result(self, task_type: str, result: dict) -> None:
        platform = task_type.split(".", 1)[0]
        observed = result.setdefault("observed_at", _now())
        async with self._lock:
            try:
                await self.db.execute("BEGIN IMMEDIATE")
                video = result.get("content") or result.get("video") or (result if result.get("video_id") else None)
                kind = "video" if platform in {"douyin", "bilibili"} else "note" if platform == "xiaohongshu" else "post" if platform == "weibo" else "article"
                if video:
                    cid = await self._content(platform, video, kind, observed)
                    if cid:
                        await self._comments(platform, cid, result.get("comments", []), observed)
                for row in result.get("items", []):
                    await self._content(platform, row, kind, observed)
                for row in result.get("serp", []):
                    await self._content("web", row, "article", observed)
                for row in result.get("documents", []):
                    if row.get("fetch_status") == "ok":
                        await self._content("web", row, "article", observed)
                for source in result.get("sources", []):
                    for row in source.get("items", []):
                        await self._content("web", row, "hot_item", observed)
                creator = result.get("creator") or {}
                if creator.get("creator_id"):
                    await self._upsert("creators", {"platform": platform, "creator_id": creator["creator_id"]}, creator)
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def export(self, path: Path, *, table: str = "contents", format: str = "jsonl") -> int:
        if table not in TABLES or format not in {"jsonl", "csv"}:
            raise ValueError("invalid export table or format")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".export-", dir=path.parent)
        count = 0
        try:
            async with self._lock:
                cursor = await self.db.execute(f"SELECT * FROM {table} ORDER BY 1, 2")
                columns = [entry[0] for entry in cursor.description]
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
                    writer = csv.DictWriter(output, fieldnames=columns) if format == "csv" else None
                    if writer:
                        writer.writeheader()
                    while rows := await cursor.fetchmany(500):
                        for record in rows:
                            value = dict(record)
                            if writer:
                                # CSV may be opened in a spreadsheet. Preserve raw values in JSONL.
                                value = {key: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for key, v in value.items()}
                                writer.writerow(value)
                            else:
                                value["data"] = json.loads(value.pop("data_json"))
                                output.write(json.dumps(value, ensure_ascii=False) + "\n")
                            count += 1
                    output.flush()
                    os.fsync(output.fileno())
                await cursor.close()
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)
        return count

    async def prune_checkpoints(self) -> int:
        async with self._lock:
            cur = await self.db.execute("DELETE FROM checkpoints WHERE expires_at<=?", (_now(),))
            await self.db.commit()
            return cur.rowcount

    async def save_checkpoint(self, *, checkpoint_id, task_id, attempt, task_type, fingerprint, extractor_version, result, ttl_s):
        now = datetime.now(UTC)
        async with self._lock:
            await self.db.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?,?,?,?,?,?)", (
                checkpoint_id, task_id, attempt, task_type, fingerprint, extractor_version,
                json.dumps(strip_secrets(result), ensure_ascii=False), now.isoformat(), (now + timedelta(seconds=ttl_s)).isoformat(),
            ))
            await self.db.commit()

    async def checkpoint(self, checkpoint_id: str):
        return await (await self.db.execute("SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,))).fetchone()
