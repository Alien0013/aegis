"""Durable outbound delivery queue with retries (survives restarts)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .. import config as cfg
from ..platforms import normalize_platform_name
from ..redact import redact_secret_values, redact_secrets
from ..util import truncate


_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 200
_TEXT_PREVIEW_CHARS = 1000


class DeliveryQueue:
    def __init__(self):
        self.db = cfg.sub("gateway_queue.db")
        with self._c() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS outbox (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           platform TEXT, chat_id TEXT, text TEXT,
                           attempts INTEGER DEFAULT 0, status TEXT DEFAULT 'pending',
                           next_at REAL DEFAULT 0, created_at REAL)""")
            columns = {row["name"] for row in c.execute("PRAGMA table_info(outbox)").fetchall()}
            if "thread_id" not in columns:
                c.execute("ALTER TABLE outbox ADD COLUMN thread_id TEXT DEFAULT ''")
            if "metadata" not in columns:
                c.execute("ALTER TABLE outbox ADD COLUMN metadata TEXT DEFAULT '{}'")

    def _c(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(
        self,
        platform: str,
        chat_id: str,
        text: str,
        *,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        platform = normalize_platform_name(platform, default=str(platform or "").strip().lower())
        metadata = dict(metadata or {})
        if thread_id:
            metadata.setdefault("thread_id", str(thread_id))
        with self._c() as c:
            c.execute(
                "INSERT INTO outbox (platform, chat_id, text, thread_id, metadata, next_at, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    platform,
                    chat_id,
                    text,
                    str(thread_id or ""),
                    json.dumps(metadata, sort_keys=True),
                    time.time(),
                    time.time(),
                ),
            )

    def due(self) -> list[sqlite3.Row]:
        with self._c() as c:
            return c.execute("SELECT * FROM outbox WHERE status='pending' AND next_at<=? "
                             "ORDER BY id LIMIT 50", (time.time(),)).fetchall()

    def mark_sent(self, row_id: int) -> None:
        with self._c() as c:
            c.execute("UPDATE outbox SET status='sent' WHERE id=?", (row_id,))

    def mark_failed(self, row_id: int, attempts: int, max_attempts: int = 5) -> None:
        with self._c() as c:
            if attempts + 1 >= max_attempts:
                c.execute("UPDATE outbox SET status='failed', attempts=? WHERE id=?",
                          (attempts + 1, row_id))
            else:
                backoff = (2 ** (attempts + 1)) * 30        # 60s, 120s, 240s, …
                c.execute("UPDATE outbox SET attempts=?, next_at=? WHERE id=?",
                          (attempts + 1, time.time() + backoff, row_id))

    def pending_count(self) -> int:
        with self._c() as c:
            return c.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]

    def stats(self) -> dict[str, Any]:
        with self._c() as c:
            rows = c.execute(
                "SELECT status, COUNT(*) AS count FROM outbox GROUP BY status"
            ).fetchall()
        statuses = {"pending": 0, "sent": 0, "failed": 0, "discarded": 0}
        for row in rows:
            statuses[str(row["status"] or "")] = int(row["count"] or 0)
        return {"total": sum(statuses.values()), **statuses, "statuses": dict(statuses)}

    def list_messages(
        self,
        status: str | None = None,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> list[dict[str, Any]]:
        limit = _safe_limit(limit)
        status_filter = str(status or "").strip().lower()
        with self._c() as c:
            if status_filter:
                rows = c.execute(
                    "SELECT * FROM outbox WHERE status=? ORDER BY id DESC LIMIT ?",
                    (status_filter, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM outbox ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [_message_dict(row) for row in rows]

    def dead_letters(self, limit: int = _DEFAULT_LIST_LIMIT) -> list[dict[str, Any]]:
        return self.list_messages(status="failed", limit=limit)

    def retry(self, row_id: int) -> dict[str, Any]:
        row_id = _row_id(row_id)
        if row_id is None:
            return {"ok": False, "id": row_id, "error": "not_found"}
        with self._c() as c:
            c.execute(
                "UPDATE outbox SET status='pending', attempts=0, next_at=? WHERE id=?",
                (time.time(), row_id),
            )
            row = c.execute("SELECT * FROM outbox WHERE id=?", (row_id,)).fetchone()
        if row is None:
            return {"ok": False, "id": row_id, "error": "not_found"}
        return {"ok": True, **_message_dict(row)}

    def discard(self, row_id: int) -> dict[str, Any]:
        row_id = _row_id(row_id)
        if row_id is None:
            return {"ok": False, "id": row_id, "error": "not_found"}
        with self._c() as c:
            c.execute("UPDATE outbox SET status='discarded' WHERE id=?", (row_id,))
            row = c.execute("SELECT * FROM outbox WHERE id=?", (row_id,)).fetchone()
        if row is None:
            return {"ok": False, "id": row_id, "error": "not_found"}
        return {"ok": True, **_message_dict(row)}

    def run(self, send_fn, poll: int = 5) -> None:
        """Drain due items, calling send_fn(platform, chat_id, text)->bool."""
        while True:
            for row in self.due():
                try:
                    metadata = _row_metadata(row)
                    ok = send_fn(row["platform"], row["chat_id"], row["text"], metadata=metadata)
                except TypeError:
                    try:
                        ok = send_fn(row["platform"], row["chat_id"], row["text"])
                    except Exception:  # noqa: BLE001
                        ok = False
                except Exception:  # noqa: BLE001
                    ok = False
                if ok:
                    self.mark_sent(row["id"])
                else:
                    self.mark_failed(row["id"], row["attempts"])
            time.sleep(poll)


OutboxQueue = DeliveryQueue


def _safe_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = _DEFAULT_LIST_LIMIT
    return max(0, min(value, _MAX_LIST_LIMIT))


def _row_id(row_id: int) -> int | None:
    try:
        value = int(row_id)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
    text = redact_secrets(str(row["text"] or ""))
    metadata = _jsonable(redact_secret_values(_row_metadata(row)))
    return {
        "id": int(row["id"]),
        "platform": str(row["platform"] or ""),
        "chat_id": str(row["chat_id"] or ""),
        "thread_id": str(row["thread_id"] or ""),
        "text": truncate(text, _TEXT_PREVIEW_CHARS),
        "text_truncated": len(text) > _TEXT_PREVIEW_CHARS,
        "attempts": int(row["attempts"] or 0),
        "status": str(row["status"] or ""),
        "next_at": float(row["next_at"] or 0),
        "created_at": float(row["created_at"] or 0),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def _row_metadata(row: sqlite3.Row) -> dict[str, Any]:
    try:
        data = json.loads(row["metadata"] or "{}")
    except Exception:  # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    thread_id = str(row["thread_id"] or "")
    if thread_id:
        data.setdefault("thread_id", thread_id)
    return data
