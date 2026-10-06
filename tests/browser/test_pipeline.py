"""Local browser/outbox/content pipeline; AGENDER_SOAK_SECONDS extends the same fault loop."""
import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta

from agender.protocol.errors import ProtocolError
from agender.protocol.file_client import FileClient
from agender.protocol.schemas import Lease, TaskEnvelope
from agender.store.db import Store
from agender.worker.loop import WorkerLoop


async def test_pipeline_replay_and_browser_recovery(browser_runtime, local_site, tmp_path):
    cfg = browser_runtime.cfg
    cfg.tools.douyin = False
    cfg.store.content_enabled = True
    cfg.worker.submit_retry_s = 0.01
    base, pages, requests = local_site
    pages['/robots.txt'] = 'User-agent: *\nAllow: /\n'
    store = Store(tmp_path / 'runs.db')
    await store.open()
    client = FileClient(tmp_path / 'in', tmp_path / 'out')
    worker = WorkerLoop(cfg, runtime=browser_runtime, client=client, store=store)
    await worker.setup()
    duration = float(os.environ.get('AGENDER_SOAK_SECONDS', '0'))
    end = time.monotonic() + duration
    count = 0
    submit = client.submit_result
    try:
        while count < 4 or time.monotonic() < end:
            # Exercise a crashed/closed context and reacquire the same profile.
            if count % 3 == 2:
                await browser_runtime.context.close()
                assert not browser_runtime.browser_ready
                await browser_runtime.start('web')
            task = TaskEnvelope(task_id=f'pipeline-{count}', type='web.fetch_article',
                                params={'urls': [base + '/article'], 'capture_screenshot': 'never'})
            async def unavailable(*args):
                raise ProtocolError('UNAVAILABLE', 'injected offline', retryable=True)
            client.submit_result = unavailable
            result = await worker._run_claimed(task, Lease(lease_id=f'lease-{count}',
                lease_until=(datetime.now(UTC) + timedelta(seconds=60)).isoformat()))
            assert result.status.value == 'succeeded'
            assert (await store.get_run(task.task_id, 1))['status'] == 'submitting'
            before = len(requests)
            client.submit_result = submit
            worker._next_submit = 0
            assert await worker._recover()
            assert len(requests) == before  # Result retry never performs a browser request.
            path = tmp_path / 'out/results' / task.task_id / '1.json'
            assert json.loads(path.read_text()) == result.model_dump(mode='json')
            assert len(browser_runtime.context.pages) == 1
            assert not await store.unfinished()
            count += 1
            await asyncio.sleep(0)
        records = await (await worker.content.db.execute('SELECT COUNT(*) FROM contents')).fetchone()
        assert records[0] == 1
        assert await store.acked_count() == count
        print(f'local pipeline: {count} tasks, {count} injected submission failures, duration >= {duration}s')
    finally:
        await worker.aclose()
