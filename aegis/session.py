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
    parent_id: str | None = None        # session lineage (set when forked, e.g. on compaction)

    @staticmethod
    def create(title: str = "", parent_id: str | None = None) -> "Session":
        sid = new_id("sess")
        return Session(id=sid, title=title or sid, parent_id=parent_id)

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": now_iso(),
            "parent_id": self.parent_id,
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
        keys = row.keys()
        return Session(
            id=row["id"],
            title=row["title"],
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            todos=data.get("todos", []),
            meta=data.get("meta", {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            parent_id=row["parent_id"] if "parent_id" in keys else None,
        )

    def maybe_title_from(self, text: str) -> None:
        if self.title == self.id and text.strip():
            self.title = slugify(text, 60).replace("-", " ")


class SessionStore:
    def __init__(self):
        self.db = cfg.sessions_db()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        # 30s busy timeout + WAL so concurrent gateway threads don't hit
        # "database is locked" under load.
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
                """CREATE TABLE IF NOT EXISTS sessions (
                       id TEXT PRIMARY KEY,
                       title TEXT,
                       created_at TEXT,
                       updated_at TEXT,
                       summary TEXT,
                       data TEXT
                   )"""
            )
            # add new columns to pre-existing tables
            cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
            if "summary" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
            if "parent_id" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN parent_id TEXT")
            # full-text index over message content (graceful if FTS5 is unavailable)
            try:
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
                          "content, session_id UNINDEXED, title UNINDEXED, role UNINDEXED, ts UNINDEXED)")
                self._fts = True
            except sqlite3.OperationalError:
                self._fts = False

    def save(self, session: Session) -> None:
        row = session.to_row()
        session.updated_at = row["updated_at"]
        row["summary"] = session.meta.get("summary", "")
        with self._conn() as c:
            c.execute(
                """INSERT INTO sessions (id, title, created_at, updated_at, summary, parent_id, data)
                   VALUES (:id, :title, :created_at, :updated_at, :summary, :parent_id, :data)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, updated_at=excluded.updated_at,
                     summary=excluded.summary, parent_id=excluded.parent_id, data=excluded.data""",
                row,
            )
            if getattr(self, "_fts", False):
                c.execute("DELETE FROM messages_fts WHERE session_id=?", (session.id,))
                for m in session.messages:
                    if m.role in ("user", "assistant") and m.content:
                        c.execute("INSERT INTO messages_fts (content, session_id, title, role, ts) "
                                  "VALUES (?,?,?,?,?)",
                                  (m.content, session.id, session.title, m.role, session.updated_at))

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
                "SELECT id, title, created_at, updated_at, parent_id FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def children(self, parent_id: str) -> list[dict]:
        """Sessions forked from ``parent_id`` (lineage chain), oldest first."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, created_at FROM sessions WHERE parent_id=? ORDER BY created_at",
                (parent_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def fork(self, parent: Session, *, carry_summary: bool = True) -> Session:
        """Create a child session linked to ``parent`` (e.g. when compaction splits a long
        session). The child keeps the system prompt + a summary breadcrumb of the parent."""
        child = Session.create(title=parent.title, parent_id=parent.id)
        system = [m for m in parent.messages if m.role == "system"][:1]
        child.messages = list(system)
        if carry_summary:
            child.meta["forked_from"] = parent.id
            child.meta["summary"] = parent.meta.get("summary", "")
        child.meta["_rebuild_system_prompt"] = True
        for key in ("runtime", "runtime_controls", "model", "provider"):
            if key in parent.meta:
                value = parent.meta[key]
                child.meta[key] = dict(value) if isinstance(value, dict) else value
        self.save(parent)
        self.save(child)
        return child

    def delete(self, sid: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM sessions WHERE id=?", (sid,))
            if getattr(self, "_fts", False):
                c.execute("DELETE FROM messages_fts WHERE session_id=?", (sid,))  # no orphan rows
            return cur.rowcount > 0

    def search(self, query: str, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, updated_at FROM sessions WHERE data LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _fts_query(query: str) -> str:
        """Natural-language query -> FTS5 OR-of-terms (ranked). A whole-query phrase
        match meant 'what did we decide about X' could never hit anything."""
        import re
        stop = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "we", "i",
                "you", "it", "is", "are", "was", "were", "do", "did", "does", "done",
                "have", "has", "had", "what", "when", "how", "who", "our", "my", "your",
                "this", "that", "about", "with", "like", "so", "far", "me", "us"}
        toks = [t for t in re.findall(r"[A-Za-z0-9_]{2,}", query)
                if t.lower() not in stop][:12]
        if not toks:
            return '"' + query.replace('"', "") + '"'
        return " OR ".join(f'"{t}"' for t in toks)

    def search_messages(self, query: str, limit: int = 8) -> list[dict]:
        """Cross-session recall: ranked message snippets across past sessions (FTS5)."""
        if getattr(self, "_fts", False):
            try:
                match = self._fts_query(query)
                with self._conn() as c:
                    rows = c.execute(
                        "SELECT session_id, title, role, ts, "
                        "snippet(messages_fts, 0, '[', ']', '…', 12) AS snip "
                        "FROM messages_fts WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
                        (match, limit),
                    ).fetchall()
                return [{"session": r["session_id"], "title": r["title"], "when": r["ts"],
                         "role": r["role"], "snippet": r["snip"].replace("\n", " ")} for r in rows]
            except sqlite3.OperationalError:
                pass
        # Fallback without FTS: scan recent sessions for ANY query term.
        import re
        toks = [t.lower() for t in re.findall(r"[A-Za-z0-9_]{2,}", query)][:12] or [query.lower()]
        out: list[dict] = []
        with self._conn() as c:
            rows = c.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                             (limit * 5,)).fetchall()
        for row in rows:
            sess = Session.from_row(row)
            for m in sess.messages:
                low = m.content.lower() if m.content else ""
                hit = next((t for t in toks if t in low), None)
                if m.role in ("user", "assistant") and hit:
                    idx = low.find(hit)
                    snippet = m.content[max(0, idx - 80):idx + 160].strip().replace("\n", " ")
                    out.append({"session": sess.id, "title": sess.title,
                                "when": sess.updated_at, "role": m.role, "snippet": snippet})
                    break
            if len(out) >= limit:
                break
        return out

    def summarize(self, sid: str, provider=None, config=None) -> str:
        """Generate + store a 1-2 sentence summary of a session via the provider."""
        sess = self.load(sid)
        if not sess:
            return ""
        from .types import Message
        transcript = "\n".join(f"{m.role}: {m.content}" for m in sess.messages
                               if m.role in ("user", "assistant") and m.content)[:12_000]
        if not transcript.strip():
            return ""
        try:
            if provider is None:
                from .auxiliary import AuxRouter
                provider = AuxRouter(config).provider_for("session_summary")
            resp = provider.complete([
                Message.system("Summarize this conversation in 1-2 sentences: what the user wanted "
                               "and what was decided/done. Be specific and factual."),
                Message.user(transcript),
            ], tools=None, stream=False)
            summary = resp.text.strip()
        except Exception:  # noqa: BLE001
            return ""
        sess.meta["summary"] = summary
        self.save(sess)
        return summary
