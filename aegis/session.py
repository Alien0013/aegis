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
    def _query_terms(query: str) -> list[str]:
        import re
        stop = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "we", "i",
                "you", "it", "is", "are", "was", "were", "do", "did", "does", "done",
                "have", "has", "had", "what", "when", "how", "who", "our", "my", "your",
                "this", "that", "about", "with", "like", "so", "far", "me", "us"}
        return [t for t in re.findall(r"[A-Za-z0-9_]{2,}", query)
                if t.lower() not in stop][:12]

    @classmethod
    def _fts_query(cls, query: str) -> str:
        """Natural-language query -> FTS5 OR-of-terms (ranked). A whole-query phrase
        match meant 'what did we decide about X' could never hit anything."""
        toks = cls._query_terms(query)
        if not toks:
            return '"' + query.replace('"', "") + '"'
        return " OR ".join(f'"{t}"' for t in toks)

    @staticmethod
    def _message_payload(index: int, message: Message, *, anchor_id: int | None = None) -> dict:
        payload: dict[str, Any] = {"id": index, "role": message.role, "content": message.content}
        if message.name:
            payload["tool_name"] = message.name
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            payload["tool_calls"] = [tc.to_dict() for tc in message.tool_calls]
        if anchor_id is not None and index == anchor_id:
            payload["anchor"] = True
        return payload

    @classmethod
    def _visible_messages(cls, sess: Session, *, roles: set[str] | None = None) -> list[dict]:
        roles = roles or {"user", "assistant", "tool"}
        return [
            cls._message_payload(i, message)
            for i, message in enumerate(sess.messages)
            if message.role in roles
        ]

    @classmethod
    def _first_matching_message_id(cls, sess: Session, query: str,
                                   roles: set[str] | None = None) -> int | None:
        roles = roles or {"user", "assistant"}
        terms = [t.lower() for t in cls._query_terms(query)] or [query.lower()]
        for i, message in enumerate(sess.messages):
            if message.role not in roles:
                continue
            content = (message.content or "").lower()
            if any(term in content for term in terms):
                return i
        return None

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
                out = []
                for r in rows:
                    sess = self.load(r["session_id"])
                    out.append({
                        "session": r["session_id"],
                        "title": r["title"],
                        "when": r["ts"],
                        "role": r["role"],
                        "snippet": r["snip"].replace("\n", " "),
                        "message_id": self._first_matching_message_id(sess, query) if sess else None,
                    })
                return out
            except sqlite3.OperationalError:
                pass
        # Fallback without FTS: scan recent sessions for ANY query term.
        toks = [t.lower() for t in self._query_terms(query)] or [query.lower()]
        out: list[dict] = []
        with self._conn() as c:
            rows = c.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                             (limit * 5,)).fetchall()
        for row in rows:
            sess = Session.from_row(row)
            for msg_index, m in enumerate(sess.messages):
                low = m.content.lower() if m.content else ""
                hit = next((t for t in toks if t in low), None)
                if m.role in ("user", "assistant") and hit:
                    char_index = low.find(hit)
                    snippet = m.content[max(0, char_index - 80):char_index + 160].strip().replace("\n", " ")
                    out.append({"session": sess.id, "title": sess.title,
                                "when": sess.updated_at, "role": m.role,
                                "snippet": snippet, "message_id": msg_index})
                    break
            if len(out) >= limit:
                break
        return out

    def _resolve_session(self, sid: str) -> Session | None:
        sess = self.load(sid)
        if sess:
            return sess
        needle = (sid or "").strip().lower()
        if not needle:
            return None
        for row in self.list(200):
            row_id = str(row.get("id", ""))
            title = str(row.get("title", ""))
            if row_id.startswith(sid) or title.lower() == needle:
                return self.load(row_id)
        return None

    def _lineage_root(self, sid: str | None) -> str | None:
        cur = sid
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            sess = self.load(cur)
            if not sess or not sess.parent_id:
                return cur
            cur = sess.parent_id
        return cur

    def browse_sessions(self, limit: int = 10, *, current_session_id: str | None = None) -> dict:
        """Hermes-style browse shape: recent sessions without needing a query."""
        limit = max(1, min(int(limit or 10), 50))
        current_root = self._lineage_root(current_session_id)
        results = []
        for row in self.list(limit * 3):
            sess = self.load(row["id"])
            if not sess:
                continue
            root = self._lineage_root(sess.id)
            if current_root and root == current_root:
                continue
            if sess.parent_id:
                continue
            preview = next((m.content for m in sess.messages
                            if m.role in ("user", "assistant") and m.content), "")
            results.append({
                "session_id": sess.id,
                "title": sess.title,
                "created_at": sess.created_at,
                "updated_at": sess.updated_at,
                "message_count": len([m for m in sess.messages if m.role != "system"]),
                "preview": preview[:240],
            })
            if len(results) >= limit:
                break
        return {"success": True, "mode": "browse", "results": results, "count": len(results)}

    def read_session(self, sid: str, *, head: int = 20, tail: int = 10) -> dict:
        """Hermes-style read shape: bounded transcript dump by session id/title/prefix."""
        sess = self._resolve_session(sid)
        if not sess:
            return {"success": False, "mode": "read", "error": f"session_id not found: {sid}"}
        shaped = self._visible_messages(sess)
        total = len(shaped)
        truncated = total > head + tail
        messages = shaped[:head] + shaped[-tail:] if truncated else shaped
        out = {
            "success": True,
            "mode": "read",
            "session_id": sess.id,
            "session_meta": {
                "title": sess.title,
                "created_at": sess.created_at,
                "updated_at": sess.updated_at,
                "parent_id": sess.parent_id,
            },
            "message_count": total,
            "truncated": truncated,
            "messages": messages,
        }
        if truncated:
            out["message"] = (
                f"Session has {total} messages; showing first {head} + last {tail}. "
                "Use around_message_id with any shown id to scroll."
            )
        return out

    def messages_around(self, sid: str, around_message_id: int, *, window: int = 5,
                        current_session_id: str | None = None) -> dict:
        """Hermes-style scroll shape: a bounded message window centered on an anchor id."""
        sess = self._resolve_session(sid)
        if not sess:
            return {"success": False, "mode": "scroll", "error": f"session_id not found: {sid}"}
        current_root = self._lineage_root(current_session_id)
        if current_root and self._lineage_root(sess.id) == current_root:
            return {
                "success": False,
                "mode": "scroll",
                "error": "anchor lives in the current session lineage (already in active context)",
            }
        try:
            anchor = int(around_message_id)
        except (TypeError, ValueError):
            return {"success": False, "mode": "scroll", "error": "around_message_id must be an integer"}
        window = max(1, min(int(window or 5), 20))
        shaped = self._visible_messages(sess)
        positions = {m["id"]: i for i, m in enumerate(shaped)}
        if anchor not in positions:
            return {
                "success": False,
                "mode": "scroll",
                "session_id": sess.id,
                "error": f"around_message_id {anchor} not in session",
            }
        pos = positions[anchor]
        start = max(0, pos - window)
        end = min(len(shaped), pos + window + 1)
        messages = [
            {**m, **({"anchor": True} if m["id"] == anchor else {})}
            for m in shaped[start:end]
        ]
        return {
            "success": True,
            "mode": "scroll",
            "session_id": sess.id,
            "around_message_id": anchor,
            "session_meta": {
                "title": sess.title,
                "created_at": sess.created_at,
                "updated_at": sess.updated_at,
                "parent_id": sess.parent_id,
            },
            "window": window,
            "messages": messages,
            "messages_before": start,
            "messages_after": len(shaped) - end,
        }

    def discover_sessions(self, query: str, limit: int = 3, *,
                          role_filter: list[str] | None = None,
                          sort: str | None = None,
                          current_session_id: str | None = None) -> dict:
        """Hermes-style discovery shape: search plus message windows and bookends."""
        limit = max(1, min(int(limit or 3), 10))
        roles = {r for r in (role_filter or ["user", "assistant"]) if r}
        hits = self.search_messages(query, limit=limit * 8)
        if sort == "newest":
            hits.sort(key=lambda h: h.get("when") or "", reverse=True)
        elif sort == "oldest":
            hits.sort(key=lambda h: h.get("when") or "")
        current_root = self._lineage_root(current_session_id)
        results = []
        seen_roots: set[str] = set()
        for hit in hits:
            if roles and hit.get("role") not in roles:
                continue
            sid = hit["session"]
            root = self._lineage_root(sid) or sid
            if current_root and root == current_root:
                continue
            if root in seen_roots:
                continue
            sess = self.load(sid)
            if not sess:
                continue
            seen_roots.add(root)
            msg_id = hit.get("message_id")
            if msg_id is None:
                msg_id = self._first_matching_message_id(sess, query, roles=roles)
            view = (
                self.messages_around(sid, int(msg_id), window=5, current_session_id=None)
                if msg_id is not None else {"messages": [], "messages_before": 0, "messages_after": 0}
            )
            bookend_roles = {"user", "assistant"}
            visible = self._visible_messages(sess, roles=bookend_roles)
            entry = {
                "session_id": sid,
                "title": sess.title,
                "when": sess.updated_at,
                "matched_role": hit.get("role"),
                "match_message_id": msg_id,
                "snippet": hit.get("snippet", ""),
                "bookend_start": visible[:3],
                "messages": view.get("messages", []),
                "bookend_end": visible[-3:] if len(visible) > 3 else visible,
                "messages_before": view.get("messages_before", 0),
                "messages_after": view.get("messages_after", 0),
            }
            if root != sid:
                entry["parent_session_id"] = root
            results.append(entry)
            if len(results) >= limit:
                break
        return {
            "success": True,
            "mode": "discover",
            "query": query,
            "results": results,
            "count": len(results),
        }

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
