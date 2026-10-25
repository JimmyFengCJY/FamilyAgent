"""Compose resolution only: no daemon, images, live credentials or model calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    if not shutil.which('docker') or subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode:
        pytest.skip('Docker Compose CLI is not installed')
    for name in ('compose.yaml', 'compose.model.yaml'):
        shutil.copyfile(ROOT / name, tmp_path / name)
    (tmp_path / 'deploy').mkdir()
    return tmp_path


def config(project, live=False):
    env = dict(os.environ, FAMILYAGENT_PROVIDER='gemini', FAMILYAGENT_MODEL='stale-generic-model',
               FAMILYAGENT_DB='/never-mount-user-data', FAMILYAGENT_DOCKER_PORT='8080')
    command = ['docker', 'compose', '--env-file', '/dev/null', '-p', 'familyagent-synthetic-config', '-f', str(project / 'compose.yaml')]
    if live:
        command += ['-f', str(project / 'compose.model.yaml')]
    result = subprocess.run([*command, 'config', '--format', 'json'], env=env, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_default_compose_ignores_host_model_and_user_database(project):
    result = config(project)
    for name in ('api', 'worker'):
        service = result['services'][name]
        assert service['environment']['FAMILYAGENT_PROVIDER'] == 'demo'
        assert service['environment']['FAMILYAGENT_DB'] == '/data/familyagent.sqlite3'
        assert 'FAMILYAGENT_MODEL' not in service['environment']
        assert all(v['type'] == 'volume' for v in service['volumes'])
    assert result['services']['api']['ports'][0]['host_ip'] == '127.0.0.1'
    assert result['services']['worker']['depends_on']['api']['condition'] == 'service_healthy'


def test_model_override_uses_literal_file_without_stale_shell_override(project):
    (project / 'deploy/model.env').write_text('FAMILYAGENT_PROVIDER=deepseek\nDEEPSEEK_MODEL=synthetic-model\nDEEPSEEK_API_KEY=synthetic-$NOT_EXPANDED\nFAMILYAGENT_MODEL=\nFAMILYAGENT_DB=/never-use-file-path\n')
    result = config(project, live=True)
    for name in ('api', 'worker'):
        env = result['services'][name]['environment']
        assert env['FAMILYAGENT_PROVIDER'] == 'deepseek'
        assert env['FAMILYAGENT_MODEL'] == ''
        # Compose config may escape dollars for round-trip serialization.
        assert env['DEEPSEEK_API_KEY'].replace('$$', '$') == 'synthetic-$NOT_EXPANDED'
        assert env['FAMILYAGENT_DB'] == '/data/familyagent.sqlite3'
