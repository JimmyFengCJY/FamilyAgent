import json
import httpx
import pytest
from services.api.app.models import ModelGateway, InvalidModelOutput, TemporaryModelError
from services.api.app.schemas import AgentResult


def mock(monkeypatch, provider, payload, status=200):
    for name in ("ANTHROPIC", "DEEPSEEK", "GEMINI"):
        monkeypatch.setenv(f"{name}_API_KEY", "synthetic-key-never-log")
    client = httpx.Client
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=payload)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    return ModelGateway(provider, "test-model"), requests


def result():
    return json.dumps(AgentResult(summary="结构化结果").model_dump(), ensure_ascii=False)


def test_deepseek_native_contract_and_usage(monkeypatch):
    payload = {"choices": [{"message": {"content": result(), "reasoning_content": "不应记录的思维"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 100, "completion_tokens": 50, "prompt_cache_hit_tokens": 20}}
    gateway, requests = mock(monkeypatch, "deepseek", payload)
    output, usage = gateway.generate({"prompt": "测试"})
    request = requests[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["Authorization"] == "Bearer synthetic-key-never-log"
    assert body["messages"][0]["role"] == "system"
    assert body["response_format"] == {"type": "json_object"}
    assert output["summary"] == "结构化结果"
    assert usage["input_tokens"] == 100 and usage["output_tokens"] == 50
    assert usage["cache_read_input_tokens"] == 20
    assert "不应记录的思维" not in json.dumps((output, usage))


def test_gemini_native_contract_usage_and_hidden_thoughts(monkeypatch):
    payload = {"candidates": [{"content": {"parts": [{"text": "隐藏思维", "thought": True}, {"text": result()}]}, "finishReason": "STOP"}],
               "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 30, "thoughtsTokenCount": 20, "cachedContentTokenCount": 10}}
    gateway, requests = mock(monkeypatch, "gemini", payload)
    output, usage = gateway.generate({"prompt": "测试"})
    request = requests[0]
    assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
    assert "key=" not in str(request.url)
    assert request.headers["x-goog-api-key"] == "synthetic-key-never-log"
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert usage["output_tokens"] == 50 and usage["reasoning_tokens"] == 20
    assert "隐藏思维" not in json.dumps((output, usage))


def test_anthropic_total_input_includes_separately_reported_cache(monkeypatch):
    gateway, _ = mock(monkeypatch, "anthropic", {"content": [{"type": "text", "text": result()}],
        "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 30}})
    _, usage = gateway.generate({})
    assert usage["input_tokens"] == 150
    assert usage["provider_usage"]["input_tokens"] == 100


@pytest.mark.parametrize("provider,payload", [
    ("anthropic", {"content": [{"type": "text", "text": result()}], "stop_reason": "max_tokens", "usage": {"output_tokens": 100}}),
    ("deepseek", {"choices": [{"message": {"content": result()}, "finish_reason": "length"}], "usage": {"completion_tokens": 100}}),
    ("gemini", {"candidates": [{"content": {"parts": [{"text": result()}]}, "finishReason": "MAX_TOKENS"}], "usageMetadata": {"candidatesTokenCount": 100}}),
])
def test_truncation_is_not_accepted_and_usage_survives(monkeypatch, provider, payload):
    gateway, _ = mock(monkeypatch, provider, payload)
    with pytest.raises(InvalidModelOutput) as exc:
        gateway.generate({})
    assert exc.value.usage["output_tokens"] == 100


def test_gemini_blocked_response_is_failure(monkeypatch):
    gateway, _ = mock(monkeypatch, "gemini", {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(InvalidModelOutput):
        gateway.generate({})


@pytest.mark.parametrize("provider", ["anthropic", "deepseek", "gemini"])
def test_missing_config_never_falls_back(monkeypatch, provider):
    monkeypatch.delenv(provider.upper() + "_API_KEY", raising=False)
    gateway = ModelGateway(provider, "test-model")
    with pytest.raises(ValueError, match="服务端"):
        gateway.generate({})


@pytest.mark.parametrize("status,exception", [(401, ValueError), (403, ValueError), (400, ValueError), (429, TemporaryModelError), (503, TemporaryModelError)])
def test_errors_do_not_expose_provider_response_or_key(monkeypatch, status, exception):
    gateway, _ = mock(monkeypatch, "deepseek", {"error": "synthetic-key-never-log"}, status)
    with pytest.raises(exception) as exc:
        gateway.generate({})
    assert "synthetic-key" not in str(exc.value)


@pytest.mark.parametrize("value", ["[]", '{"tool_request":null}', '{"tool_request":{"name":7}}', '{"tool_request":{"name":"task.list","arguments":[]}}', '{"tool_request":{"name":"task.list","approved":true}}'])
def test_invalid_tool_envelopes_rejected(monkeypatch, value):
    gateway, _ = mock(monkeypatch, "deepseek", {"choices": [{"message": {"content": value}}]})
    with pytest.raises(InvalidModelOutput):
        gateway.generate({})


def test_aliases_and_provider_specific_models(monkeypatch):
    monkeypatch.delenv("FAMILYAGENT_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_MODEL", "account-model")
    assert ModelGateway("deepseek").model == "account-model"
    assert ModelGateway("claude", "my-claude").provider == "anthropic"
    assert ModelGateway("gemini", "models/my-gemini").model == "my-gemini"
    assert "API_KEY" not in json.dumps(ModelGateway("deepseek").config_snapshot())
    with pytest.raises(ValueError):
        ModelGateway("gemini", "test?key=bad")


def test_transport_error_is_redacted(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    client = httpx.Client
    def fail(request):
        raise httpx.ConnectError("sensitive transport details", request=request)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(fail), **kwargs))
    with pytest.raises(TemporaryModelError) as exc:
        ModelGateway("gemini", "test").generate({})
    assert "sensitive" not in str(exc.value)


def test_gemini_project_id_is_rejected_before_network(monkeypatch):
    monkeypatch.setenv("FAMILYAGENT_MODEL", "gen-lang-client-123456789")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")
    with pytest.raises(ValueError, match="项目 ID"):
        ModelGateway("gemini")
    monkeypatch.delenv("FAMILYAGENT_MODEL")
    assert ModelGateway("gemini").model == "gemini-test-model"


def test_gemini_404_explains_model_override_without_exposing_body(monkeypatch):
    gateway, _ = mock(monkeypatch, "gemini", {"error": "synthetic-key-never-log"}, 404)
    with pytest.raises(ValueError) as exc:
        gateway.generate({})
    assert "FAMILYAGENT_MODEL" in str(exc.value)
    assert "generateContent" in str(exc.value)
    assert "synthetic-key" not in str(exc.value)


def test_gemini_404_for_new_user_restriction_is_specific_and_redacted(monkeypatch):
    message = "This model is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest features. synthetic-key-never-log"
    gateway, _ = mock(monkeypatch, "gemini", {"error": {"message": message}}, 404)
    with pytest.raises(ValueError) as exc:
        gateway.generate({})
    assert "不再向新用户开放" in str(exc.value)
    assert "gemini-3.8-flash" in str(exc.value)
    assert "synthetic-key" not in str(exc.value)


def test_read_timeout_has_specific_safe_hint(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    client = httpx.Client
    def fail(request):
        raise httpx.ReadTimeout("sensitive transport details", request=request)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(fail), **kwargs))
    with pytest.raises(TemporaryModelError) as exc:
        ModelGateway("gemini", "test").generate({})
    assert "超时" in str(exc.value)
    assert "FAMILYAGENT_MODEL_TIMEOUT" in str(exc.value)
    assert "sensitive" not in str(exc.value)


def test_tool_arguments_envelope_is_normalized(monkeypatch):
    value = {'tool_request': {'name': 'task.list', 'arguments': {'status': 'todo', 'limit': 1}}}
    gateway, _ = mock(monkeypatch, 'deepseek', {'choices': [{'message': {'content': json.dumps(value)}}]})
    result, _ = gateway.generate({})
    assert result == value
