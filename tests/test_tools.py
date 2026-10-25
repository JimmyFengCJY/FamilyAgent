import copy
import pytest
from services.api.app.tool_registry import ToolContext, registry, ToolDenied, ToolArgumentsError
from services.api.app.schemas import AgentResult
from tests.test_system import setup, conversation, send, drain


def model_gateway(harness, request):
    captured = []
    def generate(context):
        captured.append(copy.deepcopy(context))
        if not context['tool_results']:
            return {'tool_request': request}, {'provider': 'demo'}
        return AgentResult(summary='已完成工具读取').model_dump(), {'provider': 'demo'}
    harness.gateway.generate = generate
    return captured


def test_tool_catalog_requires_login_and_does_not_grant_write(setup):
    app, _, client, _, _ = setup
    from fastapi.testclient import TestClient
    assert TestClient(app).get('/api/tools').status_code == 401
    tools = client.get('/api/tools').json()['tools']
    assert {t['name'] for t in tools} == {'task.list', 'finance.calculate', 'task.create_batch', 'reference.search'}
    batch = next(t for t in tools if t['name'] == 'task.create_batch')
    assert batch['requires_approval'] and batch['origins'] == ['approval']
    assert batch['input_schema']['additionalProperties'] is False


def test_task_list_filters_status_limit_and_owner(setup):
    _, harness, client, other, _ = setup
    client.post('/api/tasks', json={'title': '本人待办A'})
    client.post('/api/tasks', json={'title': '本人待办B'})
    finished = client.post('/api/tasks', json={'title': '已完成任务'}).json()
    client.patch(f"/api/tasks/{finished['id']}", json={'status': 'done'})
    other.post('/api/tasks', json={'title': 'OTHER_PRIVATE_TASK'})
    contexts = model_gateway(harness, {'name': 'task.list', 'arguments': {'status': 'todo', 'limit': 1}})
    run = drain(harness, client, send(client, conversation(client)))
    assert run['status'] == 'succeeded' and run['calls'] == 2
    assert run['tool_results'][0]['result'] == [{'title': '本人待办A', 'status': 'todo'}]
    assert 'OTHER_PRIVATE_TASK' not in str(contexts)
    assert [t['name'] for t in contexts[0]['tools']] == ['task.list', 'reference.search']
    assert all(t['input_schema']['additionalProperties'] is False for t in contexts[0]['tools'])


@pytest.mark.parametrize('arguments', [{'owner_id': 'demo-lin'}, {'limit': 31}, {'limit': '1'}, {'limit': True}, {'status': 'unknown'}])
def test_bad_arguments_fail_without_data_or_argument_echo(setup, arguments):
    _, harness, client, _, _ = setup
    model_gateway(harness, {'name': 'task.list', 'arguments': arguments})
    run = drain(harness, client, send(client, conversation(client)))
    assert run['status'] == 'failed' and not run['tool_results']
    assert 'Schema' in run['error']
    assert str(arguments) not in run['error']


@pytest.mark.parametrize('mode', ['meeting', 'support', 'finance'])
def test_unauthorized_task_tool_hidden_and_execution_denied(setup, mode):
    _, harness, client, _, _ = setup
    contexts = model_gateway(harness, {'name': 'task.list'})
    run = drain(harness, client, send(client, conversation(client, mode)))
    assert run['status'] == 'failed'
    assert 'task.list' not in [t['name'] for t in contexts[0]['tools']]
    assert not run['tool_results']


@pytest.mark.parametrize('tool_request', [
    {'name': 'task.create_batch', 'arguments': {'actions': ['越权任务'], 'approved': True}},
    {'name': 'unknown', 'arguments': {}},
    {'name': ['invalid']},
])
def test_model_cannot_call_write_or_unknown_tool(setup, tool_request):
    _, harness, client, _, _ = setup
    model_gateway(harness, tool_request)
    run = drain(harness, client, send(client, conversation(client)))
    assert run['status'] == 'failed' and not client.get('/api/state').json()['tasks']


