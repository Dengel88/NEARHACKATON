"""SQLite persistence, no ORM.

Two backends with the same SQL:
- a local SQLite file (default; Docker volume, laptop, tests);
- Turso / libSQL over HTTP (set TURSO_DATABASE_URL), so serverless instances on
  Vercel share one database instead of each keeping its own file in /tmp.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'ai',          -- 'ai' | 'human' (after handoff)
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,                       -- user | assistant | tool | manager
    content TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}',          -- tool calls, invoice card, etc.
    visible INTEGER NOT NULL DEFAULT 1,       -- 0 = model-internal (tool traffic)
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (               -- small state, e.g. the mock 1Click simulator
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""


class SQLiteBackend:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def script(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)
            self._conn.commit()

    def execute(self, sql: str, args: tuple = ()) -> int | None:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid

    def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]


class TursoBackend:
    """libSQL remote protocol (Hrana over HTTP, /v2/pipeline). Sync on purpose:
    the store is called from sync code paths and each call is one round trip."""

    def __init__(self, url: str, token: str = "", timeout: float = 10.0):
        import httpx

        base = url.strip().rstrip("/")
        for scheme in ("libsql://", "wss://", "ws://"):
            if base.startswith(scheme):
                base = ("http://" if scheme == "ws://" else "https://") + base[len(scheme):]
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(base_url=base, headers=headers, timeout=timeout)

    @staticmethod
    def _arg(v: Any) -> dict[str, Any]:
        if v is None:
            return {"type": "null"}
        if isinstance(v, bool) or isinstance(v, int):
            return {"type": "integer", "value": str(int(v))}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        if isinstance(v, bytes):
            return {"type": "blob", "base64": base64.b64encode(v).decode()}
        return {"type": "text", "value": str(v)}

    @staticmethod
    def _val(v: dict[str, Any]) -> Any:
        t = v.get("type")
        if t == "integer":
            return int(v["value"])
        if t == "float":
            return float(v["value"])
        if t == "blob":
            return base64.b64decode(v["base64"])
        if t == "null":
            return None
        return v.get("value")

    def _pipeline(self, stmts: list[tuple[str, tuple]]) -> list[dict[str, Any]]:
        reqs: list[dict[str, Any]] = [
            {"type": "execute", "stmt": {"sql": sql, "args": [self._arg(a) for a in args]}} for sql, args in stmts
        ]
        r = self._http.post("/v2/pipeline", json={"requests": reqs + [{"type": "close"}]})
        r.raise_for_status()
        out = []
        for res in r.json()["results"][: len(stmts)]:
            if res.get("type") != "ok":
                raise RuntimeError(f"libSQL error: {res.get('error', {}).get('message', res)}")
            out.append(res["response"]["result"])
        return out

    def script(self, sql: str) -> None:
        stmts = [s.strip() for s in _strip_comments(sql).split(";") if s.strip()]
        self._pipeline([(s, ()) for s in stmts])

    def execute(self, sql: str, args: tuple = ()) -> int | None:
        rowid = self._pipeline([(sql, args)])[0].get("last_insert_rowid")
        return int(rowid) if rowid is not None else None

    def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        res = self._pipeline([(sql, args)])[0]
        names = [c["name"] for c in res["cols"]]
        return [dict(zip(names, (self._val(v) for v in row))) for row in res["rows"]]


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


class Store:
    def __init__(self, path: str = ":memory:", turso_url: str = "", turso_token: str = ""):
        self._db: SQLiteBackend | TursoBackend = (
            TursoBackend(turso_url, turso_token) if turso_url else SQLiteBackend(path)
        )
        self.backend = "turso" if turso_url else "sqlite"
        self._db.script(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> int | None:
        return self._db.execute(sql, args)

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        return self._db.query(sql, args)

    # kv -------------------------------------------------------------------
    def kv_get(self, key: str) -> Any:
        rows = self._all("SELECT v FROM kv WHERE k = ?", (key,))
        return json.loads(rows[0]["v"]) if rows else None

    def kv_set(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO kv(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, json.dumps(value)),
        )

    # sessions -----------------------------------------------------------
    def ensure_session(self, session_id: str) -> dict[str, Any]:
        now = time.time()
        self._exec(
            "INSERT OR IGNORE INTO sessions(id, mode, created_at, updated_at) VALUES (?, 'ai', ?, ?)",
            (session_id, now, now),
        )
        return self.get_session(session_id)  # type: ignore[return-value]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(rows[0]) if rows else None

    def set_mode(self, session_id: str, mode: str) -> None:
        self._exec("UPDATE sessions SET mode = ?, updated_at = ? WHERE id = ?", (mode, time.time(), session_id))

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self._all("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,))]

    # messages -----------------------------------------------------------
    def add_message(
        self, session_id: str, role: str, content: str = "", meta: dict | None = None, visible: bool = True
    ) -> int:
        now = time.time()
        rowid = self._exec(
            "INSERT INTO messages(session_id, role, content, meta, visible, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(meta or {}), int(visible), now),
        )
        self._exec("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return int(rowid or 0)

    def messages(self, session_id: str, after_id: int = 0, visible_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM messages WHERE session_id = ? AND id > ?"
        if visible_only:
            sql += " AND visible = 1"
        rows = self._all(sql + " ORDER BY id", (session_id, after_id))
        out = []
        for r in rows:
            d = dict(r)
            d["meta"] = json.loads(d["meta"])
            d["visible"] = bool(d["visible"])
            out.append(d)
        return out

    # invoices -----------------------------------------------------------
    def save_invoice(self, invoice: dict[str, Any]) -> None:
        now = time.time()
        invoice.setdefault("created_at", now)
        invoice["updated_at"] = now
        self._exec(
            """INSERT INTO invoices(id, session_id, status, data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data,
                                             updated_at = excluded.updated_at""",
            (invoice["id"], invoice["session_id"], invoice["status"], json.dumps(invoice), invoice["created_at"], now),
        )

    def get_invoice(self, invoice_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT data FROM invoices WHERE id = ?", (invoice_id,))
        return json.loads(rows[0]["data"]) if rows else None

    def list_invoices(self, limit: int = 100) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in self._all("SELECT data FROM invoices ORDER BY created_at DESC LIMIT ?", (limit,))]

    # handoffs -----------------------------------------------------------
    def add_handoff(self, session_id: str, reason: str, contact: str = "") -> int:
        rowid = self._exec(
            "INSERT INTO handoffs(session_id, reason, contact, created_at) VALUES (?, ?, ?, ?)",
            (session_id, reason, contact, time.time()),
        )
        return int(rowid or 0)

    def list_handoffs(self, include_resolved: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM handoffs" + ("" if include_resolved else " WHERE resolved = 0") + " ORDER BY id DESC"
        return [dict(r) for r in self._all(sql)]

    def resolve_handoffs(self, session_id: str) -> None:
        self._exec("UPDATE handoffs SET resolved = 1 WHERE session_id = ?", (session_id,))
