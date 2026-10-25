"""Migration integrity and SQL query semantics, using disposable databases only."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.api.app import migrations
from services.api.app.db import Store


def test_legacy_upgrade_preserves_rows_and_event_sequence(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as conn:
        for sql in migrations.MIGRATIONS[0][2]:
            conn.execute(sql)
        conn.execute("INSERT INTO records VALUES(?,?,?)", ('msg_old', 'message', '{"id":"msg_old","conversation_id":"c"}'))
        conn.execute("INSERT INTO users VALUES('u','synthetic@example.com','test','hash','h')")
        conn.execute("INSERT INTO events VALUES(41,'r','test','{}','old')")
    store = Store(path)
    assert store.get('msg_old')['conversation_id'] == 'c'
    with store.tx() as conn:
        store.event('r', 'next', {}, conn)
        assert conn.execute('SELECT password FROM users').fetchone()[0] == 'hash'
    assert [e['id'] for e in store.events('r')] == [41, 42]
    Store(path)
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == migrations.SCHEMA_VERSION


def test_migration_failure_rolls_back_entire_upgrade(tmp_path, monkeypatch):
    path = tmp_path / 'failure.sqlite'
    monkeypatch.setattr(migrations, 'MIGRATIONS', migrations.MIGRATIONS + ((migrations.SCHEMA_VERSION + 1, 'broken', ('CREATE TABLE partial(x)', 'INVALID SQL')),))
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


@pytest.mark.parametrize('tamper', ["UPDATE schema_migrations SET checksum='changed' WHERE version=1", "INSERT INTO schema_migrations VALUES(999,'future','x','now')", 'DELETE FROM schema_migrations WHERE version=1'])
def test_incompatible_history_refuses_startup(tmp_path, tamper):
    path = tmp_path / 'tamper.sqlite'
    store = Store(path)
    with store.tx() as conn:
        conn.execute(tamper)
    with pytest.raises(RuntimeError):
        Store(path)


def test_concurrent_startup(tmp_path):
    path = tmp_path / 'concurrent.sqlite'
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: Store(path), range(8)))
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == migrations.SCHEMA_VERSION


def test_history_filters_before_limit_and_preserves_order(tmp_path):
    store = Store(tmp_path / 'history.sqlite')
    with store.tx() as conn:
        for i in range(60):
            store.put('message', {'id': str(i), 'conversation_id': 'other' if i % 3 == 0 else 'target', **({'run_id': 'current'} if i > 30 else {})}, conn)
    expected = [str(i) for i in range(31) if i % 3][-10:]
    assert [m['id'] for m in store.conversation_records('message', 'target', limit=10, exclude_run='current')] == expected
    assert store.conversation_records('message', "target' OR 1=1 --") == []
    assert len(store.conversation_records('message', 'target')) == 40
    with pytest.raises(ValueError):
        store.conversation_records('user', 'target')


def test_claim_filters_retry_and_lease_and_uses_indexes(tmp_path):
    store = Store(tmp_path / 'claims.sqlite')
    with store.tx() as conn:
        for i in range(100):
            store.put('run', {'id': str(i), 'conversation_id': 'c', 'status': 'succeeded'}, conn)
        for obj in [dict(id='delay', status='queued', next_attempt_at=200), dict(id='leased', status='running', lease_until=200), dict(id='expired', status='running', lease_until=99), dict(id='ready', status='queued')]:
            store.put('run', {**obj, 'conversation_id': 'c'}, conn)
        assert store.claim_candidate(100, conn)['id'] == 'expired'
        assert store.claim_candidate(98, conn)['id'] == 'ready'
        plans = []
        conn.set_trace_callback(plans.append)
        store.claim_candidate(100, conn)
        claim_sql = plans[-1]
        conn.set_trace_callback(None)
        assert 'runs_ready' in str([tuple(r) for r in conn.execute('EXPLAIN QUERY PLAN ' + claim_sql)])
        assert 'messages_conversation' in str([tuple(r) for r in conn.execute("EXPLAIN QUERY PLAN SELECT data FROM messages WHERE conversation_id='c' ORDER BY seq DESC LIMIT 10")])
