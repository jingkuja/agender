import os

from agender.store.db import Store
from agender.store.retention import gc


async def test_gc_keeps_unacknowledged_rows_and_artifacts(tmp_path):
    store = Store(tmp_path / 'runs.db')
    await store.open()
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    try:
        for state in ('submitting', 'acked'):
            run = await store.insert_run(task_id=state, task_type='web.search', attempt=1,
                                         lease_id='l', lease_until=None, payload={})
            await store.mark(run, state)
            file = artifacts / f'{state}.html'
            file.write_text('<p>Diagnostic</p>')
            os.utime(file, (1, 1))
            await store.add_artifact(run, kind='html', path=str(file), sha256=None, size=17, content_type='text/html')
            await store.add_event(run, 'info', 'stage')
        for table, field in (('task_runs', 'created_at'), ('artifacts', 'created_at'), ('events', 'ts')):
            await store.db.execute(f"UPDATE {table} SET {field}='2000-01-01T00:00:00Z'")
        await store.db.commit()
        result = await gc(store, artifact_dir=artifacts, log_path=tmp_path / 'absent.log',
                          artifact_days=7, run_days=30, log_days=14)
        assert result == {'files': 1, 'events': 1, 'artifacts': 1, 'runs': 1}
        assert (artifacts / 'submitting.html').exists()
        assert await store.get_run('submitting', 1)
        assert not await store.get_run('acked', 1)
    finally:
        await store.close()
