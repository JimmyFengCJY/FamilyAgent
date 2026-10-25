import copy
import pytest
from services.api.app.context import ContextOverflow, POLICY, measure, pack_context
from services.api.app.db import now
from tests.test_system import setup, conversation, send, drain


def test_packing_preserves_critical_data_and_does_not_mutate():
    context = {'prompt': '预算不能超过8000', 'constraints': ['每天最多一个景点'],
               'character': {'id': 'finance'}, 'intent': {'name': 'discussion'},
               'expert_findings': [{'source_id': 'expert_1', 'result': {'summary': '逐字原文'}}],
               'tool_results': [{'name': 'task.list', 'result': ['真实工具输出']}],
               'finance_result': {'remaining': '100.00'},
               'history': [{'content': '旧消息' * 1000}, {'content': '较新消息'}],
               'memories': [{'content': '可选记忆' * 100}], 'prior_results': [{'summary': '旧结果' * 100}]}
    original = copy.deepcopy(context)
    packed, receipt = pack_context(context, 'system', {**POLICY, 'max_input_chars': 700})
    assert context == original
    for key in ('prompt', 'constraints', 'character', 'intent', 'expert_findings', 'tool_results', 'finance_result'):
        assert packed[key] == original[key]
    assert receipt['input_chars'] <= 700
    assert receipt['dropped']['history'] == 2
    assert receipt['dropped']['memories'] == 1
    assert pack_context(context, 'system', {**POLICY, 'max_input_chars': 700}) == (packed, receipt)


def test_system_and_serialization_are_included_in_budget():
    context = {'prompt': '你好😀"\n'}
    exact = measure(context, '系统规则')['input_chars']
    assert pack_context(context, '系统规则', {**POLICY, 'max_input_chars': exact})[1]['input_chars'] == exact
    with pytest.raises(ContextOverflow):
        pack_context(context, '系统规则', {**POLICY, 'max_input_chars': exact - 1})


def test_long_history_keeps_explicit_constraints_and_snapshot(setup):
    app, harness, client, _, _ = setup
    conv = conversation(client, constraints=['预算上限8000元', '周日必须返程'])
    with app.state.store.tx() as conn:
        for index in range(12):
            app.state.store.put('message', {'id': f'msg_long_{index}', 'conversation_id': conv['id'],
                'actor_type': 'human', 'actor_name': '用户', 'content': '历史信息' * 500,
                'created_at': now()}, conn)
    run = send(client, conv, content='请给我一些旅行建议')
    with app.state.store.tx() as conn:
        row = app.state.store.get(run['id'], conn)
        row['context_policy']['max_input_chars'] = 6000
        app.state.store.put('run', row, conn)
        changed = app.state.store.get(conv['id'], conn)
        changed['constraints'] = ['后来被修改的约束']
        app.state.store.put('conversation', changed, conn)
    captured = []
    original = harness.gateway.generate
    def generate(context):
        captured.append(context)
        return original(context)
    harness.gateway.generate = generate
    run = drain(harness, client, run)
    assert run['status'] == 'succeeded'
    assert captured[0]['constraints'] == conv['constraints']
    events = client.get(f"/api/runs/{run['id']}/events").json()
    receipt = next(e['data'] for e in events if e['type'] == 'context.prepared')
    assert receipt['input_chars'] <= 6000 and receipt['dropped']['history'] > 0
    assert '预算上限8000元' not in str(receipt)


def test_critical_overflow_does_not_call_model_or_spend_call_budget(setup):
    app, harness, client, _, _ = setup
    conv = conversation(client, constraints=['必须保留的约束'])
    run = send(client, conv)
    with app.state.store.tx() as conn:
        row = app.state.store.get(run['id'], conn)
        row['context_policy']['max_input_chars'] = 10
        app.state.store.put('run', row, conn)
    def forbidden(context):
        pytest.fail('overflow must fail before model invocation')
    harness.gateway.generate = forbidden
    run = drain(harness, client, run)
    assert run['status'] == 'failed' and run['calls'] == 0
    assert not run['results']
    events = client.get(f"/api/runs/{run['id']}/events").json()
    assert any(e['type'] == 'context.rejected' and e['data']['model_called'] is False for e in events)
    assert not any(e['type'] == 'agent.started' for e in events)


@pytest.mark.parametrize('constraints', [['x'] * 9, ['x' * 201], ['   ']])
def test_constraint_api_bounds(setup, constraints):
    _, _, client, _, _ = setup
    assert client.post('/api/conversations', json={'title': '验证', 'mode': 'assistant', 'constraints': constraints}).status_code == 422
