import json
import httpx
import pytest
from services.api.app.models import ModelGateway, InvalidModelOutput, TemporaryModelError
from services.api.app.schemas import AgentResult


def adapter(monkeypatch, status, payload):
    monkeypatch.setenv("FAMILYAGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("FAMILYAGENT_MODEL", "test-model-id")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")
    original = httpx.Client
    captured = []
    def handle(request):
        captured.append(request)
        return httpx.Response(status, json=payload)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    return ModelGateway(), captured


def test_actual_provider_contract_without_network(monkeypatch):
    value = AgentResult(summary="这是结构化结果", suggested_actions=["核对约束"]).model_dump()
    gateway, captured = adapter(monkeypatch, 200, {"content": [{"type": "text", "text": json.dumps(value)}], "usage": {"input_tokens": 20, "output_tokens": 30}})
    result, usage = gateway.generate({"prompt": "测试"})
    assert result["summary"] == "这是结构化结果"
    assert usage["input_tokens"] == 20 and usage["provider"] == "anthropic"
    assert captured[0].headers["x-api-key"] == "synthetic-test-key"
    assert captured[0].url == "https://api.anthropic.com/v1/messages"
    assert json.loads(captured[0].content)["model"] == "test-model-id"


@pytest.mark.parametrize("text", ["not-json", "[]", '{"summary":""}'])
def test_invalid_actual_model_output_is_rejected(monkeypatch, text):
    gateway, _ = adapter(monkeypatch, 200, {"content": [{"type": "text", "text": text}]})
    with pytest.raises(InvalidModelOutput):
        gateway.generate({})


def test_actual_provider_rate_limit_is_retryable(monkeypatch):
    gateway, _ = adapter(monkeypatch, 429, {})
    with pytest.raises(TemporaryModelError):
        gateway.generate({})


def test_actual_mode_never_silently_falls_back_to_demo(monkeypatch):
    monkeypatch.setenv("FAMILYAGENT_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="服务端"):
        ModelGateway().generate({})
