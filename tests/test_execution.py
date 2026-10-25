import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest
from services.api.app.harness import Harness
from services.api.app.schemas import AgentResult
from services.api.app.tool_registry import registry, ToolError
from tests.test_system import setup, conversation, send, drain


def request_tool(harness, name='task.list', arguments=None):
    harness.gateway.generate = lambda context: (
        {'tool_request': {'name': name, 'arguments': arguments or {}}}, {'provider': 'demo'})


def slow_tool(monkeypatch, name='task.list'):
    entered, release = threading.Event(), threading.Event()
    count = []
    def handler(args, ctx):
        count.append(1)
        if len(count) == 1:
            entered.set()
            if not release.wait(12):
                raise ToolError('synthetic gate timeout')
            return [{'title': 'OLD_RESULT', 'status': 'todo'}]
        return [{'title': 'NEW_RESULT', 'status': 'todo'}]
    monkeypatch.setitem(registry._tools, name, replace(registry._tools[name], handler=handler))
    return entered, release


def test_tool_wait_allows_other_writes_and_cancel(setup, monkeypatch):
    app, harness, client, _, _ = setup
    request_tool(harness)
    entered, release = slow_tool(monkeypatch)
    conv = conversation(client)
    run = send(client, conv)
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = pool.submit(harness.tick)
        try:
            assert entered.wait(3)
            write = pool.submit(client.post, '/api/tasks', json={'title': 'WRITE_DURING_TOOL_WAIT'})
            assert write.result(timeout=3).status_code == 201
            cancel = pool.submit(client.post, f"/api/runs/{run['id']}/cancel", json={})
            assert cancel.result(timeout=3).status_code == 200
            assert not pending.done()  # both writes committed before tool was released
        finally:
            release.set()
        assert pending.result(timeout=3)
    saved = app.state.store.get(run['id'])
    assert saved['status'] == 'cancelled' and not saved['tool_results']
    assert len(client.get(f"/api/conversations/{conv['id']}").json()['messages']) == 1
    assert any(e['type']=='attempt.discarded' for e in app.state.store.events(run['id']))


def test_pause_resume_same_worker_fences_old_tool_result(setup, monkeypatch):
    app, harness, client, _, _ = setup
    request_tool(harness)
    entered, release = slow_tool(monkeypatch)
    run = send(client, conversation(client))
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(harness.tick)
        try:
            assert entered.wait(3)
            old_attempt = app.state.store.get(run['id'])['attempt_id']
            assert client.post(f"/api/runs/{run['id']}/pause",json={}).status_code==200
            assert client.post(f"/api/runs/{run['id']}/resume",json={}).status_code==200
            assert harness.tick()  # same Harness/worker, fresh attempt, checkpoint only
            new = app.state.store.get(run['id'])
            assert new['attempt_id'] != old_attempt
            assert new['tool_results'][0]['result'][0]['title'] == 'NEW_RESULT'
            assert new['calls']==1 and new['tool_requests']==1
        finally:
            release.set()
        old.result(timeout=3)
    saved=app.state.store.get(run['id'])
    assert len(saved['tool_results'])==1 and 'OLD_RESULT' not in str(saved['tool_results'])
    completed=[e for e in app.state.store.events(run['id']) if e['type']=='tool.completed']
    assert len(completed)==1


def test_crash_checkpoint_recovery_does_not_repeat_model_request(setup, monkeypatch):
    app, harness, client, _, _ = setup
    request_tool(harness)
    # Simulate crash after committing pending_tool, before dispatch.
    monkeypatch.setattr(harness, 'execute_pending_tool', lambda run: True)
    run=send(client,conversation(client))
    assert harness.tick()
    saved=app.state.store.get(run['id'])
    call_id=saved['pending_tool']['id']
    assert saved['calls']==1 and saved['tool_requests']==1
    with app.state.store.tx() as conn:
        saved.update(worker='dead-worker',lease_until=0)
        app.state.store.put('run',saved,conn)
    replacement=Harness(app.state.store,delay=0)
    replacement.gateway.generate=lambda context: pytest.fail('must resume pending tool without model')
    assert replacement.tick()
    saved=app.state.store.get(run['id'])
    assert saved['status']=='queued' and 'pending_tool' not in saved
    assert saved['calls']==1 and saved['tool_requests']==1
    assert saved['tool_results'][0]['call_id']==call_id


