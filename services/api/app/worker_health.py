"""Container process liveness only; the deployment smoke verifies real work."""
from pathlib import Path


def alive(proc=Path('/proc')):
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / 'cmdline').read_bytes().split(b'\0')
        except (OSError, PermissionError):
            continue
        if len(args) >= 3 and args[1:3] == [b'-m', b'services.api.app.worker']:
            return True
    return False


if __name__ == '__main__':
    raise SystemExit(0 if alive() else 1)
