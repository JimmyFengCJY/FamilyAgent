import copy
import pytest
from services.api.app.schemas import AgentResult
from services.api.app.harness import validate_discussion
from services.api.app.models import InvalidModelOutput
from tests.test_system import setup, conversation, send, drain


@pytest.mark.parametrize('strategy,isolated', [('independent', True), ('multi', False)])
def test_first_round_isolation_covers_history_and_results(setup, strategy, isolated):
    _, harness, client, _, _ = setup
    captured = []
    original = harness.gateway.generate
    def generate(context):
        captured.append(copy.deepcopy(context))
        result, usage = original(context)
        if context['stage'] == 'expert':
            result['summary'] += ' EXPERT_MARKER_' + context['character']['id']
        return result, usage
    harness.gateway.generate = generate
    conv = conversation(client, 'meeting', orchestration=strategy)
    run = drain(harness, client, send(client, conv))
    assert run['status'] == 'waiting_approval' and run['calls'] == 5
    experts = [c for c in captured if c['stage'] == 'expert']
    assert len(experts) == 3
    if isolated:
        assert all(not c['prior_results'] and not c['expert_findings'] for c in experts)
        assert all('EXPERT_MARKER_' not in str(c['history']) for c in experts)
    else:
        assert 'EXPERT_MARKER_' in str(experts[-1]['history'])
        assert len(experts[-1]['prior_results']) == 3
    summary = captured[-1]
    assert len(summary['expert_findings']) == 3
    assert all('EXPERT_MARKER_' in f['result']['summary'] for f in summary['expert_findings'])
    assert run['results'][-1]['disagreements']
    assert len(run['results'][-1]['expert_sources']) == 3
    assert not client.get('/api/state').json()['tasks']


def test_independent_resume_preserves_completed_steps_and_isolation(setup):
    _, harness, client, _, _ = setup
    conv = conversation(client, 'meeting', orchestration='independent')
    run = drain(harness, client, send(client, conv, max_calls=2))
    assert run['status'] == 'exhausted' and run['cursor'] == 2
    contexts = []
    original = harness.gateway.generate
    def generate(context):
        contexts.append(copy.deepcopy(context))
        return original(context)
    harness.gateway.generate = generate
    client.post(f"/api/runs/{run['id']}/resume", json={'max_calls': 8})
    run = drain(harness, client, run)
    assert run['calls'] == 5 and run['cursor'] == 5
    assert len(contexts[-1]['expert_findings']) == 3
    assert all(not c['prior_results'] for c in contexts if c['stage'] == 'expert')


@pytest.mark.parametrize('positions', [
    [{'source_id': 'missing', 'quote': '原文'}, {'source_id': 'expert_2', 'quote': '另一观点'}],
    [{'source_id': 'expert_1', 'quote': '伪造观点'}, {'source_id': 'expert_2', 'quote': '另一观点'}],
    [{'source_id': 'expert_1', 'quote': '原文'}, {'source_id': 'expert_1', 'quote': '原文'}],
])
def test_summary_rejects_invented_or_duplicate_attribution(positions):
    result = AgentResult(summary='汇总', disagreements=[{'topic': '取舍', 'positions': positions, 'question': '优先哪个？'}]).model_dump()
    context = {'stage': 'summary', 'expert_findings': [
        {'source_id': 'expert_1', 'result': {'summary': '原文'}},
        {'source_id': 'expert_2', 'result': {'summary': '另一观点'}}]}
    with pytest.raises(InvalidModelOutput):
        validate_discussion(result, context)


def test_invalid_attribution_retries_with_usage_accounted(setup):
    _, harness, client, _, _ = setup
    original = harness.gateway.generate
    attempts = []
    def generate(context):
        result, usage = original(context)
        if context['stage'] == 'summary':
            attempts.append(1)
            if len(attempts) == 1:
                result['disagreements'][0]['positions'][0]['quote'] = 'FABRICATED_QUOTE'
                usage['input_tokens'] = 123
        return result, usage
    harness.gateway.generate = generate
    run = drain(harness, client, send(client, conversation(client, 'meeting')))
    assert run['status'] == 'waiting_approval' and run['calls'] == 6
    assert len(attempts) == 2 and run['usage']['input_tokens'] >= 123
    assert 'FABRICATED_QUOTE' not in str(run['results'])


def test_single_expert_does_not_manufacture_disagreement(setup):
    _, harness, client, _, _ = setup
    run = drain(harness, client, send(client, conversation(client, 'meeting', character_ids=['planner'])))
    assert run['calls'] == 3
    assert not run['results'][-1]['disagreements']
