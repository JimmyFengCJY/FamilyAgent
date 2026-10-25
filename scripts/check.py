"""Offline quality gate shared by local runs and CI. Never loads .env."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('backend', 'web', 'eval', 'browser')


def isolated_environment(database):
    prefixes = ('FAMILYAGENT_', 'ANTHROPIC_', 'GEMINI_', 'DEEPSEEK_', 'GOOGLE_', 'OPENAI_')
    env = {key: value for key, value in os.environ.items() if not key.startswith(prefixes) and key != 'PYTEST_ADDOPTS'}
    env.update(FAMILYAGENT_PROVIDER='demo', FAMILYAGENT_DEMO_LOGIN='1', FAMILYAGENT_EMBEDDED_WORKER='0',
               FAMILYAGENT_DB=str(database), PYTHON_BIN=sys.executable)
    return env


def commands(stage, output):
    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    return {
        'backend': [[sys.executable, '-m', 'pip', 'check'],
                    [sys.executable, '-m', 'pytest', '-q', '--junitxml=' + str(output / 'backend.xml')]],
        'web': [[npm, 'run', 'build']],
        'eval': [[sys.executable, '-m', 'evals.run', '--provider', 'demo', '--strategies', 'all', '--output', str(output / 'evaluation')],
                 [sys.executable, '-m', 'evals.memory', '--output', str(output / 'memory')]],
        'browser': [[npm, 'run', 'test:browser']],
    }[stage]


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线质量检查；需先安装锁定依赖，不调用真实模型')
    parser.add_argument('--stage', choices=(*STAGES, 'all'), default='all')
    args = parser.parse_args(argv)
    stages = STAGES if args.stage == 'all' else (args.stage,)
    output = ROOT / 'test-results' / 'ci' / uuid.uuid4().hex
    output.mkdir(parents=True)
    summary = {'stages': list(stages), 'checks': [], 'status': 'running', 'scope': 'offline synthetic checks, not live model quality'}
    code = 0
    try:
        with tempfile.TemporaryDirectory(prefix='familyagent-check-') as temporary:
            env = isolated_environment(Path(temporary) / 'import.sqlite3')
            for stage in stages:
                for command in commands(stage, output):
                    print(f'[{stage}] {" ".join(command)}', flush=True)
                    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
                    summary['checks'].append({'stage': stage, 'command': command, 'exit_code': result.returncode})
                    if result.returncode:
                        code = result.returncode if result.returncode > 0 else 1
                        return code
        summary['status'] = 'passed'
    except (OSError, KeyboardInterrupt) as exc:
        summary['error'] = type(exc).__name__
        code = 1
    finally:
        if summary['status'] != 'passed':
            summary['status'] = 'failed'
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        print(f'检查结果：{output / "summary.json"}', flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
