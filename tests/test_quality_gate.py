"""Check isolation and fail-closed behavior of the local/CI command runner."""
import json
from types import SimpleNamespace

from scripts import check


def test_environment_discards_model_credentials_and_user_database(monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-not-a-real-key')
    monkeypatch.setenv('FAMILYAGENT_DB', '/never-open-user-database')
    monkeypatch.setenv('FAMILYAGENT_PROVIDER', 'gemini')
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k skip_everything')
    env = check.isolated_environment(tmp_path / 'isolated.sqlite3')
    assert 'GEMINI_API_KEY' not in env and 'PYTEST_ADDOPTS' not in env
    assert env['FAMILYAGENT_PROVIDER'] == 'demo'
    assert env['FAMILYAGENT_DB'] == str(tmp_path / 'isolated.sqlite3')


def test_nonzero_stage_fails_fast_and_preserves_report(monkeypatch, tmp_path):
    monkeypatch.setattr(check, 'ROOT', tmp_path)
    calls = []
    def fail(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(check.subprocess, 'run', fail)
    assert check.main(['--stage', 'all']) == 7
    assert len(calls) == 1
    report = json.loads(next(tmp_path.glob('test-results/ci/*/summary.json')).read_text())
    assert report['status'] == 'failed' and report['checks'][0]['exit_code'] == 7


def test_missing_executable_still_writes_failure_report(monkeypatch, tmp_path):
    monkeypatch.setattr(check, 'ROOT', tmp_path)
    def missing(*args, **kwargs):
        raise FileNotFoundError('synthetic missing executable')
    monkeypatch.setattr(check.subprocess, 'run', missing)
    assert check.main(['--stage', 'web']) == 1
    report = json.loads(next(tmp_path.glob('test-results/ci/*/summary.json')).read_text())
    assert report['status'] == 'failed' and report['error'] == 'FileNotFoundError'


def test_default_plan_orders_browser_after_build_and_stays_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(check, 'ROOT', tmp_path)
    observed = []
    def passed(command, **kwargs):
        assert kwargs['env']['FAMILYAGENT_PROVIDER'] == 'demo'
        assert '--live' not in command
        observed.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(check.subprocess, 'run', passed)
    assert check.main([]) == 0
    report = json.loads(next(tmp_path.glob('test-results/ci/*/summary.json')).read_text())
    assert [r['stage'] for r in report['checks']] == ['backend', 'backend', 'web', 'eval', 'eval', 'browser']
    assert report['status'] == 'passed'
    evaluation = next(c for c in observed if 'evals.run' in c)
    assert evaluation[evaluation.index('--provider') + 1] == 'demo'
