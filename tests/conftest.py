"""Isolate module-level ASGI app initialization before collecting any tests."""
import os
import tempfile
from pathlib import Path

# main.py constructs its default app on import, even when tests use create_app.
_import_database = tempfile.TemporaryDirectory(prefix='familyagent-pytest-import-')
os.environ['FAMILYAGENT_DB'] = str(Path(_import_database.name) / 'import.sqlite3')
os.environ['FAMILYAGENT_PROVIDER'] = 'demo'
os.environ['FAMILYAGENT_EMBEDDED_WORKER'] = '0'
