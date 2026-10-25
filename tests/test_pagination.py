"""Pagination must preserve conversation ACLs, insertion order and bounded payloads."""
from tests.test_system import setup, conversation


def seed_messages(app, conv_id, start, end):
    with app.state.store.tx() as conn:
        for i in range(start, end):
            app.state.store.put('message', {'id': f'msg_page_{conv_id}_{i}', 'conversation_id': conv_id,
                'actor_type': 'human', 'actor_name': '合成用户', 'content': str(i), 'created_at': 'synthetic'}, conn)


def test_backward_pages_and_concurrent_append(setup):
    app, _, user, _, outsider = setup
    conv = conversation(user)
    seed_messages(app, conv['id'], 0, 125)
    base = f"/api/conversations/{conv['id']}"
    first = user.get(base + '?compact=true').json()
    assert [int(m['content']) for m in first['messages']] == list(range(75, 125))
    assert first['page']['has_more']
    # A concurrent append cannot shift the insertion-ordered older cursor.
    seed_messages(app, conv['id'], 125, 128)
    older = user.get(base + '/messages', params={'before': first['page']['oldest']}).json()
    oldest = user.get(base + '/messages', params={'before': older['page']['oldest']}).json()
    assert [int(m['content']) for m in older['messages']] == list(range(25, 75))
    assert [int(m['content']) for m in oldest['messages']] == list(range(25))
    assert not oldest['page']['has_more']
    new = user.get(base + '/messages', params={'after': first['page']['newest']}).json()
    assert [int(m['content']) for m in new['messages']] == [125, 126, 127]
    assert not new['page']['has_more']
    assert outsider.get(base + '/messages').status_code == 404
    assert outsider.get(base + '?compact=true').status_code == 404


def test_forward_backlog_drains_without_gaps(setup):
    app, _, user, _, _ = setup
    conv = conversation(user)
    seed_messages(app, conv['id'], 0, 131)
    cursor = f"msg_page_{conv['id']}_0"
    seen = []
    while True:
        result = user.get(f"/api/conversations/{conv['id']}?compact=true", params={'after': cursor, 'compact': 'true'}).json()
        seen += [int(m['content']) for m in result['messages']]
        cursor = result['page']['newest'] or cursor
        if not result['page']['has_more']:
            break
    assert seen == list(range(1, 131))
    empty = user.get(f"/api/conversations/{conv['id']}/messages", params={'after': cursor, 'compact': 'true'}).json()
    assert empty['messages'] == [] and not empty['page']['has_more']


def test_invalid_and_cross_conversation_cursors(setup):
    app, _, user, _, _ = setup
    a, b = conversation(user), conversation(user)
    seed_messages(app, b['id'], 0, 1)
    base = f"/api/conversations/{a['id']}/messages"
    for params in ({'limit': 0}, {'limit': 101}, {'limit': 'x'}, {'before': 'missing'},
                   {'before': f"msg_page_{b['id']}_0"}, {'before': 'x', 'after': 'y'}):
        assert user.get(base, params=params).status_code == 422
    assert user.get(base).json()['messages'] == []


def test_summary_omits_heavy_payload_and_latest_run_only(setup):
    app, _, user, _, outsider = setup
    conv = conversation(user)
    with app.state.store.tx() as conn:
        for i in range(3):
            app.state.store.put('run', {'id': f'run_page_{i}', 'conversation_id': conv['id'], 'status': 'succeeded',
                'owner_id': conv['owner_id'], 'results': [{'summary': 'synthetic-heavy-' * 1000}], 'prompt': 'synthetic', 'steps': []}, conn)
    compact = user.get('/api/state?compact=true').json()
    assert len(compact['runs']) == 3
    assert all('results' not in r and 'prompt' not in r and 'steps' not in r for r in compact['runs'])
    assert outsider.get('/api/state?compact=true').json()['runs'] == []
    assert len(user.get('/api/state').json()['runs']) == 3
    detail = user.get(f"/api/conversations/{conv['id']}?compact=true").json()
    assert [r['id'] for r in detail['runs']] == ['run_page_2']
    assert user.get('/api/runs/run_page_0').json()['results']
    assert outsider.get('/api/runs/run_page_0').status_code == 404


def test_source_refresh_rechecks_deletion_and_conversation_binding(setup):
    from services.api.app.memory import fingerprint
    app, _, user, _, outsider = setup
    conv = conversation(user, mode='planning', use_memory=True)
    other = conversation(user)
    memory = user.post('/api/memories', json={'content': '合成学习约束'}).json()
    with app.state.store.tx() as conn:
        app.state.store.put('message', {'id': 'msg_source_page', 'conversation_id': conv['id'],
            'memory_sources': [{'id': memory['id'], 'content_sha256': fingerprint(memory['content']), 'score': 1}]}, conn)
    base = f"/api/conversations/{conv['id']}/messages/msg_source_page/sources"
    assert user.get(base).json()[0]['status'] == 'available'
    assert outsider.get(base).status_code == 404
    assert user.get(f"/api/conversations/{other['id']}/messages/msg_source_page/sources").status_code == 404
    assert user.delete('/api/memories/' + memory['id']).status_code == 200
    result = user.get(base).json()[0]
    assert result['status'] == 'unavailable' and 'content' not in result
