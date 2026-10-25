import copy
import pytest
from services.api.app.context import POLICY, measure, pack_context
from services.api.app.memory import retrieve, references, terms
from tests.test_system import setup, conversation, send, drain


def test_lexical_normalization():
    assert 'python' in terms('学习 ＰＹＴＨＯＮ')
    assert '英语' in terms('英语学习')
    assert not terms('帮我 please')


def test_retrieval_finds_old_relevant_record_and_omits_recent_noise(setup):
    app, _, client, _, _ = setup
    relevant = client.post('/api/memories', json={'content': '英语练习偏好：每天早上练习口语'}).json()
    for index in range(8):
        client.post('/api/memories', json={'content': f'咖啡口味记录{index}：不加糖'})
    user = client.get('/api/state').json()['user']
    with app.state.store.tx() as conn:
        selected, receipt = retrieve(app.state.store, user, {'scope': 'private', 'mode': 'planning', 'use_memory': True}, '英语口语', conn)
    assert [m['id'] for m in selected] == [relevant['id']]
    assert receipt['reason'] == 'matched'
    assert relevant['id'] not in [m['id'] for m in app.state.store.all('memory')[-6:]]


@pytest.mark.parametrize('mode,scope,enabled', [('planning', 'private', False), ('support', 'private', True), ('meeting', 'family', True)])
def test_disabled_spaces_do_not_query_memory_store(setup, monkeypatch, mode, scope, enabled):
    app, _, client, _, _ = setup
    user = client.get('/api/state').json()['user']
    monkeypatch.setattr(app.state.store, 'private_memories', lambda *args: pytest.fail('must not query memory'))
    with app.state.store.tx() as conn:
        selected, receipt = retrieve(app.state.store, user, {'scope': scope, 'mode': mode, 'use_memory': enabled}, '英语', conn)
    assert not selected and receipt['selected'] == 0


def test_authorization_precedes_scoring_and_no_match_is_empty(setup):
    app, _, client, other, _ = setup
    other.post('/api/memories', json={'content': '英语私密标记 OTHER_PRIVATE'})
    user = client.get('/api/state').json()['user']
    with app.state.store.tx() as conn:
        app.state.store.put('memory', {'id': 'mem_wrong_scope', 'owner_id': user['id'], 'scope': 'family', 'content': '英语错误范围'}, conn)
        selected, _ = retrieve(app.state.store, user, {'scope': 'private', 'mode': 'planning', 'use_memory': True}, '英语', conn)
    assert not selected


def test_sources_reflect_sent_memory_and_delete_without_copy(setup):
    app, harness, client, _, outsider = setup
    memory = client.post('/api/memories', json={'content': '英语口语每天练习20分钟'}).json()
    conv = conversation(client, 'planning', use_memory=True)
    run = drain(harness, client, send(client, conv, content='请给我英语口语建议'))
    detail = client.get(f"/api/conversations/{conv['id']}").json()
    message = next(m for m in detail['messages'] if m['actor_type'] == 'agent')
    assert message['memory_sources'][0]['content'] == memory['content']
    assert message['memory_sources'][0]['source'] == '用户主动保存'
    stored = app.state.store.get(message['id'])
    assert memory['content'] not in str(stored['memory_sources'])
    assert outsider.get(f"/api/conversations/{conv['id']}").status_code == 404
    client.delete(f"/api/memories/{memory['id']}")
    detail = client.get(f"/api/conversations/{conv['id']}").json()
    source = next(m for m in detail['messages'] if m['actor_type'] == 'agent')['memory_sources'][0]
    assert source['status'] == 'unavailable' and 'content' not in source
    captured = []
    original = harness.gateway.generate
    harness.gateway.generate = lambda context: (captured.append(copy.deepcopy(context)) or original(context))
    drain(harness, client, send(client, conv, content='继续给我英语建议'))
    assert not captured[0]['memories']


def test_changed_source_is_not_presented_as_original(setup):
    app, harness, client, _, _ = setup
    memory = client.post('/api/memories', json={'content': '英语口语原有偏好'}).json()
    conv = conversation(client, 'planning', use_memory=True)
    drain(harness, client, send(client, conv, content='英语口语建议'))
    with app.state.store.tx() as conn:
        app.state.store.put('memory', {**memory, 'content': '变更后的内容'}, conn)
    source = client.get(f"/api/conversations/{conv['id']}").json()['messages'][-1]['memory_sources'][0]
    assert source['status'] == 'changed' and 'content' not in source


def test_budget_drops_lowest_ranked_memories_first():
    memories = [{'id': f'mem_{i}', 'content': '英语' * 100, 'retrieval_score': 3-i} for i in range(3)]
    context = {'prompt': '英语', 'memories': memories}
    limit = measure({**context, 'memories': memories[:1]}, '')['input_chars']
    packed, receipt = pack_context(context, '', {**POLICY, 'max_input_chars': limit})
    assert [m['id'] for m in packed['memories']] == ['mem_0']
    assert receipt['dropped']['memories'] == 2
    assert [r['id'] for r in references(packed['memories'])] == ['mem_0']


def test_labelled_retrieval_regression():
    from evals.memory import evaluate
    rows = evaluate()
    assert len(rows) == 9 and all(row['lexical_exact'] for row in rows)
    assert not next(row for row in rows if row['case_id'] == 'old-english')['recent6_exact']
