"""Real isolated worker processes + bounded lease ownership tests, no LLM/network."""
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid

import pytest

from services.api.app.db import Store
from services.api.app.harness import Harness
from services.api.app.leases import LeaseHeartbeat
from tests.test_system import setup, conversation, send


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError('synthetic condition timed out')


@pytest.fixture
def workers(tmp_path):
    children = []
    def start(database, mode):
        prefix = tmp_path / ('worker_' + uuid.uuid4().hex)
        env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'SYSTEMROOT') if key in os.environ}
        env.update(FAMILYAGENT_PROVIDER='demo', FAMILYAGENT_EMBEDDED_WORKER='0', FAMILYAGENT_DB=str(database))
        child = subprocess.Popen([sys.executable, '-m', 'tests.worker_process_fixture', str(database), mode, str(prefix)],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children.append(child)
        until(lambda: prefix.with_suffix('.ready').exists() or child.poll() is not None)
        assert child.poll() is None, child.communicate(timeout=1)
        prefix.with_suffix('.go').touch()
        return child, prefix
    yield start
    for child in children:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=3)


def collect(child):
    out, err = child.communicate(timeout=8)
    assert child.returncode == 0, err
    return json.loads(out)


def test_long_model_renews_then_process_death_allows_recovery(setup, workers):
    app, _, client, _, _ = setup
    run = send(client, conversation(client))
    store = app.state.store
    child, prefix = workers(store.path, 'model-block')
    until(lambda: prefix.with_suffix('.started').exists())
    initial = store.get(run['id'])
    until(lambda: time.time() > initial['lease_until'] and store.get(run['id']).get('lease_renewals', 0) >= 2)
    fresh = store.get(run['id'])
    assert fresh['lease_until'] > time.time() and fresh['calls'] == 1
    rival, _ = workers(store.path, 'claim')
    assert collect(rival)['claimed'] is None  # original lease has elapsed, heartbeat holds ownership
    child.kill()
    child.communicate(timeout=3)
    until(lambda: time.time() > store.get(run['id'])['lease_until'])
    recovery, _ = workers(store.path, 'complete')
    assert collect(recovery)['worked']
    saved = store.get(run['id'])
    assert saved['status'] == 'succeeded' and saved['calls'] == 2
    assert saved['attempt_id'] != initial['attempt_id']
    messages = store.conversation_records('message', run['conversation_id'])
    assert len([m for m in messages if m['actor_type'] == 'agent']) == 1
    assert any(e['type'] == 'lease.renewed' for e in store.events(run['id']))
    assert any(e['type'] == 'run.recovered' for e in store.events(run['id']))


def test_killed_tool_process_recovers_checkpoint_without_model(setup, workers):
    app, _, client, _, _ = setup
    run = send(client, conversation(client))
    store = app.state.store
    child, prefix = workers(store.path, 'tool-block')
    until(lambda: prefix.with_suffix('.started').exists())
    initial = store.get(run['id'])
    until(lambda: time.time() > initial['lease_until'] and store.get(run['id']).get('lease_renewals', 0) >= 2)
    child.kill()
    child.communicate(timeout=3)
    until(lambda: time.time() > store.get(run['id'])['lease_until'])
    recovery, marker = workers(store.path, 'recover-tool')
    assert collect(recovery)['worked']
    assert not marker.with_suffix('.model').exists()
    saved = store.get(run['id'])
    assert saved['status'] == 'queued' and 'pending_tool' not in saved
    assert saved['calls'] == saved['tool_requests'] == 1
    assert saved['tool_results'][0]['call_id'] == initial['pending_tool']['id']
    assert len([e for e in store.events(run['id']) if e['type'] == 'tool.completed']) == 1


