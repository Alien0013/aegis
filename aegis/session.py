"""Session model + SQLite-backed session store (resume, list, search)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import config as cfg
from .types import Message, new_id
from .util import now_iso, slugify


@dataclass
class Session:
    id: str
    title: str = ""
    messages: list[Message] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @staticmethod
    def create(title: str = "") -> "Session":
        sid = new_id("sess")
        return Session(id=sid, title=title or sid)

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": now_iso(),
            "data": json.dumps(
                {
                    "messages": [m.to_dict() for m in self.messages],
                    "todos": self.todos,
                    "meta": self.meta,
                }
            ),
        }

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Session":
        data = json.loads(row["data"])
        return Session(
            id=row["id"],
            title=row["title"],
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            todos=data.get("todos", []),
            meta=data.get("meta", {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def maybe_title_from(self, text: str) -> None:
        if self.title == self.id and text.strip():
            self.title = slugify(text, 60).replace("-", " ")


class SessionStore:
    def __init__(self):
        self.db = cfg.sessions_db()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                       id TEXT PRIMARY KEY,
                       title TEXT,
                       created_at TEXT,
                       updated_at TEXT,
                       data TEXT
                   )"""
            )

    def save(self, session: Session) -> None:
        row = session.to_row()
        session.updated_at = row["updated_at"]
        with self._conn() as c:
            c.execute(
                """INSERT INTO sessions (id, title, created_at, updated_at, data)
                   VALUES (:id, :title, :created_at, :updated_at, :data)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, updated_at=excluded.updated_at, data=excluded.data""",
                row,
            )

    def load(self, sid: str) -> Session | None:
        with self._conn() as c:
            # exact id, then title match, then prefix
            for q, arg in (
                ("SELECT * FROM sessions WHERE id=?", sid),
                ("SELECT * FROM sessions WHERE title=?", sid),
                ("SELECT * FROM sessions WHERE id LIKE ? ORDER BY updated_at DESC", sid + "%"),
            ):
                row = c.execute(q, (arg,)).fetchone()
                if row:
                    return Session.from_row(row)
        return None

    def latest(self) -> Session | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 1").fetchone()
            return Session.from_row(row) if row else None

    def list(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete(self, sid: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM sessions WHERE id=?", (sid,))
            return cur.rowcount > 0

    def search(self, query: str, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, updated_at FROM sessions WHERE data LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def search_messages(self, query: str, limit: int = 8) -> list[dict]:
        """Cross-session recall: return matching message snippets across past sessions."""
        out: list[dict] = []
        q = query.lower()
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM sessions WHERE data LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (f"%{query}%", limit * 3),
            ).fetchall()
        for row in rows:
            sess = Session.from_row(row)
            for m in sess.messages:
                if m.role in ("user", "assistant") and m.content and q in m.content.lower():
                    idx = m.content.lower().find(q)
                    start = max(0, idx - 80)
                    snippet = m.content[start:idx + 160].strip().replace("\n", " ")
                    out.append({"session": sess.id, "title": sess.title,
                                "when": sess.updated_at, "role": m.role, "snippet": snippet})
                    break
            if len(out) >= limit:
                break
        return out
