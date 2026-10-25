"""Container-only startup: validate effective configuration, then exec the role."""
import os
import sys

from .models import ModelGateway


def command(role):
    if role == 'api':
        return [sys.executable, '-m', 'uvicorn', 'services.api.app.main:app', '--host', '0.0.0.0', '--port', '8000']
    if role == 'worker':
        return [sys.executable, '-m', 'services.api.app.worker']
    raise ValueError('Container role must be api or worker')


def main():
    try:
        args = command(sys.argv[1] if len(sys.argv) == 2 else '')
        config = ModelGateway().validate_config()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Container role={sys.argv[1]} provider={config['provider']} model={config['model']}", flush=True)
    os.execv(sys.executable, args)


if __name__ == '__main__':
    main()