def test_tool_failure_can_resume_checkpoint_without_model(setup, monkeypatch):
    app,harness,client,_,_=setup
    request_tool(harness)
    original=registry._tools['task.list']
    def fail(args,ctx):
        raise ToolError('synthetic failure')
    monkeypatch.setitem(registry._tools,'task.list',replace(original,handler=fail))
    run=drain(harness,client,send(client,conversation(client)))
    assert run['status']=='failed' and run['pending_tool']['state']=='failed'
    monkeypatch.setitem(registry._tools,'task.list',original)
    harness.gateway.generate=lambda context: pytest.fail('must retry read checkpoint only')
    assert client.post(f"/api/runs/{run['id']}/resume",json={}).status_code==200
    assert harness.tick()
    saved=app.state.store.get(run['id'])
    assert saved['status']=='queued' and saved['tool_requests']==1 and saved['calls']==1


def test_lease_expiry_without_reclaim_discards_tool_result(setup,monkeypatch):
    app,harness,client,_,_=setup
    request_tool(harness)
    entered,release=slow_tool(monkeypatch)
    run=send(client,conversation(client))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(harness.tick)
        try:
            assert entered.wait(3)
            with app.state.store.tx() as conn:
                saved=app.state.store.get(run['id'],conn)
                saved['lease_until']=0
                app.state.store.put('run',saved,conn)
        finally:
            release.set()
        pending.result(timeout=3)
    assert not app.state.store.get(run['id'])['tool_results']
    assert harness.tick()
    assert app.state.store.get(run['id'])['tool_results'][0]['result'][0]['title']=='NEW_RESULT'


def test_permission_revoked_during_read_rejects_result(setup,monkeypatch):
    app,harness,client,_,_=setup
    request_tool(harness,'reference.search',{'query':'目标拆解','limit':1})
    entered,release=slow_tool(monkeypatch,'reference.search')
    run=send(client,conversation(client,'meeting'),content='解释家庭会议是什么')
    owner=client.get('/api/state').json()['user']['id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(harness.tick)
        try:
            assert entered.wait(3)
            with app.state.store.tx() as conn:
                conn.execute('UPDATE users SET household=? WHERE id=?',('revoked-family',owner))
        finally:
            release.set()
        pending.result(timeout=3)
    saved=app.state.store.get(run['id'])
    assert saved['status']=='failed' and not saved['tool_results']


def test_same_worker_model_aba_discards_reply_but_accounts_usage(setup):
    app,harness,client,_,_=setup
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def generate(context):
        calls.append(1)
        if len(calls)==1:
            entered.set()
            assert release.wait(12)
            return AgentResult(summary='OLD_REPLY').model_dump(),{'provider':'demo','input_tokens':3}
        return AgentResult(summary='NEW_REPLY').model_dump(),{'provider':'demo','input_tokens':2}
    harness.gateway.generate=generate
    conv=conversation(client)
    run=send(client,conv)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old=pool.submit(harness.tick)
        try:
            assert entered.wait(3)
            assert client.post(f"/api/runs/{run['id']}/pause",json={}).status_code==200
            assert client.post(f"/api/runs/{run['id']}/resume",json={}).status_code==200
            assert harness.tick()
        finally:
            release.set()
        old.result(timeout=3)
    saved=app.state.store.get(run['id'])
    assert saved['status']=='succeeded' and saved['results'][0]['summary']=='NEW_REPLY'
    assert saved['usage']['input_tokens']==5 and saved['calls']==2
    assert len(saved['accounted_attempts'])==2
    messages=client.get(f"/api/conversations/{conv['id']}").json()['messages']
    assert 'OLD_REPLY' not in str(messages)


def test_model_usage_is_retained_when_acl_changes_before_commit(setup):
    app,harness,client,_,_=setup
    conv=conversation(client,'meeting')
    owner=client.get('/api/state').json()['user']['id']
    def generate(context):
        with app.state.store.tx() as conn:
            conn.execute('UPDATE users SET household=? WHERE id=?',('changed-family',owner))
        return AgentResult(summary='DO_NOT_PUBLISH').model_dump(),{'provider':'demo','input_tokens':7}
    harness.gateway.generate=generate
    run=send(client,conv,content='解释家庭会议是什么')
    assert harness.tick()
    saved=app.state.store.get(run['id'])
    assert saved['status']=='failed' and not saved['results'] and saved['usage']['input_tokens']==7
