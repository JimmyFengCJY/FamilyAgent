"""Demo-only container smoke, then verify the same data after a Compose restart."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import time
import urllib.request
from urllib.parse import urlparse
import uuid

PASSWORD = 'synthetic-container-password'


def main():
    parser = argparse.ArgumentParser(description='仅对回环地址 demo 容器执行合成验证')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.username or parsed.password:
        parser.error('Only local HTTP container endpoints are supported')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(path, data=None, method=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(args.base_url + path, body, {'Content-Type': 'application/json'}, method=method)
        with opener.open(req, timeout=20) as response:
            return json.load(response)
    health = request('/api/health')
    assert health['provider'] == 'demo' and health['demo_login'] is False
    assert health['runtime_revision'] and health['model_config']['provider'] == 'demo'
    with opener.open(args.base_url + '/', timeout=10) as response:
        html = response.read().decode()
    asset = re.search(r'src="(/assets/[^\"]+\.js)"', html)
    assert asset, 'Production web assets must be present'
    with opener.open(args.base_url + asset[1], timeout=10) as response:
        assert response.status == 200
    if args.verify:
        saved = json.loads(args.state.read_text())
        request('/api/auth/login', {'email': saved['email'], 'password': PASSWORD})
        detail = request('/api/conversations/' + saved['conversation_id'] + '?compact=true')
        assert any(r['id'] == saved['run_id'] and r['status'] == 'succeeded' for r in detail['runs'])
        assert len(detail['messages']) == 2
        assert any(t['id'] == saved['task_id'] for t in request('/api/state?compact=true')['tasks'])
        assert health['runtime_revision'] == saved['runtime_revision']
        print('PASS: service restart preserves account, messages, run and task')
        return
    assert not args.state.exists(), 'Never overwrite previous smoke state'
    email = 'container-' + uuid.uuid4().hex + '@example.test'
    request('/api/auth/register', {'name': '合成容器验证', 'email': email, 'password': PASSWORD})
    conv = request('/api/conversations', {'title': '容器合成验证', 'mode': 'assistant'})
    run = request('/api/conversations/' + conv['id'] + '/messages', {'content': '解释时间管理，只交流不创建任务', 'intent': 'question'})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = request('/api/runs/' + run['id'])
        if current['status'] == 'succeeded':
            break
        assert current['status'] in {'queued', 'running'}, 'Synthetic run failed'
        time.sleep(.2)
    assert current['status'] == 'succeeded', 'Standalone worker did not complete run'
    probe = request('/api/mcp/reference/probe', {'query': '目标拆解', 'limit': 1})
    assert probe['transport'] == 'stdio' and probe['hits']
    task = request('/api/tasks', {'title': '合成持久化检查'})
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps({'email': email, 'conversation_id': conv['id'], 'run_id': run['id'],
        'task_id': task['id'], 'runtime_revision': health['runtime_revision']}, indent=2) + '\n')
    print('PASS: production assets, registration, standalone worker, MCP stdio, task write')


if __name__ == '__main__':
    main()
