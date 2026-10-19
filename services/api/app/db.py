"""Small transactional repository. A queued run is itself the durable job/outbox."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix=""):
    return prefix + uuid.uuid4().hex


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path=None):
        self.path = str(path or os.getenv("FAMILYAGENT_DB", "data/familyagent.sqlite3"))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
              password TEXT NOT NULL, household TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS records (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS records_kind ON records(kind);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
            CREATE TABLE IF NOT EXISTS invitations (
              token TEXT PRIMARY KEY, household TEXT NOT NULL, expires REAL NOT NULL,
              used INTEGER NOT NULL DEFAULT 0);
            """)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def tx(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, key, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.get(key, c)
        row = conn.execute("SELECT data FROM records WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.all(kind, c)
        return [json.loads(r[0]) for r in conn.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def put(self, kind, value, conn):
        conn.execute("INSERT INTO records(id,kind,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (value["id"], kind, dump(value)))
        return value

    def event(self, run_id, event_type, data, conn):
        conn.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)", (run_id, event_type, dump(data), now()))

    def events(self, run_id, after=0):
        with self.connect() as conn:
            return [{"id": r["id"], "run_id": run_id, "type": r["type"], "data": json.loads(r["data"]), "created_at": r["created_at"]} for r in conn.execute("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 500", (run_id, after))]

    def user(self, user_id, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.user(user_id, c)
        row = conn.execute("SELECT id,email,name,household FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def members(self, household):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT id,name,email FROM users WHERE household=?", (household,))]


def can_access(user, obj):
    if not user or not obj:
        return False
    if obj.get("scope") == "family" and obj.get("household") != user["household"]:
        return False
    if obj.get("owner_id") == user["id"]:
        return True
    return (obj.get("scope") == "family" and obj.get("household") == user["household"]
            and user["id"] in obj.get("participants", []))
