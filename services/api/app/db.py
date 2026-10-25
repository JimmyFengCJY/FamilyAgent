"""Small transactional repository. A queued run is itself the durable job/outbox."""
import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .migrations import migrate


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
        conn = self.connect()
        try:
            migrate(conn)
        finally:
            conn.close()

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
        row = conn.execute("SELECT data FROM records WHERE id=? UNION ALL SELECT data FROM runs WHERE id=? UNION ALL SELECT data FROM messages WHERE id=?", (key, key, key)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind, conn=None):
        if conn is None:
            with self.connect() as c:
                return self.all(kind, c)
        if kind in {"run", "message"}:
            table = "runs" if kind == "run" else "messages"
            return [json.loads(r[0]) for r in conn.execute(f"SELECT data FROM {table} ORDER BY seq")]
        return [json.loads(r[0]) for r in conn.execute("SELECT data FROM records WHERE kind=? ORDER BY rowid", (kind,))]

    def conversation_records(self, kind, conversation_id, conn=None, *, limit=None, exclude_run=None):
        # Callers must authorize the conversation first; this is not an ACL API.
        if kind not in {"message", "run"}:
            raise ValueError("Unsupported conversation record kind")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 500):
            raise ValueError("Invalid history limit")
        if conn is None:
            with self.connect() as c:
                return self.conversation_records(kind, conversation_id, c, limit=limit, exclude_run=exclude_run)
        table = "messages" if kind == "message" else "runs"
        sql = f"SELECT data FROM {table} WHERE conversation_id=?"
        args = [conversation_id]
        if exclude_run is not None:
            sql += " AND " + ("run_id" if kind == "message" else "json_extract(data,'$.run_id')") + " IS NOT ?"
            args.append(exclude_run)
        sql += " ORDER BY seq" + (" DESC LIMIT ?" if limit is not None else "")
        if limit is not None:
            args.append(limit)
        rows = [json.loads(r[0]) for r in conn.execute(sql, args)]
        return rows[::-1] if limit is not None else rows

    def message_page(self, conversation_id, *, limit=50, before=None, after=None):
        if type(limit) is not int or not 1 <= limit <= 100 or (before and after):
            raise ValueError("分页参数无效")
        with self.connect() as conn:
            args = [conversation_id]
            sql = "SELECT id,data FROM messages WHERE conversation_id=?"
            cursor = before or after
            if cursor:
                anchor = conn.execute("SELECT seq FROM messages WHERE id=? AND conversation_id=?", (cursor, conversation_id)).fetchone()
                if anchor is None:
                    raise ValueError("消息游标已失效或不属于此会话，请重新打开会话")
                sql += " AND seq" + (">?" if after else "<?")
                args.append(anchor[0])
            sql += " ORDER BY seq " + ("ASC" if after else "DESC") + " LIMIT ?"
            rows = conn.execute(sql, [*args, limit + 1]).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
            if not after:
                rows.reverse()
            return {"messages": [json.loads(r['data']) for r in rows],
                    "page": {"has_more": more, "direction": "after" if after else "before",
                             "oldest": rows[0]['id'] if rows else None,
                             "newest": rows[-1]['id'] if rows else None}}

    def run_summaries(self, conversation_ids):
        # Filter membership in SQL without constructing an unbounded IN parameter list.
        fields = ("id", "owner_id", "conversation_id", "mode", "status", "cursor", "calls",
                  "max_calls", "created_at", "provider", "version", "usage", "participants", "approvals")
        projection = ",".join("'" + f + "',json_extract(data,'$." + f + "')" for f in fields)
        with self.connect() as conn:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT json_object(" + projection + ") FROM runs "
                "WHERE conversation_id IN (SELECT value FROM json_each(?)) ORDER BY seq",
                (dump(list(conversation_ids)),))]

    def claim_candidate(self, timestamp, conn):
        row = conn.execute(
            "SELECT data FROM runs INDEXED BY runs_ready WHERE status IN ('queued','running') "
            "AND next_attempt_at<=? AND (status='queued' OR lease_until<?) "
            "ORDER BY seq LIMIT 1", (timestamp, timestamp)).fetchone()
        return json.loads(row[0]) if row else None

    def renew_lease(self, run_id, worker, attempt, deadline, duration):
        """Short compare-and-update; never revive an expired or superseded attempt."""
        conn = sqlite3.connect(self.path, timeout=0.1)
        try:
            conn.execute("BEGIN IMMEDIATE")
            timestamp = time.time()  # sample after acquiring the writer lock
            # Millisecond precision avoids JSON number serialization drifting past the cap.
            expires = min(math.floor((timestamp + duration) * 1000) / 1000, deadline)
            row = conn.execute(
                "UPDATE runs SET data=json_set(data,'$.lease_until',?, "
                "'$.lease_renewed_at',?,'$.lease_renewals',COALESCE(json_extract(data,'$.lease_renewals'),0)+1) "
                "WHERE id=? AND status='running' AND lease_until>? "
                "AND json_extract(data,'$.worker')=? AND json_extract(data,'$.attempt_id')=? "
                "AND json_extract(data,'$.lease_deadline')=? AND ?>?",
                (expires, timestamp, run_id, timestamp, worker, attempt, deadline, deadline, timestamp))
            if row.rowcount:
                self.event(run_id, "lease.renewed", {"attempt_id": attempt, "lease_until": expires}, conn)
            conn.commit()
            return bool(row.rowcount)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def put(self, kind, value, conn):
        if kind in {"run", "message"}:
            table = "runs" if kind == "run" else "messages"
            conn.execute(f"INSERT INTO {table}(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (value["id"], dump(value)))
        else:
            conn.execute("INSERT INTO records(id,kind,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (value["id"], kind, dump(value)))
        return value

    def private_memories(self, owner_id, conn):
        return [json.loads(row[0]) for row in conn.execute(
            "SELECT data FROM records WHERE kind='memory' AND json_extract(data,'$.owner_id')=? "
            "AND COALESCE(json_extract(data,'$.scope'),'private')='private' ORDER BY rowid", (owner_id,))]

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