def test_renewal_deadline_allows_takeover_and_fences_late_model(setup, workers):
    app, _, client, _, _ = setup
    run = send(client, conversation(client))
    store = app.state.store
    old, prefix = workers(store.path, 'deadline-model')
    until(lambda: prefix.with_suffix('.started').exists())
    initial = store.get(run['id'])
    until(lambda: time.time() > initial['lease_deadline'])
    assert store.get(run['id'])['lease_until'] <= initial['lease_deadline']
    replacement, _ = workers(store.path, 'complete')
    assert collect(replacement)['worked']
    prefix.with_suffix('.release').touch()
    assert collect(old)['worked']
    saved = store.get(run['id'])
    assert saved['status'] == 'succeeded' and saved['results'][0]['summary'] == 'synthetic complete'
    assert saved['usage']['input_tokens'] == 2  # late reported usage is retained
    assert any(e['type'] == 'attempt.discarded' for e in store.events(run['id']))


@pytest.mark.parametrize('change', [
    {'status': 'cancelled'}, {'status': 'paused'}, {'worker': 'other'},
    {'attempt_id': 'superseding-attempt'}, {'lease_until': 0}, {'lease_deadline': 0},
])
def test_renewal_cannot_revive_or_overwrite_lost_ownership(tmp_path, change):
    store = Store(tmp_path / 'lease.sqlite')
    harness = Harness(store, delay=0)
    with store.tx() as conn:
        store.put('run', {'id': 'r', 'conversation_id': 'c', 'status': 'queued', 'cursor': 0}, conn)
    claimed = harness.claim()
    with store.tx() as conn:
        fresh = store.get('r', conn)
        fresh.update(change)
        store.put('run', fresh, conn)
    assert not store.renew_lease('r', claimed['worker'], claimed['attempt_id'], claimed['lease_deadline'], 120)
    assert store.get('r') == fresh
    assert not any(e['type'] == 'lease.renewed' for e in store.events('r'))


def test_heartbeat_stops_on_worker_shutdown_and_context_exit(tmp_path):
    store = Store(tmp_path / 'shutdown.sqlite')
    harness = Harness(store, delay=0, lease_seconds=.5, heartbeat_interval=.05, max_attempt_seconds=2)
    with store.tx() as conn:
        store.put('run', {'id': 'r', 'conversation_id': 'c', 'status': 'queued', 'cursor': 0}, conn)
    run = harness.claim()
    with LeaseHeartbeat(store, run, harness.stop_event, .05, .5) as heartbeat:
        until(lambda: store.get('r').get('lease_renewals', 0) > 0)
        harness.stop_event.set()
        heartbeat.thread.join(timeout=1)
        assert not heartbeat.thread.is_alive()
    assert not heartbeat.thread.is_alive()
    stopped = store.get('r')
    assert stopped['status'] == 'running'  # shutdown doesn't prematurely release an active call


def test_busy_writer_does_not_revive_expired_lease(tmp_path):
    store = Store(tmp_path / 'busy.sqlite')
    harness = Harness(store, delay=0, lease_seconds=.3, heartbeat_interval=.05, max_attempt_seconds=1)
    with store.tx() as conn:
        store.put('run', {'id': 'r', 'conversation_id': 'c', 'status': 'queued', 'cursor': 0}, conn)
    run = harness.claim()
    with store.tx() as blocker:
        with pytest.raises(sqlite3.OperationalError):
            store.renew_lease('r', run['worker'], run['attempt_id'], run['lease_deadline'], .3)
        until(lambda: time.time() > run['lease_until'])
    assert not store.renew_lease('r', run['worker'], run['attempt_id'], run['lease_deadline'], .3)
    assert store.get('r')['lease_until'] == run['lease_until']


def test_production_worker_entrypoint_starts_and_stops_offline(setup):
    app, _, client, _, _ = setup
    run = send(client, conversation(client), content='请解释时间管理，只交流，不创建任务', intent='question')
    env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'SYSTEMROOT') if key in os.environ}
    env.update(FAMILYAGENT_PROVIDER='demo', FAMILYAGENT_DEMO_DELAY='0', FAMILYAGENT_DB=app.state.store.path)
    child = subprocess.Popen([sys.executable, '-m', 'services.api.app.worker'], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until(lambda: app.state.store.get(run['id'])['status'] == 'succeeded')
        child.terminate()
        _, err = child.communicate(timeout=5)
        assert child.returncode == 0, err
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=3)
