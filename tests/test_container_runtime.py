"""Container entrypoint and readiness behavior, without Docker or live APIs."""
import sys
import pytest
from services.api.app import container_health, container_start
from services.api.app.config import runtime_revision
from services.api.app.models import ModelGateway


def test_entrypoint_roles_preserve_exec_signal_delivery():
    assert container_start.command('worker') == [sys.executable, '-m', 'services.api.app.worker']
    assert container_start.command('api')[-4:] == ['--host', '0.0.0.0', '--port', '8000']
    with pytest.raises(ValueError):
        container_start.command('arbitrary-command')


def test_health_requires_runtime_revision_and_effective_config():
    root = container_health.Path(container_health.__file__).resolve().parents[3]
    payload = {'ok': True, 'service': 'familyagent', 'runtime_revision': runtime_revision(root),
               'model_config': ModelGateway().config_snapshot()}
    assert container_health.valid(payload)
    assert not container_health.valid({**payload, 'runtime_revision': 'stale'})
    assert not container_health.valid({**payload, 'model_config': {'provider': 'gemini'}})


def test_entrypoint_rejects_invalid_provider_before_exec(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['container_start', 'worker'])
    monkeypatch.setenv('FAMILYAGENT_PROVIDER', 'unknown')
    monkeypatch.setattr(container_start.os, 'execv', lambda *args: pytest.fail('invalid config must not launch process'))
    with pytest.raises(SystemExit):
        container_start.main()