def test_model_finance_tool_uses_registered_schema(setup):
    _, harness, client, _, _ = setup
    contexts = model_gateway(harness, {'name': 'finance.calculate', 'arguments': {'income': '0.30', 'items': [{'name': 'a', 'amount': '0.10'}, {'name': 'b', 'amount': '0.20'}]}})
    run = drain(harness, client, send(client, conversation(client, 'finance')))
    assert run['status'] == 'succeeded'
    assert run['tool_results'][0]['result']['remaining'] == '0.00'
    assert [t['name'] for t in contexts[0]['tools']] == ['finance.calculate']


def test_tool_limit_remains_bounded(setup):
    _, harness, client, _, _ = setup
    harness.gateway.generate = lambda context: ({'tool_request': {'name': 'task.list'}}, {'provider': 'demo'})
    run = drain(harness, client, send(client, conversation(client)))
    assert run['status'] == 'failed' and run['tool_requests'] == 2 and run['calls'] == 3


def test_write_tool_rechecks_saved_approval_and_exact_actions(setup):
    app, harness, client, _, _ = setup
    conv = conversation(client)
    run = drain(harness, client, send(client, conv, content='创建三个旅行待办'))
    user = client.get('/api/state').json()['user']
    with app.state.store.tx() as conn:
        ctx = ToolContext(app.state.store, conn, user, 'approval', run=run)
        with pytest.raises(ToolDenied):
            registry.execute('task.create_batch', {'actions': run['proposal']['actions']}, ctx)
        approved = copy.deepcopy(run)
        approved['approvals'][user['id']] = True
        app.state.store.put('run', approved, conn)
        ctx = ToolContext(app.state.store, conn, user, 'approval', run=approved)
        with pytest.raises(ToolDenied):
            registry.execute('task.create_batch', {'actions': ['不同于草案']}, ctx)
        app.state.store.put('run', {**approved, 'status': 'cancelled'}, conn)
        with pytest.raises(ToolDenied):
            registry.execute('task.create_batch', {'actions': approved['proposal']['actions']}, ctx)
    assert not client.get('/api/state').json()['tasks']


def test_approval_write_failure_rolls_back_all_tasks(setup, monkeypatch):
    app, harness, client, _, _ = setup
    run = drain(harness, client, send(client, conversation(client), content='创建三个旅行待办'))
    original = app.state.store.put
    writes = []
    def fail_second(kind, value, conn):
        if kind == 'task':
            writes.append(value['id'])
            if len(writes) == 2:
                raise RuntimeError('synthetic internal secret')
        return original(kind, value, conn)
    monkeypatch.setattr(app.state.store, 'put', fail_second)
    response = client.post(f"/api/decisions/{run['id']}/acceptances", json={'version': run['version']})
    assert response.status_code == 500 and 'secret' not in response.text
    assert not client.get('/api/state').json()['tasks']
    saved = app.state.store.get(run['id'])
    assert saved['status'] == 'waiting_approval' and not saved['approvals']


def test_finance_nested_unknown_arguments_are_rejected(setup):
    _, harness, client, _, _ = setup
    model_gateway(harness, {'name': 'finance.calculate', 'arguments': {'income': '100', 'items': [{'name': 'a', 'amount': '10', 'owner_id': 'spoof'}]}})
    run = drain(harness, client, send(client, conversation(client, 'finance')))
    assert run['status'] == 'failed' and not run['tool_results']


def test_action_title_matches_task_write_contract():
    from pydantic import ValidationError
    from services.api.app.schemas import AgentResult
    assert AgentResult(summary='草案', suggested_actions=['a' * 150])
    with pytest.raises(ValidationError):
        AgentResult(summary='草案', suggested_actions=['a' * 151])


def test_unknown_tool_policy_fails_before_generation(setup):
    app, harness, client, _, _ = setup
    run = send(client, conversation(client))
    with app.state.store.tx() as conn:
        saved = app.state.store.get(run['id'], conn)
        saved['tool_policy'] = 'unavailable-version'
        app.state.store.put('run', saved, conn)
    harness.gateway.generate = lambda context: pytest.fail('must not call model')
    run = drain(harness, client, run)
    assert run['status'] == 'failed' and run['calls'] == 0
