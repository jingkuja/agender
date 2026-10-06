from __future__ import annotations

import time
from pathlib import Path

from agender.store.db import Store


def _older_than(path: Path, days: int) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age > days * 86400


async def gc(store: Store, *, artifact_dir: Path, log_path: Path, artifact_days: int, run_days: int, log_days: int) -> dict[str, int]:
    deleted_files = 0
    cursor = await store.db.execute("SELECT path FROM artifacts WHERE run_id IN (SELECT id FROM task_runs WHERE status IN ('running','submitting'))")
    protected = {Path(row[0]).resolve() for row in await cursor.fetchall()}
    if artifact_dir.exists():
        for p in artifact_dir.rglob("*"):
            if p.is_file() and p.resolve() not in protected and _older_than(p, artifact_days):
                p.unlink(missing_ok=True)
                deleted_files += 1
    if log_path.exists() and _older_than(log_path, log_days):
        log_path.unlink(missing_ok=True)
        deleted_files += 1
    cutoff = f"-{run_days} days"
    cur = await store.db.execute(
        "DELETE FROM events WHERE julianday(ts) < julianday('now', ?) AND (run_id IS NULL OR run_id IN (SELECT id FROM task_runs WHERE status IN ('acked','interrupted','superseded')))", (cutoff,)
    )
    events = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    cur = await store.db.execute(
        "DELETE FROM artifacts WHERE julianday(created_at) < julianday('now', ?) AND run_id IN (SELECT id FROM task_runs WHERE status IN ('acked','interrupted','superseded'))", (cutoff,)
    )
    arts = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    cur = await store.db.execute(
        "DELETE FROM task_runs WHERE julianday(created_at) < julianday('now', ?) AND status IN ('acked','interrupted','superseded')",
        (cutoff,),
    )
    runs = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    await store.db.commit()
    return {"files": deleted_files, "events": events, "artifacts": arts, "runs": runs}
