"""Session-keyed OpenAI Responses state.

The Responses API can continue from a previous response id when the provider is
allowed to store state. AEGIS keeps that opt-in and session-scoped: stateless
mode remains the default, while enabled installs can preserve response ids and
hosted-tool output item metadata for replay/debugging.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import config as cfg
from .util import now_iso


@dataclass
class ResponseState:
    session_id: str
    response_id: str
    provider: str = ""
    model: str = ""
    output_items: list[dict[str, Any]] | None = None
    input_message_count: int = 0
    updated_at: str = ""


class ResponsesStateStore:
    def __init__(self, path=None):
        self.db = path or cfg.sub("responses_state.db")
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.OperationalError:
            pass
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS responses_state (
                       session_id TEXT PRIMARY KEY,
                       response_id TEXT NOT NULL,
                       provider TEXT,
                       model TEXT,
                       updated_at TEXT,
                       output_items TEXT,
                       input_message_count INTEGER DEFAULT 0
                   )"""
            )
            cols = {row["name"] for row in c.execute("PRAGMA table_info(responses_state)").fetchall()}
            if "input_message_count" not in cols:
                c.execute("ALTER TABLE responses_state ADD COLUMN input_message_count INTEGER DEFAULT 0")

    def get(self, session_id: str) -> ResponseState | None:
        if not session_id:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM responses_state WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        try:
            items = json.loads(row["output_items"] or "[]")
        except json.JSONDecodeError:
            items = []
        return ResponseState(
            session_id=row["session_id"],
            response_id=row["response_id"],
            provider=row["provider"] or "",
            model=row["model"] or "",
            updated_at=row["updated_at"] or "",
            output_items=items if isinstance(items, list) else [],
            input_message_count=int(row["input_message_count"] or 0),
        )

    def set(
        self,
        session_id: str,
        response_id: str,
        *,
        provider: str = "",
        model: str = "",
        output_items: list[dict[str, Any]] | None = None,
        input_message_count: int = 0,
    ) -> None:
        if not session_id or not response_id:
            return
        payload = json.dumps(output_items or [])
        with self._conn() as c:
            c.execute(
                """INSERT INTO responses_state
                   (session_id, response_id, provider, model, updated_at, output_items, input_message_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     response_id=excluded.response_id,
                     provider=excluded.provider,
                     model=excluded.model,
                     updated_at=excluded.updated_at,
                     output_items=excluded.output_items,
                     input_message_count=excluded.input_message_count""",
                (session_id, response_id, provider, model, now_iso(), payload,
                 max(0, int(input_message_count or 0))),
            )

    def clear(self, session_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM responses_state WHERE session_id=?", (session_id,))
            return cur.rowcount > 0

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT session_id, response_id, provider, model, updated_at, input_message_count
                   FROM responses_state ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def enabled(config) -> bool:
    return bool(config.get("responses.state.enabled", False))


def settings(config) -> dict[str, Any]:
    return dict(config.get("responses.state", {}) or {})
