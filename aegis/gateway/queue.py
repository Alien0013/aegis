"""Durable outbound delivery queue with retries (survives restarts)."""

from __future__ import annotations

import sqlite3
import time

from .. import config as cfg


class DeliveryQueue:
    def __init__(self):
        self.db = cfg.sub("gateway_queue.db")
        with self._c() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS outbox (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           platform TEXT, chat_id TEXT, text TEXT,
                           attempts INTEGER DEFAULT 0, status TEXT DEFAULT 'pending',
                           next_at REAL DEFAULT 0, created_at REAL)""")

    def _c(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, platform: str, chat_id: str, text: str) -> None:
        with self._c() as c:
            c.execute("INSERT INTO outbox (platform, chat_id, text, next_at, created_at) "
                      "VALUES (?,?,?,?,?)", (platform, chat_id, text, time.time(), time.time()))

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

    def run(self, send_fn, poll: int = 5) -> None:
        """Drain due items, calling send_fn(platform, chat_id, text)->bool."""
        while True:
            for row in self.due():
                try:
                    ok = send_fn(row["platform"], row["chat_id"], row["text"])
                except Exception:  # noqa: BLE001
                    ok = False
                if ok:
                    self.mark_sent(row["id"])
                else:
                    self.mark_failed(row["id"], row["attempts"])
            time.sleep(poll)
