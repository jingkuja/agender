import csv
import json
from datetime import UTC, datetime, timedelta

import pytest

from agender.protocol.schemas import TaskEnvelope, TaskType, parse_params
from agender.store import checkpoints
from agender.store.content import ContentStore
from agender.worker.execution import ExecutionStopped


@pytest.fixture
async def content(tmp_path):
    store = ContentStore(tmp_path / 'content.db')
    await store.open()
    yield store
    await store.close()


async def rows(store, table):
    return await (await store.db.execute(f'SELECT * FROM {table}')).fetchall()


async def test_entity_upsert_metric_history_and_parent_links(content, tmp_path):
    one = {'video': {'video_id': '12345', 'title': 'First', 'stats': {'like_count': 10}},
           'comments': [{'comment_id': 'c1', 'text': 'Hello', 'replies': [
               {'comment_id': 'r1', 'parent_id': 'c1', 'text': 'Reply'}]}],
           'observed_at': '2026-10-06T01:00:00+00:00'}
    await content.save_result('douyin.video_bundle', one)
    await content.save_result('douyin.video_bundle', one)
    await content.save_result('douyin.video_stats', {'video_id': '12345', 'title': '',
        'stats': {'like_count': 12, 'play_count': None}, 'observed_at': '2026-10-06T02:00:00+00:00'})
    entities = await rows(content, 'contents')
    assert len(entities) == 1
    data = json.loads(entities[0]['data_json'])
    assert data['title'] == 'First'
    assert data['stats']['like_count'] == 12
    assert data['stats']['play_count'] is None
    assert data['field_observed_at']['title'] == one['observed_at']
    assert len(await rows(content, 'metric_snapshots')) == 2
    comments = await rows(content, 'comments')
    assert len(comments) == 2
    assert next(c for c in comments if c['comment_id'] == 'r1')['parent_id'] == 'c1'
    path = tmp_path / 'export.jsonl'
    assert await content.export(path) == 1
    assert json.loads(path.read_text())['data']['title'] == 'First'
    csv_path = tmp_path / 'comments.csv'
    assert await content.export(csv_path, table='comments', format='csv') == 2
    with csv_path.open() as f:
        assert len(list(csv.DictReader(f))) == 2
    assert not list(tmp_path.glob('.export-*'))


async def test_transaction_rolls_back_partial_batch(content):
    with pytest.raises(AttributeError):
        await content.save_result('douyin.search', {'items': [{'video_id': '12345'}, None]})
    assert not await rows(content, 'contents')


async def test_url_dedup_and_failed_document_exclusion(content):
    await content.save_result('web.fetch_article', {'documents': [
        {'url': 'https://example.org/a?utm_source=x', 'fetch_status': 'ok', 'body_text': 'Body'},
        {'url': 'https://example.org/a#anchor', 'fetch_status': 'ok'},
        {'url': 'https://example.org/b', 'fetch_status': 'failed'}]})
    assert len(await rows(content, 'contents')) == 1


async def test_checkpoint_resume_requires_new_matching_attempt(content):
    task = TaskEnvelope(task_id='resume', type='douyin.search', params={'query': 'q'})
    result = {'kind': task.type, 'items': [{'video_id': '12345', 'title': 'Saved'}], 'password': 'secret'}
    cp = await checkpoints.save(content, task, result, 'v1', 3600)
    assert cp
    later = task.model_copy(deep=True, update={'attempt': 2})
    later.params['resume_from'] = cp
    params = parse_params(TaskType(later.type), later.params).model_dump(mode='json')
    restored = await checkpoints.restore(content, later, params, 'v1')
    assert restored['items'] == result['items']
    assert 'password' not in restored
    with pytest.raises(ExecutionStopped, match='CHECKPOINT_ATTEMPT_MISMATCH'):
        await checkpoints.restore(content, task, params, 'v1')
    later.params['sort'] = 'latest'
    with pytest.raises(ExecutionStopped, match='CHECKPOINT_MISMATCH'):
        await checkpoints.restore(content, later, params, 'v1')
    later.params['sort'] = 'general'
    with pytest.raises(ExecutionStopped, match='CHECKPOINT_MISMATCH'):
        await checkpoints.restore(content, later, params, 'v2')
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    await content.db.execute('UPDATE checkpoints SET expires_at=?', (expired,))
    await content.db.commit()
    with pytest.raises(ExecutionStopped, match='CHECKPOINT_EXPIRED'):
        await checkpoints.restore(content, later, params, 'v1')
    assert await content.prune_checkpoints() == 1


async def test_completed_collection_does_not_create_checkpoint(content):
    task = TaskEnvelope(task_id='done', type='douyin.search', params={'query': 'q'})
    for flags in ({'exhausted': True}, {'limit_reached': True}):
        assert await checkpoints.save(content, task, {'items': [{'video_id': '12345'}], **flags}, 'v1', 60) is None


async def test_future_database_version_is_not_silently_downgraded(content):
    await content.db.execute('PRAGMA user_version=99')
    path = content.path
    await content.close()
    with pytest.raises(RuntimeError, match='unsupported content database version'):
        await content.open()
    import sqlite3
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 99
