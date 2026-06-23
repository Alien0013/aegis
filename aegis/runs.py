"""Durable run history for AEGIS entry surfaces."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import config as cfg
from .types import new_id
from .util import now_iso


class RunStore:
    """SQLite-backed run log used by dashboard, automation, API, and eval tooling."""

    def __init__(self, path: str | Path | None = None):
        self.db = Path(path) if path else cfg.sub("runs.db")
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
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                       id TEXT PRIMARY KEY,
                       surface TEXT,
                       kind TEXT,
                       status TEXT,
                       title TEXT,
                       session_id TEXT,
                       trace_id TEXT,
                       started_at TEXT,
                       ended_at TEXT,
                       prompt_preview TEXT,
                       result_preview TEXT,
                       error TEXT,
                       data TEXT
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, started_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_runs_surface ON runs(surface, started_at)")

    def start(
        self,
        *,
        surface: str = "",
        kind: str = "",
        title: str = "",
        session_id: str = "",
        trace_id: str = "",
        prompt: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = {
            "id": new_id("run"),
            "surface": surface or kind or "agent",
            "kind": kind or surface or "agent",
            "status": "running",
            "title": title,
            "session_id": session_id,
            "trace_id": trace_id,
            "started_at": now_iso(),
            "ended_at": "",
            "prompt_preview": _clip(prompt),
            "result_preview": "",
            "error": "",
            "data": data or {},
        }
        self.write(run)
        return run

    def finish(
        self,
        run_id: str,
        *,
        status: str = "ok",
        trace_id: str = "",
        result: str = "",
        error: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        run = self.get(run_id)
        if run is None:
            return
        run["status"] = status
        run["ended_at"] = now_iso()
        if trace_id:
            run["trace_id"] = trace_id
        if result:
            run["result_preview"] = _clip(result)
        if error:
            run["error"] = _clip(error, 4000)
        if data:
            merged = dict(run.get("data") or {})
            merged.update(data)
            run["data"] = merged
        self.write(run)

    def update_data(self, run_id: str, data: dict[str, Any], *, trace_id: str = "") -> None:
        """Merge live metadata into a run without ending it."""
        if not run_id or not data:
            return
        run = self.get(run_id)
        if run is None:
            return
        if trace_id:
            run["trace_id"] = trace_id
        merged = dict(run.get("data") or {})
        merged.update(data)
        run["data"] = merged
        self.write(run)

    def write(self, run: dict[str, Any]) -> None:
        row = {
            "id": run.get("id") or new_id("run"),
            "surface": run.get("surface", ""),
            "kind": run.get("kind", ""),
            "status": run.get("status", "ok"),
            "title": run.get("title", ""),
            "session_id": run.get("session_id", ""),
            "trace_id": run.get("trace_id", ""),
            "started_at": run.get("started_at") or now_iso(),
            "ended_at": run.get("ended_at", ""),
            "prompt_preview": run.get("prompt_preview", ""),
            "result_preview": run.get("result_preview", ""),
            "error": run.get("error", ""),
            "data": json.dumps(run.get("data") or {}),
        }
        with self._conn() as c:
            c.execute(
                """INSERT INTO runs
                   (id, surface, kind, status, title, session_id, trace_id, started_at,
                    ended_at, prompt_preview, result_preview, error, data)
                   VALUES
                   (:id, :surface, :kind, :status, :title, :session_id, :trace_id,
                    :started_at, :ended_at, :prompt_preview, :result_preview, :error, :data)
                   ON CONFLICT(id) DO UPDATE SET
                    surface=excluded.surface, kind=excluded.kind, status=excluded.status,
                    title=excluded.title, session_id=excluded.session_id,
                    trace_id=excluded.trace_id, started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    prompt_preview=excluded.prompt_preview,
                    result_preview=excluded.result_preview, error=excluded.error,
                    data=excluded.data""",
                row,
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM runs WHERE id=? OR id LIKE ? ORDER BY started_at DESC LIMIT 1",
                (run_id, run_id + "%"),
            ).fetchone()
        return _row(row) if row else None

    def list(
        self,
        *,
        limit: int = 50,
        surface: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if surface:
            clauses.append("surface=?")
            args.append(surface)
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        if status:
            clauses.append("status=?")
            args.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(limit)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM runs {where} ORDER BY started_at DESC LIMIT ?",
                args,
            ).fetchall()
        return [_row(r) for r in rows]


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    try:
        out["data"] = json.loads(out.get("data") or "{}")
    except json.JSONDecodeError:
        out["data"] = {}
    return out


def _clip(text: str, limit: int = 1200) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + "\n...[truncated]"
