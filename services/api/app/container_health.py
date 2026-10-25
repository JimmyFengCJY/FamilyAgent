"""API readiness: effective config + runtime fingerprint, never a paid request."""
import http.client
import json
from pathlib import Path

from .config import runtime_revision
from .models import ModelGateway


def valid(payload):
    config = ModelGateway().config_snapshot()
    return (payload.get('ok') is True and payload.get('service') == 'familyagent'
            and payload.get('model_config') == config
            and payload.get('runtime_revision') == runtime_revision(Path(__file__).resolve().parents[3]))


def main():
    connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=3)
    try:
        connection.request('GET', '/api/health')
        response = connection.getresponse()
        return 0 if response.status == 200 and valid(json.loads(response.read(65536))) else 1
    except Exception:
        return 1
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
