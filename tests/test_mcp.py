import copy
import json
import os
import sys
import time
import anyio
import pytest
from mcp import StdioServerParameters
from services.api.app.mcp_client import McpFailure, exchange, search_reference, server_parameters
from services.api.app.schemas import AgentResult
from tests.test_system import setup, conversation, send, drain


def test_real_sdk_stdio_discovery_and_call():
    result = search_reference({'query': '目标拆解', 'limit': 2})
    assert result['protocol'] == '2025-11-25' and result['transport'] == 'stdio'
    assert result['discovered_tools'] == ['search_reference']
    assert result['hits'][0]['source'] == 'familyagent://reference/goal'
    assert search_reference({'query': '完全无关火星', 'limit': 1})['hits'] == []


def test_reference_tool_through_harness(setup):
    _, harness, client, _, _ = setup
    captured = []
    def generate(context):
        captured.append(copy.deepcopy(context))
        if not context['tool_results']:
            return {'tool_request': {'name': 'reference.search', 'arguments': {'query': '目标拆解', 'limit': 1}}}, {'provider': 'demo'}
        return AgentResult(summary='已读取内置目标参考资料').model_dump(), {'provider': 'demo'}
    harness.gateway.generate = generate
    run = drain(harness, client, send(client, conversation(client)))
    assert run['status'] == 'succeeded' and run['calls'] == 2
    assert run['tool_results'][0]['result']['hits'][0]['id'] == 'goal'
    events = client.get(f"/api/runs/{run['id']}/events").json()
    assert any(e['type'] == 'tool.completed' and e['data']['transport'] == 'stdio' for e in events)


@pytest.mark.parametrize('mode', ['support', 'finance'])
def test_reference_authorization_precedes_process_launch(setup, monkeypatch, mode):
    _, harness, client, _, _ = setup
    monkeypatch.setattr('services.api.app.tool_registry.search_reference', lambda data: pytest.fail('must not launch'))
    harness.gateway.generate = lambda context: ({'tool_request': {'name': 'reference.search', 'arguments': {'query': '目标拆解'}}}, {'provider': 'demo'})
    run = drain(harness, client, send(client, conversation(client, mode)))
    assert run['status'] == 'failed' and not run['tool_results']


def test_probe_requires_auth_and_rejects_command_arguments(setup, monkeypatch):
    app, _, client, _, _ = setup
    from fastapi.testclient import TestClient
    assert TestClient(app).post('/api/mcp/reference/probe', json={'query': '目标拆解'}).status_code == 401
    monkeypatch.setattr('services.api.app.tool_registry.search_reference', lambda data: pytest.fail('bad args must not launch'))
    assert client.post('/api/mcp/reference/probe', json={'query': '目标拆解', 'command': 'arbitrary'}).status_code == 422


def fake_server(tmp_path, mode):
    script = tmp_path / ('fake_' + mode + '.py')
    script.write_text('''import sys,json,os,time
mode = MODE
for line in sys.stdin:
 request=json.loads(line)
 if 'id' not in request: continue
 method=request['method']
 if method=='initialize':
  result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'familyagent-reference','version':'test'}}
  if mode=='version': result['protocolVersion']='2099-01-01'
 elif method=='tools/list':
  result={'tools':[{'name':'search_reference','inputSchema':{'type':'object','properties':{'query':{'type':'string'},'limit':{'type':'integer'}},'required':['query']},'outputSchema':{'type':'object'}}]}
  if mode=='discovery': result['tools']=[]
 elif method=='tools/call':
  hit={'id':'goal','title':'synthetic','excerpt':'test','source':'familyagent://reference/goal'}
  if mode=='output': hit['source']='https://untrusted.invalid'
  if mode=='environment': hit['excerpt']='|'.join(os.getenv(key,'absent') for key in ['GEMINI_API_KEY','FAMILYAGENT_DB','ANTHROPIC_API_KEY'])
  result={'content':[],'structuredContent':{'hits':[hit]},'isError':False}
  if mode=='error': result={'content':[{'type':'text','text':'synthetic internal secret'}],'isError':True}
  if mode=='oversize': result['structuredContent']={'hits':[], 'extra':'x'*17000}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
'''.replace('MODE', repr(mode)))
    return StdioServerParameters(command=sys.executable, args=[str(script)], cwd=str(tmp_path))


@pytest.mark.parametrize('mode', ['version', 'discovery', 'output', 'error', 'oversize'])
def test_malformed_servers_are_not_treated_as_success(tmp_path, mode):
    with pytest.raises(McpFailure) as exc:
        anyio.run(exchange, fake_server(tmp_path, mode), {'query': '目标拆解', 'limit': 1}, 3)
    assert 'internal secret' not in str(exc.value) and 'untrusted.invalid' not in str(exc.value)


def test_child_does_not_inherit_app_credentials(tmp_path, monkeypatch):
    for key in ['GEMINI_API_KEY', 'FAMILYAGENT_DB', 'ANTHROPIC_API_KEY']:
        monkeypatch.setenv(key, 'synthetic-secret')
    result = anyio.run(exchange, fake_server(tmp_path, 'environment'), {'query': '目标拆解', 'limit': 1}, 3)
    assert result['hits'][0]['excerpt'] == 'absent|absent|absent'
    parameters = server_parameters()
    assert '-m' not in parameters.args  # fixed absolute bundled script, no shell
    assert parameters.env == {'PYTHONIOENCODING': 'utf-8'}


def test_timeout_terminates_child(tmp_path):
    pidfile = tmp_path / 'pid'
    script = tmp_path / 'stall.py'
    script.write_text(f"import os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
    parameters = StdioServerParameters(command=sys.executable, args=[str(script)], cwd=str(tmp_path))
    started=time.monotonic()
    with pytest.raises(McpFailure):
        anyio.run(exchange, parameters, {'query': '目标拆解', 'limit': 1}, .5)
    assert time.monotonic()-started < 5
    if pidfile.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)


def test_transport_failure_is_recorded_without_tool_result(setup, monkeypatch):
    _, harness, client, _, _ = setup
    def fail(data):
        raise McpFailure('MCP 调用超时，连接已结束；未自动重试')
    monkeypatch.setattr('services.api.app.tool_registry.search_reference', fail)
    harness.gateway.generate = lambda context: ({'tool_request': {'name': 'reference.search', 'arguments': {'query': '目标拆解'}}}, {'provider': 'demo'})
    run=drain(harness,client,send(client,conversation(client)))
    assert run['status']=='failed' and not run['tool_results']
    events=client.get(f"/api/runs/{run['id']}/events").json()
    assert any(e['type']=='tool.failed' for e in events)
