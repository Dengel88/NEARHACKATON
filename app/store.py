"""SQLite persistence. One file, no ORM, safe to point at /tmp on serverless."""

from __future__ import annotations

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
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

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
        cur = self._exec(
            "INSERT INTO messages(session_id, role, content, meta, visible, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(meta or {}), int(visible), now),
        )
        self._exec("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return int(cur.lastrowid)

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
        cur = self._exec(
            "INSERT INTO handoffs(session_id, reason, contact, created_at) VALUES (?, ?, ?, ?)",
            (session_id, reason, contact, time.time()),
        )
        return int(cur.lastrowid)

    def list_handoffs(self, include_resolved: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM handoffs" + ("" if include_resolved else " WHERE resolved = 0") + " ORDER BY id DESC"
        return [dict(r) for r in self._all(sql)]

    def resolve_handoffs(self, session_id: str) -> None:
        self._exec("UPDATE handoffs SET resolved = 1 WHERE session_id = ?", (session_id,))
