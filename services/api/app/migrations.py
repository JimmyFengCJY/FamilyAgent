"""Append-only SQLite migrations; startup upgrades are atomic and fail closed."""
import hashlib
import sqlite3
import time

# Never edit a released migration: append a new version instead.
MIGRATIONS = (
    (1, "baseline", (
        "CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL, household TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS records_kind ON records(kind)",
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS events_run ON events(run_id,id)",
        "CREATE TABLE IF NOT EXISTS invitations (token TEXT PRIMARY KEY, household TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0)",
    )),
    (2, "query_indexes", (
        "CREATE INDEX records_conversation ON records(kind,json_extract(data,'$.conversation_id'))",
        "CREATE INDEX records_run_status ON records(kind) WHERE kind='run' AND json_extract(data,'$.status') IN ('queued','running')",
        "CREATE INDEX records_private_memory ON records(json_extract(data,'$.owner_id'),COALESCE(json_extract(data,'$.scope'),'private')) WHERE kind='memory'",
    )),
    (3, "split_runs_messages", (
        """CREATE TABLE runs (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            data TEXT NOT NULL CHECK(json_valid(data) AND json_type(data)='object'),
            conversation_id TEXT GENERATED ALWAYS AS (json_extract(data,'$.conversation_id')) STORED NOT NULL,
            status TEXT GENERATED ALWAYS AS (json_extract(data,'$.status')) STORED NOT NULL,
            next_attempt_at REAL GENERATED ALWAYS AS (COALESCE(json_extract(data,'$.next_attempt_at'),0)) STORED NOT NULL,
            lease_until REAL GENERATED ALWAYS AS (COALESCE(json_extract(data,'$.lease_until'),0)) STORED NOT NULL,
            CHECK(json_type(data,'$.id') IS 'text' AND json_extract(data,'$.id')=id),
            CHECK(json_type(data,'$.conversation_id') IS 'text' AND length(conversation_id)>0),
            CHECK(json_type(data,'$.status') IS 'text' AND status IN ('queued','running','paused','exhausted','failed','waiting_approval','waiting_user','succeeded','cancelled')),
            CHECK(COALESCE(json_type(data,'$.next_attempt_at'),'null') IN ('null','integer','real')),
            CHECK(COALESCE(json_type(data,'$.lease_until'),'null') IN ('null','integer','real'))
        )""",
        """CREATE TABLE messages (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            data TEXT NOT NULL CHECK(json_valid(data) AND json_type(data)='object'),
            conversation_id TEXT GENERATED ALWAYS AS (json_extract(data,'$.conversation_id')) STORED NOT NULL,
            run_id TEXT GENERATED ALWAYS AS (json_extract(data,'$.run_id')) STORED,
            CHECK(json_type(data,'$.id') IS 'text' AND json_extract(data,'$.id')=id),
            CHECK(json_type(data,'$.conversation_id') IS 'text' AND length(conversation_id)>0),
            CHECK(COALESCE(json_type(data,'$.run_id'),'null') IN ('null','text'))
        )""",
        "CREATE INDEX runs_conversation ON runs(conversation_id,seq)",
        "CREATE INDEX runs_ready ON runs(seq) WHERE status IN ('queued','running')",
        "CREATE INDEX messages_conversation ON messages(conversation_id,seq)",
        "INSERT INTO runs(seq,id,data) SELECT rowid,id,data FROM records WHERE kind='run' ORDER BY rowid",
        "INSERT INTO messages(seq,id,data) SELECT rowid,id,data FROM records WHERE kind='message' ORDER BY rowid",
        "CREATE TEMP TABLE split_verification (ok INTEGER NOT NULL CHECK(ok=1))",
        """INSERT INTO split_verification SELECT
            (SELECT count(*) FROM runs)=(SELECT count(*) FROM records WHERE kind='run') AND
            (SELECT count(*) FROM messages)=(SELECT count(*) FROM records WHERE kind='message') AND
            NOT EXISTS(SELECT 1 FROM records r LEFT JOIN runs n ON n.id=r.id WHERE r.kind='run' AND (n.id IS NULL OR n.seq!=r.rowid OR n.data!=r.data)) AND
            NOT EXISTS(SELECT 1 FROM records r LEFT JOIN messages n ON n.id=r.id WHERE r.kind='message' AND (n.id IS NULL OR n.seq!=r.rowid OR n.data!=r.data))""",
        "DROP TABLE split_verification",
        "DELETE FROM records WHERE kind IN ('run','message')",
        "DROP INDEX records_run_status",
        "DROP INDEX records_conversation",
        """CREATE TRIGGER records_insert_guard BEFORE INSERT ON records
            WHEN NEW.kind IN ('run','message') OR EXISTS(SELECT 1 FROM records WHERE id=NEW.id AND kind!=NEW.kind) OR EXISTS(SELECT 1 FROM runs WHERE id=NEW.id) OR EXISTS(SELECT 1 FROM messages WHERE id=NEW.id)
            BEGIN SELECT RAISE(ABORT,'Use the entity repository; duplicate or legacy entity write'); END""",
        """CREATE TRIGGER records_update_guard BEFORE UPDATE ON records
            WHEN NEW.id IS NOT OLD.id OR NEW.kind IS NOT OLD.kind OR NEW.kind IN ('run','message')
            BEGIN SELECT RAISE(ABORT,'Record identity and kind are immutable'); END""",
        """CREATE TRIGGER runs_insert_guard BEFORE INSERT ON runs
            WHEN EXISTS(SELECT 1 FROM records WHERE id=NEW.id) OR EXISTS(SELECT 1 FROM messages WHERE id=NEW.id)
            BEGIN SELECT RAISE(ABORT,'Entity id already exists'); END""",
        """CREATE TRIGGER messages_insert_guard BEFORE INSERT ON messages
            WHEN EXISTS(SELECT 1 FROM records WHERE id=NEW.id) OR EXISTS(SELECT 1 FROM runs WHERE id=NEW.id)
            BEGIN SELECT RAISE(ABORT,'Entity id already exists'); END""",
        """CREATE TRIGGER runs_identity_guard BEFORE UPDATE ON runs WHEN NEW.id IS NOT OLD.id OR NEW.seq IS NOT OLD.seq OR NEW.conversation_id IS NOT OLD.conversation_id
            BEGIN SELECT RAISE(ABORT,'Run identity, conversation and order are immutable'); END""",
        """CREATE TRIGGER messages_identity_guard BEFORE UPDATE ON messages WHEN NEW.id IS NOT OLD.id OR NEW.seq IS NOT OLD.seq OR NEW.conversation_id IS NOT OLD.conversation_id
            BEGIN SELECT RAISE(ABORT,'Message identity, conversation and order are immutable'); END""",
    )),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


def migrate(conn):
    """Adopt the legacy schema without rewriting rows; serialize concurrent startups."""
    if sqlite3.sqlite_version_info < (3, 31, 0):
        raise RuntimeError("数据库升级需要 SQLite 3.31 或更新版本及 JSON 支持")
    deadline = time.monotonic() + 15
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        applied = list(conn.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version"))
        if [r[0] for r in applied] != list(range(1, len(applied) + 1)) or len(applied) > len(MIGRATIONS):
            raise RuntimeError("数据库迁移版本未知或不连续，请使用匹配版本的应用")
        for version, name, statements in MIGRATIONS:
            checksum = hashlib.sha256("\n".join(statements).encode()).hexdigest()
            if version <= len(applied):
                if tuple(applied[version - 1]) != (version, name, checksum):
                    raise RuntimeError("数据库迁移历史与代码不一致，停止启动")
                continue
            for statement in statements:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES(?,?,?)", (version, name, checksum))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
