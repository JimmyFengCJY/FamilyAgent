"""Offline v2 -> v3 cutover, integrity boundaries and checkpoint recovery."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.api.app import migrations
from services.api.app.db import Store, dump
from services.api.app.harness import Harness
from tests.test_system import setup, conversation, send


def legacy_database(path):
    """Freeze the actual released v2 DDL/checksums, without calling new Store APIs."""
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    for version, name, statements in migrations.MIGRATIONS[:2]:
        for sql in statements:
            conn.execute(sql)
        conn.execute('INSERT INTO schema_migrations(version,name,checksum) VALUES(?,?,?)',
                     (version, name, hashlib.sha256('\n'.join(statements).encode()).hexdigest()))
    conn.commit()
    return conn


def test_cutover_preserves_raw_payloads_order_and_cursor(tmp_path):
    path = tmp_path / 'v2.sqlite'
    with legacy_database(path) as conn:
        for seq, kind, value in [
            (8, 'message', {'id': 'm1', 'conversation_id': 'c', 'content': '合成旧消息'}),
            (11, 'task', {'id': 't', 'status': 'todo'}),
            (14, 'run', {'id': 'r', 'conversation_id': 'c', 'status': 'paused', 'cursor': 2, 'calls': 3,
                         'pending_tool': {'id': 'stable-call', 'name': 'task.list'}, 'usage': {'input_tokens': 8}}),
            (21, 'message', {'id': 'm2', 'conversation_id': 'c', 'run_id': 'r', 'content': '合成回复'}),
        ]:
            conn.execute('INSERT INTO records(rowid,id,kind,data) VALUES(?,?,?,?)', (seq, value['id'], kind, json.dumps(value, ensure_ascii=False, indent=2)))
        expected = conn.execute("SELECT rowid,id,data FROM records WHERE kind IN ('run','message') ORDER BY rowid").fetchall()
        conn.execute("INSERT INTO events VALUES(50,'r','checkpoint','{}','synthetic')")
    store = Store(path)
    with store.connect() as conn:
        actual = conn.execute('SELECT seq,id,data FROM runs UNION ALL SELECT seq,id,data FROM messages ORDER BY seq').fetchall()
        assert [tuple(r) for r in actual] == expected  # byte-for-byte, not just equivalent JSON
        assert conn.execute("SELECT count(*) FROM records WHERE kind IN ('run','message')").fetchone()[0] == 0
        assert conn.execute('SELECT rowid FROM records WHERE id=\'t\'').fetchone()[0] == 11
        assert conn.execute('SELECT status,lease_until,next_attempt_at FROM runs').fetchone()[:] == ('paused', 0, 0)
    assert store.message_page('c', before='m2')['messages'][0]['id'] == 'm1'
    with store.tx() as conn:
        store.put('message', {'id': 'm3', 'conversation_id': 'c'}, conn)
        store.event('r', 'next', {}, conn)
    assert [m['id'] for m in store.message_page('c', after='m2')['messages']] == ['m3']
    assert [e['id'] for e in store.events('r')] == [50, 51]
    Store(path)  # reopen is idempotent
    assert store.get('r')['pending_tool']['id'] == 'stable-call'


def test_cutover_failure_after_delete_restores_v2(tmp_path, monkeypatch):
    path = tmp_path / 'rollback.sqlite'
    with legacy_database(path) as conn:
        conn.execute('INSERT INTO records VALUES(?,?,?)', ('m', 'message', '{"id":"m","conversation_id":"c"}'))
    version, name, statements = migrations.MIGRATIONS[2]
    monkeypatch.setattr(migrations, 'MIGRATIONS', (*migrations.MIGRATIONS[:2], (version, name, (*statements, 'INVALID SQL'))))
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM records').fetchone()[0] == 1
        assert conn.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 2
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name IN ('runs','messages','records_insert_guard')").fetchall()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='records_run_status'").fetchone()


@pytest.mark.parametrize('value', [
    {'id': 'bad', 'status': 'queued'},
    {'id': 'bad', 'conversation_id': 'c', 'status': 'unknown'},
    {'id': 'bad', 'conversation_id': 'c', 'status': 'queued', 'lease_until': 'later'},
    {'id': 'mismatched', 'conversation_id': 'c', 'status': 'queued'},
])
def test_invalid_legacy_rows_abort_without_losing_source(tmp_path, value):
    path = tmp_path / 'invalid.sqlite'
    with legacy_database(path) as conn:
        conn.execute('INSERT INTO records VALUES(?,?,?)', ('bad', 'run', dump(value)))
    with pytest.raises(sqlite3.IntegrityError):
        Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT data FROM records WHERE id=\'bad\'').fetchone()[0] == dump(value)
        assert conn.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 2


def test_constraints_apply_to_repository_and_direct_sql(tmp_path):
    store = Store(tmp_path / 'constraints.sqlite')
    with store.tx() as conn:
        store.put('run', {'id': 'r', 'conversation_id': 'c', 'status': 'queued'}, conn)
        store.put('message', {'id': 'm', 'conversation_id': 'c'}, conn)
        seq = conn.execute("SELECT seq FROM runs WHERE id='r'").fetchone()[0]
        store.put('run', {'id': 'r', 'conversation_id': 'c', 'status': 'running', 'lease_until': 42}, conn)
        assert tuple(conn.execute('SELECT seq,status,lease_until FROM runs').fetchone()) == (seq, 'running', 42)
        bad_sql = [
            ("INSERT INTO records VALUES('legacy','message','{}')", ()),
            ("INSERT INTO records VALUES('r','task','{}')", ()),
            ("INSERT INTO messages(id,data) VALUES('r',?)", (dump({'id': 'r', 'conversation_id': 'c'}),)),
            ("UPDATE runs SET seq=99", ()),
            ("UPDATE runs SET id='renamed'", ()),
            ("UPDATE runs SET data=?", (dump({'id': 'r', 'conversation_id': 'other', 'status': 'queued'}),)),
            ("UPDATE messages SET data=?", (dump({'id': 'm', 'conversation_id': 'c', 'run_id': 4}),)),
            ("UPDATE runs SET data=?", (dump({'id': 'r', 'conversation_id': 'c', 'status': 'unknown'}),)),
        ]
        for sql, args in bad_sql:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql, args)
    assert store.get('r')['status'] == 'running'
    assert store.get('m')['conversation_id'] == 'c'


def test_migrated_pending_tool_recovers_without_model(setup, tmp_path, monkeypatch):
    app, harness, client, _, _ = setup
    harness.gateway.generate = lambda context: ({'tool_request': {'name': 'task.list', 'arguments': {}}}, {'provider': 'demo'})
    monkeypatch.setattr(harness, 'execute_pending_tool', lambda run: True)
    run = send(client, conversation(client))
    assert harness.tick()
    saved = app.state.store.get(run['id'])
    saved.update(worker='exited-v2-worker', lease_until=0)
    path = tmp_path / 'recover-v2.sqlite'
    # Export only this synthetic test fixture into the released layout.
    with legacy_database(path) as dest, app.state.store.connect() as source:
        for table in ('users', 'sessions', 'invitations', 'events'):
            for row in source.execute(f'SELECT * FROM {table}'):
                dest.execute(f'INSERT INTO {table} VALUES({",".join("?" for _ in row)})', tuple(row))
        for row in source.execute('SELECT id,kind,data FROM records'):
            dest.execute('INSERT INTO records VALUES(?,?,?)', tuple(row))
        for message in app.state.store.all('message'):
            dest.execute('INSERT INTO records VALUES(?,?,?)', (message['id'], 'message', dump(message)))
        dest.execute('INSERT INTO records VALUES(?,?,?)', (saved['id'], 'run', dump(saved)))
    upgraded = Store(path)
    replacement = Harness(upgraded, delay=0)
    replacement.gateway.generate = lambda context: pytest.fail('must resume migrated pending tool without another model request')
    assert replacement.tick()
    fresh = upgraded.get(saved['id'])
    assert fresh['status'] == 'queued' and 'pending_tool' not in fresh
    assert fresh['calls'] == 1 and fresh['tool_requests'] == 1
    assert fresh['tool_results'][0]['call_id'] == saved['pending_tool']['id']
    assert any(e['type'] == 'run.recovered' for e in upgraded.events(saved['id']))


def test_two_workers_do_not_claim_same_run(tmp_path):
    store = Store(tmp_path / 'claims.sqlite')
    with store.tx() as conn:
        for i in range(8):
            store.put('run', {'id': f'r{i}', 'conversation_id': f'c{i}', 'status': 'queued', 'cursor': 0}, conn)
    workers = [Harness(store, delay=0), Harness(store, delay=0)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda i: workers[i % 2].claim(), range(8)))
    assert len({r['id'] for r in claimed}) == 8
    assert len({r['attempt_id'] for r in claimed}) == 8
    assert workers[0].claim() is None


def test_transfer_verification_detects_payload_drift(tmp_path, monkeypatch):
    path = tmp_path / 'drift.sqlite'
    original = '{"id":"m","conversation_id":"c","content":"synthetic-original"}'
    with legacy_database(path) as conn:
        conn.execute('INSERT INTO records VALUES(?,?,?)', ('m', 'message', original))
    version, name, statements = migrations.MIGRATIONS[2]
    statements = list(statements)
    index = next(i for i, sql in enumerate(statements) if sql.startswith('INSERT INTO split_verification'))
    statements.insert(index, "UPDATE messages SET data=json_set(data,'$.content','synthetic-drift')")
    monkeypatch.setattr(migrations, 'MIGRATIONS', (*migrations.MIGRATIONS[:2], (version, name, tuple(statements))))
    with pytest.raises(sqlite3.IntegrityError):
        Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT data FROM records').fetchone()[0] == original
        assert conn.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 2


def test_business_transaction_rolls_back_across_split_tables(tmp_path):
    store = Store(tmp_path / 'atomic.sqlite')
    with pytest.raises(RuntimeError, match='synthetic rollback'):
        with store.tx() as conn:
            store.put('run', {'id': 'r', 'conversation_id': 'c', 'status': 'queued'}, conn)
            store.put('message', {'id': 'm', 'conversation_id': 'c', 'run_id': 'r'}, conn)
            store.put('task', {'id': 't', 'status': 'todo'}, conn)
            store.event('r', 'synthetic', {}, conn)
            raise RuntimeError('synthetic rollback')
    assert all(store.get(key) is None for key in ('r', 'm', 't'))
    assert store.events('r') == []


def test_all_existing_run_states_survive_upgrade(tmp_path):
    path = tmp_path / 'states.sqlite'
    states = ('queued', 'running', 'paused', 'exhausted', 'failed', 'waiting_approval', 'waiting_user', 'succeeded', 'cancelled')
    with legacy_database(path) as conn:
        for status in states:
            value = {'id': status, 'conversation_id': 'c', 'status': status}
            conn.execute('INSERT INTO records VALUES(?,?,?)', (status, 'run', dump(value)))
    store = Store(path)
    assert [r['status'] for r in store.all('run')] == list(states)
