"""Subprocess-only offline worker controlled by filesystem gates; no API credentials."""
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from services.api.app.db import Store
from services.api.app.harness import Harness
from services.api.app.schemas import AgentResult
from services.api.app.tool_registry import registry


def main():
    database, mode, marker = sys.argv[1:]
    prefix = Path(marker)
    def signal(name):
        prefix.with_suffix('.' + name).touch()
    def wait(name):
        deadline = time.monotonic() + 12
        while not prefix.with_suffix('.' + name).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('synthetic worker gate timed out')
            time.sleep(.01)
    class OfflineGateway:
        provider = 'demo'
        def generate(self, context):
            signal('model')
            if mode == 'recover-tool':
                raise AssertionError('pending tool must not repeat model request')
            if mode == 'tool-block':
                return {'tool_request': {'name': 'task.list', 'arguments': {}}}, {'provider': 'demo', 'input_tokens': 1, 'output_tokens': 1}
            if mode in {'model-block', 'deadline-model'}:
                signal('started')
                wait('release')
            return AgentResult(summary='synthetic ' + mode).model_dump(), {'provider': 'demo', 'input_tokens': 1, 'output_tokens': 1}
    if mode == 'tool-block':
        def blocking_tool(args, context):
            signal('started')
            wait('release')
            return []
        registry._tools['task.list'] = replace(registry._tools['task.list'], handler=blocking_tool)
    harness = Harness(Store(database), OfflineGateway(), delay=0, lease_seconds=.6,
                      heartbeat_interval=.05, max_attempt_seconds=.9 if mode == 'deadline-model' else 8)
    signal('ready')
    wait('go')
    if mode == 'claim':
        result = harness.claim()
        print(json.dumps({'claimed': result['id'] if result else None}), flush=True)
    else:
        print(json.dumps({'worked': harness.tick()}), flush=True)


if __name__ == '__main__':
    main()
