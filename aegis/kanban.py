"""Multi-agent kanban orchestration kernel, SQLite-backed.

A durable work board several agents (or a human + agents) share. Beyond a flat queue,
it is a dependency-aware orchestration kernel:

* **Dependency graph.** A card created with ``parents=[...]`` starts in ``todo`` and is
  gated — it auto-promotes to ``ready`` only once every parent reaches ``done``. Links
  live in ``task_links``; promotion happens on each completion and on every dispatch pass.
* **Attempt history.** Each execution is a *run* in ``task_runs`` with an ``outcome``
  (completed/failed/timed_out/reclaimed/blocked), summary, metadata, and error — so a
  retry can read what the prior attempt did and avoid repeating it.
* **Audit log.** Every state change appends to ``task_events`` (append-only).
* **Structured handoff.** ``complete(summary=, metadata=)`` carries a human-readable
  summary plus machine-readable facts downstream workers read via ``show``.
* **Liveness.** ``heartbeat`` stamps progress; the dispatcher reclaims tasks that go
  silent past a timeout back to ``ready`` (no failure penalty).
* **Isolation.** ``tenant`` namespaces work; ``workspace_kind`` (scratch/dir/worktree)
  controls where a worker operates.

Status flow:  ``triage → todo → ready → in_progress → (blocked|review) → done → archived``
plus ``scheduled`` (time-gated). Claiming is atomic (one conditional UPDATE) so two
workers never grab the same card. Everything is plain SQLite — no external services.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import config as cfg
from .types import new_id
from .util import now_iso, truncate

# Full lifecycle. AEGIS keeps `in_progress` as the persisted running state.
STATUSES = ("triage", "todo", "scheduled", "ready", "in_progress",
            "blocked", "review", "done", "archived")
# Terminal/active sets used by the dependency engine and dispatcher.
_DONE = "done"
_GATED = "todo"
RUN_OUTCOMES = ("completed", "failed", "timed_out", "crashed", "reclaimed", "blocked")

# Columns added to ``tasks`` beyond the original flat schema (additive migration).
_EXTRA_TASK_COLS = {
    "tenant": "TEXT",
    "workspace_kind": "TEXT",
    "workspace_path": "TEXT",
    "branch_name": "TEXT",
    "created_by": "TEXT",
    "result": "TEXT",
    "consecutive_failures": "INTEGER",
    "last_heartbeat_at": "TEXT",
    "current_run_id": "INTEGER",
    "max_runtime_seconds": "INTEGER",
    "max_retries": "INTEGER",
    "skills": "TEXT",
    "completed_at": "TEXT",
}


@dataclass
class Task:
    id: str
    title: str
    body: str
    status: str
    assignee: str
    priority: int
    created_at: str
    updated_at: str
    run_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    tenant: str = ""
    workspace_kind: str = "scratch"
    workspace_path: str = ""
    branch_name: str = ""
    created_by: str = ""
    result: str = ""
    consecutive_failures: int = 0
    last_heartbeat_at: str = ""
    current_run_id: int = 0
    max_runtime_seconds: int = 0
    max_retries: int = 0
    skills: str = ""
    completed_at: str = ""

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Task":
        keys = set(row.keys())

        def g(name: str, default: Any) -> Any:
            return row[name] if name in keys and row[name] is not None else default

        return Task(
            id=row["id"], title=row["title"], body=g("body", ""),
            status=row["status"], assignee=g("assignee", ""), priority=g("priority", 0),
            created_at=g("created_at", ""), updated_at=g("updated_at", ""),
            run_id=g("run_id", ""), session_id=g("session_id", ""), trace_id=g("trace_id", ""),
            tenant=g("tenant", ""), workspace_kind=g("workspace_kind", "scratch"),
            workspace_path=g("workspace_path", ""), branch_name=g("branch_name", ""),
            created_by=g("created_by", ""), result=g("result", ""),
            consecutive_failures=g("consecutive_failures", 0),
            last_heartbeat_at=g("last_heartbeat_at", ""), current_run_id=g("current_run_id", 0),
            max_runtime_seconds=g("max_runtime_seconds", 0), max_retries=g("max_retries", 0),
            skills=g("skills", ""), completed_at=g("completed_at", ""),
        )


@dataclass
class Comment:
    id: str
    task_id: str
    text: str
    created_at: str
    author: str = ""

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Comment":
        keys = set(row.keys())
        return Comment(
            id=row["id"], task_id=row["task_id"], text=row["text"], created_at=row["created_at"],
            author=row["author"] if "author" in keys and row["author"] is not None else "",
        )


@dataclass
class Run:
    id: int
    task_id: str
    profile: str
    status: str
    outcome: str = ""
    summary: str = ""
    metadata: dict = field(default_factory=dict)
    error: str = ""
    started_at: str = ""
    ended_at: str = ""

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Run":
        try:
            meta = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        return Run(
            id=row["id"], task_id=row["task_id"], profile=row["profile"] or "",
            status=row["status"] or "", outcome=row["outcome"] or "", summary=row["summary"] or "",
            metadata=meta if isinstance(meta, dict) else {}, error=row["error"] or "",
            started_at=row["started_at"] or "", ended_at=row["ended_at"] or "",
        )


@dataclass
class Event:
    id: int
    task_id: str
    kind: str
    payload: dict
    created_at: str
    run_id: int = 0

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Event":
        try:
            payload = json.loads(row["payload"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        return Event(id=row["id"], task_id=row["task_id"], kind=row["kind"],
                     payload=payload if isinstance(payload, dict) else {},
                     created_at=row["created_at"], run_id=row["run_id"] or 0)


class KanbanStore:
    """SQLite-backed dependency-aware task board. Safe for concurrent claimers."""

    def __init__(self):
        self.db = cfg.sub("kanban.db")
        self._init()

    def _conn(self) -> sqlite3.Connection:
        cfg.get_home().mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                       id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT,
                       status TEXT NOT NULL DEFAULT 'ready', assignee TEXT,
                       priority INTEGER NOT NULL DEFAULT 0,
                       created_at TEXT, updated_at TEXT,
                       run_id TEXT, session_id TEXT, trace_id TEXT)"""
            )
            cols = {r[1] for r in c.execute("PRAGMA table_info(tasks)").fetchall()}
            for name in ("run_id", "session_id", "trace_id"):
                if name not in cols:
                    c.execute(f"ALTER TABLE tasks ADD COLUMN {name} TEXT")
            for name, sqltype in _EXTRA_TASK_COLS.items():
                if name not in cols:
                    c.execute(f"ALTER TABLE tasks ADD COLUMN {name} {sqltype}")
            c.execute(
                """CREATE TABLE IF NOT EXISTS comments (
                       id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                       text TEXT NOT NULL, created_at TEXT)"""
            )
            ccols = {r[1] for r in c.execute("PRAGMA table_info(comments)").fetchall()}
            if "author" not in ccols:
                c.execute("ALTER TABLE comments ADD COLUMN author TEXT")
            c.execute(
                """CREATE TABLE IF NOT EXISTS task_links (
                       parent_id TEXT NOT NULL, child_id TEXT NOT NULL,
                       PRIMARY KEY (parent_id, child_id))"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS task_events (
                       id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                       run_id INTEGER, kind TEXT NOT NULL, payload TEXT, created_at TEXT)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS task_runs (
                       id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                       profile TEXT, status TEXT, outcome TEXT, summary TEXT,
                       metadata TEXT, error TEXT, started_at TEXT, ended_at TEXT)"""
            )
            for stmt in (
                "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
                "CREATE INDEX IF NOT EXISTS idx_links_child ON task_links(child_id)",
                "CREATE INDEX IF NOT EXISTS idx_links_parent ON task_links(parent_id)",
                "CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id, id)",
                "CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id, id)",
            ):
                c.execute(stmt)

    # -- events -------------------------------------------------------------
    def event(self, task_id: str, kind: str, payload: dict | None = None,
              run_id: int = 0) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) "
                "VALUES (?,?,?,?,?)",
                (task_id, run_id or None, kind, json.dumps(payload or {}), now_iso()),
            )

    def events(self, task_id: str, limit: int = 200) -> list[Event]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY id ASC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [Event.from_row(r) for r in rows]

    # -- mutations ----------------------------------------------------------
    def create(self, title: str, body: str = "", priority: int = 0, *,
               assignee: str = "", parents: list[str] | None = None, tenant: str = "",
               workspace: str = "scratch", branch: str = "", created_by: str = "",
               skills: str = "", max_runtime_seconds: int = 0, max_retries: int = 0) -> Task:
        """Create a card. With ``parents`` that are not all ``done``, the card starts
        gated in ``todo`` and auto-promotes to ``ready`` when every parent completes."""
        parents = [p for p in (parents or []) if p]
        kind, path = _parse_workspace(workspace)
        now = now_iso()
        with self._conn() as c:
            gated = bool(parents) and not self._all_parents_done(c, parents)
            status = _GATED if gated else "ready"
            task = Task(
                id=new_id("task"), title=title, body=body, status=status,
                assignee=assignee, priority=int(priority), created_at=now, updated_at=now,
                tenant=tenant, workspace_kind=kind, workspace_path=path, branch_name=branch,
                created_by=created_by or "user", skills=skills,
                max_runtime_seconds=int(max_runtime_seconds or 0),
                max_retries=int(max_retries or 0),
            )
            c.execute(
                """INSERT INTO tasks
                   (id,title,body,status,assignee,priority,created_at,updated_at,
                    run_id,session_id,trace_id,tenant,workspace_kind,workspace_path,
                    branch_name,created_by,result,consecutive_failures,last_heartbeat_at,
                    current_run_id,max_runtime_seconds,max_retries,skills,completed_at)
                   VALUES
                   (:id,:title,:body,:status,:assignee,:priority,:created_at,:updated_at,
                    '','','',:tenant,:workspace_kind,:workspace_path,:branch_name,:created_by,
                    '',0,'',0,:max_runtime_seconds,:max_retries,:skills,'')""",
                task.__dict__,
            )
            for p in parents:
                c.execute("INSERT OR IGNORE INTO task_links (parent_id, child_id) VALUES (?,?)",
                          (p, task.id))
        self.event(task.id, "created",
                   {"title": title, "assignee": assignee, "parents": parents, "status": status})
        return task

    def link(self, parent_id: str, child_id: str) -> bool:
        """Add a parent→child dependency. If the parent isn't done yet, gate the child."""
        with self._conn() as c:
            if not c.execute("SELECT 1 FROM tasks WHERE id=?", (parent_id,)).fetchone():
                return False
            if not c.execute("SELECT 1 FROM tasks WHERE id=?", (child_id,)).fetchone():
                return False
            c.execute("INSERT OR IGNORE INTO task_links (parent_id, child_id) VALUES (?,?)",
                      (parent_id, child_id))
            child = c.execute("SELECT status FROM tasks WHERE id=?", (child_id,)).fetchone()
            if child and child["status"] == "ready" and not self._all_parents_done(c, [], child_id):
                c.execute("UPDATE tasks SET status='todo', updated_at=? WHERE id=?",
                          (now_iso(), child_id))
        self.event(child_id, "linked", {"parent": parent_id})
        return True

    def unlink(self, parent_id: str, child_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM task_links WHERE parent_id=? AND child_id=?",
                            (parent_id, child_id))
            ok = cur.rowcount > 0
        if ok:
            self.event(child_id, "unlinked", {"parent": parent_id})
            self.promote_ready()
        return ok

    def parents(self, task_id: str) -> list[str]:
        with self._conn() as c:
            return [r["parent_id"] for r in
                    c.execute("SELECT parent_id FROM task_links WHERE child_id=?", (task_id,))]

    def children(self, task_id: str) -> list[str]:
        with self._conn() as c:
            return [r["child_id"] for r in
                    c.execute("SELECT child_id FROM task_links WHERE parent_id=?", (task_id,))]

    def _all_parents_done(self, c: sqlite3.Connection, parents: list[str],
                          child_id: str | None = None) -> bool:
        if child_id is not None:
            parents = [r["parent_id"] for r in
                       c.execute("SELECT parent_id FROM task_links WHERE child_id=?", (child_id,))]
        if not parents:
            return True
        rows = c.execute(
            f"SELECT status FROM tasks WHERE id IN ({','.join('?' * len(parents))})", parents
        ).fetchall()
        return bool(rows) and all(r["status"] == _DONE for r in rows)

    def promote_ready(self) -> list[str]:
        """Promote every ``todo`` card whose parents are all done to ``ready``."""
        promoted: list[str] = []
        with self._conn() as c:
            todos = c.execute("SELECT id FROM tasks WHERE status=?", (_GATED,)).fetchall()
            for r in todos:
                if self._all_parents_done(c, [], r["id"]):
                    c.execute("UPDATE tasks SET status='ready', updated_at=? WHERE id=?",
                              (now_iso(), r["id"]))
                    promoted.append(r["id"])
        for tid in promoted:
            self.event(tid, "promoted", {"to": "ready"})
        return promoted

    def claim(self, task_id: str, worker: str) -> bool:
        """Atomically claim a *ready* task for ``worker`` (ready → in_progress)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status='in_progress', assignee=?, updated_at=?, "
                "last_heartbeat_at=? WHERE id=? AND status='ready'",
                (worker, now_iso(), now_iso(), task_id),
            )
            ok = cur.rowcount > 0
        if ok:
            self.event(task_id, "claimed", {"worker": worker})
        return ok

    def claim_next(self, worker: str, lane: str = "") -> Task | None:
        """Atomically claim the highest-priority oldest ready task. With ``lane``, cards
        pre-assigned to another lane are skipped."""
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                """SELECT * FROM tasks WHERE status='ready'
                     AND (assignee='' OR assignee IS NULL OR assignee=?)
                   ORDER BY priority DESC, created_at ASC LIMIT 1""",
                (lane or worker,),
            ).fetchone()
            if row is None:
                c.execute("COMMIT")
                return None
            c.execute(
                "UPDATE tasks SET status='in_progress', assignee=?, updated_at=?, "
                "last_heartbeat_at=? WHERE id=?",
                (worker, now_iso(), now_iso(), row["id"]),
            )
            c.execute("COMMIT")
        self.event(row["id"], "claimed", {"worker": worker})
        task = Task.from_row(row)
        task.status, task.assignee = "in_progress", worker
        return task

    def _set_status(self, task_id: str, status: str) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                            (status, now_iso(), task_id))
            return cur.rowcount > 0

    def complete(self, task_id: str, summary: str = "", metadata: dict | None = None,
                 result: str = "") -> bool:
        """Mark a task done with an optional structured handoff, then auto-promote any
        children whose parents are now all complete."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status='done', updated_at=?, completed_at=?, result=? WHERE id=?",
                (now_iso(), now_iso(), result or summary, task_id),
            )
            ok = cur.rowcount > 0
        if not ok:
            return False
        self.event(task_id, "completed", {"summary": summary, "metadata": metadata or {}})
        if summary:
            self.comment(task_id, summary, author="worker")
        self.promote_ready()
        return True

    def assign(self, task_id: str, who: str) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE tasks SET assignee=?, updated_at=? WHERE id=?",
                            (who, now_iso(), task_id))
            ok = cur.rowcount > 0
        if ok:
            self.event(task_id, "assigned", {"assignee": who})
        return ok

    def block(self, task_id: str, reason: str = "") -> bool:
        ok = self._set_status(task_id, "blocked")
        if ok:
            self.event(task_id, "blocked", {"reason": reason})
            if reason:
                self.comment(task_id, f"BLOCKED: {reason}", author="worker")
        return ok

    def unblock(self, task_id: str) -> bool:
        """Return a blocked/scheduled task to ``ready`` (review answered / time elapsed)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status='ready', updated_at=? WHERE id=? "
                "AND status IN ('blocked','scheduled','review')",
                (now_iso(), task_id),
            )
            ok = cur.rowcount > 0
        if ok:
            self.event(task_id, "unblocked", {})
        return ok

    def review(self, task_id: str, reason: str = "") -> bool:
        ok = self._set_status(task_id, "review")
        if ok:
            self.event(task_id, "review", {"reason": reason})
            if reason:
                self.comment(task_id, f"REVIEW: {reason}", author="worker")
        return ok

    def schedule(self, task_id: str) -> bool:
        ok = self._set_status(task_id, "scheduled")
        if ok:
            self.event(task_id, "scheduled", {})
        return ok

    def promote(self, task_id: str) -> bool:
        """Manual recovery: move a todo/blocked card straight to ready."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status='ready', updated_at=? WHERE id=? "
                "AND status IN ('todo','blocked','triage','scheduled')",
                (now_iso(), task_id),
            )
            ok = cur.rowcount > 0
        if ok:
            self.event(task_id, "promoted", {"to": "ready", "manual": True})
        return ok

    def archive(self, task_id: str) -> bool:
        ok = self._set_status(task_id, "archived")
        if ok:
            self.event(task_id, "archived", {})
        return ok

    def set_workspace(self, task_id: str, path: str) -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE tasks SET workspace_path=?, updated_at=? WHERE id=?",
                            (path, now_iso(), task_id))
            return cur.rowcount > 0

    def heartbeat(self, task_id: str, note: str = "") -> bool:
        with self._conn() as c:
            cur = c.execute("UPDATE tasks SET last_heartbeat_at=?, updated_at=? WHERE id=?",
                            (now_iso(), now_iso(), task_id))
            ok = cur.rowcount > 0
        if ok:
            self.event(task_id, "heartbeat", {"note": note})
        return ok

    def record_breadcrumbs(self, task_id: str, *, run_id: str = "", session_id: str = "",
                           trace_id: str = "") -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET run_id=?, session_id=?, trace_id=?, updated_at=? WHERE id=?",
                (run_id, session_id, trace_id, now_iso(), task_id),
            )
            return cur.rowcount > 0

    def reopen(self, task_id: str) -> bool:
        """Send a blocked/done task back to ``ready`` and drop its assignee."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status='ready', assignee='', updated_at=? WHERE id=?",
                (now_iso(), task_id),
            )
            ok = cur.rowcount > 0
        if ok:
            self.event(task_id, "reopened", {})
        return ok

    def comment(self, task_id: str, text: str, author: str = "") -> Comment:
        cm = Comment(id=new_id("cmt"), task_id=task_id, text=text, created_at=now_iso(),
                     author=author)
        with self._conn() as c:
            c.execute(
                "INSERT INTO comments (id,task_id,text,created_at,author) VALUES (?,?,?,?,?)",
                (cm.id, cm.task_id, cm.text, cm.created_at, cm.author),
            )
        return cm

    # -- runs (attempt history) --------------------------------------------
    def start_run(self, task_id: str, profile: str = "") -> int:
        now = now_iso()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO task_runs (task_id, profile, status, started_at) VALUES (?,?,?,?)",
                (task_id, profile, "running", now),
            )
            run_id = int(cur.lastrowid)
            c.execute("UPDATE tasks SET current_run_id=?, updated_at=?, last_heartbeat_at=? "
                      "WHERE id=?", (run_id, now, now, task_id))
        self.event(task_id, "run_started", {"profile": profile}, run_id=run_id)
        return run_id

    def end_run(self, run_id: int, outcome: str, *, summary: str = "",
                metadata: dict | None = None, error: str = "") -> bool:
        with self._conn() as c:
            row = c.execute("SELECT task_id FROM task_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return False
            c.execute(
                "UPDATE task_runs SET status='closed', outcome=?, summary=?, metadata=?, "
                "error=?, ended_at=? WHERE id=?",
                (outcome, summary, json.dumps(metadata or {}), error, now_iso(), run_id),
            )
            task_id = row["task_id"]
            if outcome in ("failed", "crashed", "timed_out"):
                c.execute("UPDATE tasks SET consecutive_failures=consecutive_failures+1 WHERE id=?",
                          (task_id,))
            elif outcome == "completed":
                c.execute("UPDATE tasks SET consecutive_failures=0 WHERE id=?", (task_id,))
        self.event(task_id, "run_ended", {"outcome": outcome, "summary": summary, "error": error},
                   run_id=run_id)
        return True

    def runs(self, task_id: str) -> list[Run]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM task_runs WHERE task_id=? ORDER BY id ASC",
                             (task_id,)).fetchall()
        return [Run.from_row(r) for r in rows]

    # -- created_cards gate -------------------------------------------------
    def verify_created_cards(self, ids: list[str], profile: str) -> tuple[list[str], list[str]]:
        """Split ``ids`` into (valid, rejected): a card is valid only if it exists and was
        created by ``profile``. Anti-hallucination gate for kanban_complete(created_cards=…)."""
        ok, bad = [], []
        with self._conn() as c:
            for tid in ids:
                row = c.execute("SELECT created_by FROM tasks WHERE id=?", (tid,)).fetchone()
                if row is None:
                    bad.append(tid)
                elif profile and row["created_by"] and row["created_by"] != profile:
                    bad.append(tid)
                else:
                    ok.append(tid)
        return ok, bad

    # -- stale reclaim ------------------------------------------------------
    def reclaim_stale(self, timeout_seconds: float) -> list[str]:
        """Return ``in_progress`` tasks whose last heartbeat is older than the timeout to
        ``ready`` with no failure penalty. Returns reclaimed ids."""
        cutoff = datetime.now(timezone.utc).timestamp() - timeout_seconds
        reclaimed: list[str] = []
        with self._conn() as c:
            rows = c.execute("SELECT id, last_heartbeat_at, current_run_id, updated_at "
                             "FROM tasks WHERE status='in_progress'").fetchall()
            for r in rows:
                stamp = _parse_ts(r["last_heartbeat_at"]) or _parse_ts(r["updated_at"])
                if stamp is not None and stamp < cutoff:
                    c.execute("UPDATE tasks SET status='ready', assignee='', current_run_id=0, "
                              "updated_at=? WHERE id=?", (now_iso(), r["id"]))
                    reclaimed.append((r["id"], r["current_run_id"]))
        out = []
        for tid, run_id in reclaimed:
            if run_id:
                self.end_run(int(run_id), "reclaimed", summary="task reclaimed (stale)")
            self.event(tid, "reclaimed", {})
            out.append(tid)
        return out

    # -- queries ------------------------------------------------------------
    def _resolve(self, c: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            return row
        rows = c.execute("SELECT * FROM tasks WHERE id LIKE ? LIMIT 2",
                         (task_id + "%",)).fetchall()
        return rows[0] if len(rows) == 1 else None

    def show(self, task_id: str) -> Task | None:
        with self._conn() as c:
            row = self._resolve(c, task_id)
            return Task.from_row(row) if row else None

    def worker_context(self, task_id: str) -> dict:
        """Everything a worker needs to orient: the task, parent handoffs (summary +
        metadata), prior runs (for retries), and the comment thread."""
        task = self.show(task_id)
        if task is None:
            return {}
        parent_handoffs = []
        for pid in self.parents(task.id):
            p = self.show(pid)
            if p is None:
                continue
            last = [r for r in self.runs(pid) if r.outcome == "completed"]
            parent_handoffs.append({
                "id": pid, "title": p.title, "result": p.result,
                "summary": last[-1].summary if last else "",
                "metadata": last[-1].metadata if last else {},
            })
        return {
            "task": {"id": task.id, "title": task.title, "body": task.body,
                     "status": task.status, "assignee": task.assignee, "tenant": task.tenant,
                     "workspace_kind": task.workspace_kind, "workspace_path": task.workspace_path},
            "parents": parent_handoffs,
            "runs": [{"profile": r.profile, "outcome": r.outcome, "summary": r.summary,
                      "error": r.error} for r in self.runs(task.id)],
            "comments": [{"author": cm.author, "text": cm.text, "at": cm.created_at}
                         for cm in self.comments(task.id)],
        }

    def comments(self, task_id: str) -> list[Comment]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM comments WHERE task_id=? ORDER BY created_at ASC",
                             (task_id,)).fetchall()
        return [Comment.from_row(r) for r in rows]

    def list(self, status: str | None = None, assignee: str | None = None,
             tenant: str | None = None) -> list[Task]:
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if assignee:
            clauses.append("assignee=?")
            params.append(assignee)
        if tenant:
            clauses.append("tenant=?")
            params.append(tenant)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM tasks{where} ORDER BY priority DESC, created_at ASC", params
            ).fetchall()
        return [Task.from_row(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as c:
            by_status = dict(c.execute(
                "SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall())
            by_assignee = dict(c.execute(
                "SELECT assignee, COUNT(*) FROM tasks WHERE assignee!='' "
                "GROUP BY assignee").fetchall())
        return {"by_status": by_status, "by_assignee": by_assignee}


# -- helpers ----------------------------------------------------------------
def _parse_workspace(spec: str) -> tuple[str, str]:
    """``scratch`` | ``dir:<path>`` | ``worktree`` | ``worktree:<path>`` → (kind, path)."""
    spec = (spec or "scratch").strip()
    if spec in ("scratch", "worktree", "dir"):
        return spec, ""
    if ":" in spec:
        kind, _, path = spec.partition(":")
        if kind in ("dir", "worktree"):
            return kind, path.strip()
    return "scratch", ""


def _parse_ts(value: str) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


# -- dispatch ---------------------------------------------------------------
def dispatch(config, worker: str = "agent") -> Task | None:
    """Claim the next ready task, run it through an Agent, mark it done (single task).

    Records a run row (attempt history). On failure the task is blocked (not lost) with
    the error recorded and the run closed as ``crashed``. Returns the processed task.
    """
    from .surface import SurfaceRunner

    store = KanbanStore()
    store.promote_ready()
    task = store.claim_next(worker)
    if task is None:
        return None
    run_id = store.start_run(task.id, profile=worker)
    prompt = task.title if not task.body else f"{task.title}\n\n{task.body}"
    runner = SurfaceRunner(config, include_mcp=True, reuse_agents=False)
    try:
        result = runner.run_prompt(
            prompt, title=task.title, surface="kanban",
            meta={"kanban_task_id": task.id, "kanban_worker": worker, "kanban_run_id": run_id},
        )
        store.record_breadcrumbs(task.id, run_id=result.run_id, session_id=result.session.id,
                                 trace_id=result.trace_id)
        store.end_run(run_id, "completed", summary=(result.text or "")[:1000])
        store.complete(task.id, summary=(result.text or "")[:1000])
    except Exception as e:  # noqa: BLE001
        store.end_run(run_id, "crashed", error=str(e))
        store.block(task.id, f"agent error: {e}")
    return store.show(task.id)


# -- CLI --------------------------------------------------------------------
def _print(s: str = "") -> None:
    print(s)


def _fmt_task(t: Task) -> str:
    who = f" @{t.assignee}" if t.assignee else ""
    dep = " ⛓" if t.status == "todo" else ""
    return f"  {t.id[:13]}  [{t.status:<11}] P{t.priority}{who}{dep}  {truncate(t.title, 56)}"


def cmd_kanban(args, config) -> int:
    """CLI: aegis kanban <create|list|show|claim|complete|assign|block|unblock|link|
    promote|runs|heartbeat|stats|dispatch|decompose|run> ..."""
    action = getattr(args, "action", None)
    store = KanbanStore()

    def _arg(name: str, default: Any = None) -> Any:
        return getattr(args, name, default)

    # id-taking verbs accept the id positionally (e.g. `aegis kanban complete <id>`)
    # as well as via --id, matching the AEGIS CLI ergonomics.
    if action not in ("create", "decompose", "list", "stats", "dispatch", "run") \
            and not _arg("id") and _arg("title"):
        args.id = _arg("title")

    if action == "create":
        title = _arg("title")
        if not title:
            _print("usage: aegis kanban create <title> [--body ...] [--priority N] "
                   "[--assignee NAME] [--parent ID ...]")
            return 1
        parents = _arg("parent") or _arg("parents") or []
        if isinstance(parents, str):
            parents = [parents]
        task = store.create(title, body=_arg("body", "") or "",
                            priority=int(_arg("priority", 0) or 0),
                            assignee=_arg("assignee", "") or "", parents=list(parents),
                            tenant=_arg("tenant", "") or "",
                            workspace=_arg("workspace", "scratch") or "scratch")
        _print(f"created {task.id} [{task.status}]")
        return 0

    if action == "list":
        tasks = store.list(status=_arg("status"), assignee=_arg("assignee"),
                           tenant=_arg("tenant"))
        if not tasks:
            _print("(no tasks)")
            return 0
        for t in tasks:
            _print(_fmt_task(t))
        return 0

    if action == "show":
        tid = _arg("id")
        if not tid:
            _print("usage: aegis kanban show <id>")
            return 1
        task = store.show(tid)
        if not task:
            _print(f"task '{tid}' not found")
            return 1
        _print(f"{task.id}  [{task.status}]  P{task.priority}"
               + (f"  @{task.assignee}" if task.assignee else "")
               + (f"  tenant={task.tenant}" if task.tenant else ""))
        _print(f"title: {task.title}")
        if task.body:
            _print(f"\n{task.body}")
        parents, children = store.parents(task.id), store.children(task.id)
        if parents:
            _print(f"\nparents: {', '.join(p[:13] for p in parents)}")
        if children:
            _print(f"children: {', '.join(ch[:13] for ch in children)}")
        runs = store.runs(task.id)
        if runs:
            _print("\nruns:")
            for r in runs:
                _print(f"  #{r.id} @{r.profile or '?'} {r.outcome or r.status}"
                       + (f" — {truncate(r.summary, 60)}" if r.summary else "")
                       + (f" [error: {truncate(r.error, 60)}]" if r.error else ""))
        comments = store.comments(task.id)
        if comments:
            _print("\ncomments:")
            for cm in comments:
                who = f"{cm.author}: " if cm.author else ""
                _print(f"  [{cm.created_at}] {who}{cm.text}")
        return 0

    if action == "claim":
        tid, worker = _arg("id"), _arg("worker") or "cli"
        if not tid:
            _print("usage: aegis kanban claim <id> [--worker NAME]")
            return 1
        if store.claim(tid, worker):
            _print(f"claimed {tid} as @{worker}")
            return 0
        _print(f"could not claim {tid} (not ready or not found)")
        return 1

    if action == "complete":
        tid = _arg("id")
        if not tid:
            _print("usage: aegis kanban complete <id>")
            return 1
        if store.complete(tid, summary=_arg("summary", "") or ""):
            _print(f"completed {tid}")
            return 0
        _print(f"task '{tid}' not found")
        return 1

    if action == "assign":
        tid, who = _arg("id"), _arg("assignee") or _arg("worker")
        if not tid or not who:
            _print("usage: aegis kanban assign <id> <assignee>")
            return 1
        _print(f"assigned {tid} -> @{who}" if store.assign(tid, who)
               else f"task '{tid}' not found")
        return 0

    if action in ("block", "unblock", "promote", "archive"):
        tid = _arg("id")
        if not tid:
            _print(f"usage: aegis kanban {action} <id>")
            return 1
        fn = {"block": lambda: store.block(tid, _arg("reason", "") or ""),
              "unblock": lambda: store.unblock(tid), "promote": lambda: store.promote(tid),
              "archive": lambda: store.archive(tid)}[action]
        _print(f"{action} {tid}" if fn() else f"could not {action} {tid}")
        return 0

    if action == "link":
        parent, child = _arg("parent") or _arg("id"), _arg("child")
        if not parent or not child:
            _print("usage: aegis kanban link --parent <id> --child <id>")
            return 1
        _print(f"linked {parent} -> {child}" if store.link(parent, child)
               else "could not link (one id not found)")
        return 0

    if action == "runs":
        tid = _arg("id")
        runs = store.runs(tid) if tid else []
        if not runs:
            _print("(no runs)")
            return 0
        for r in runs:
            _print(f"  #{r.id} @{r.profile or '?'} {r.outcome or r.status} "
                   f"{r.started_at} -> {r.ended_at or '...'}"
                   + (f"  {truncate(r.summary, 60)}" if r.summary else ""))
        return 0

    if action == "heartbeat":
        tid = _arg("id")
        _print(f"heartbeat {tid}" if tid and store.heartbeat(tid, _arg("note", "") or "")
               else "could not heartbeat")
        return 0

    if action == "stats":
        st = store.stats()
        _print("by status:")
        for k, v in sorted(st["by_status"].items()):
            _print(f"  {k:<12} {v}")
        if st["by_assignee"]:
            _print("by assignee:")
            for k, v in sorted(st["by_assignee"].items()):
                _print(f"  {k:<16} {v}")
        return 0

    if action == "dispatch":
        from .kanban_auto import dispatch_pass
        summary = dispatch_pass(config, worker=_arg("worker") or "agent",
                                spawn=not _arg("no_spawn", False))
        _print(f"dispatch: promoted={summary['promoted']} reclaimed={summary['reclaimed']} "
               f"completed={len(summary['completed'])} blocked={len(summary['blocked'])}")
        return 0

    if action == "decompose":
        goal = _arg("title") or _arg("goal")
        if not goal:
            _print('usage: aegis kanban decompose "<goal>"')
            return 1
        from .kanban_auto import decompose
        cards = decompose(goal, config, store=store)
        _print(f"created {len(cards)} task(s):")
        for c in cards:
            _print(_fmt_task(c))
        return 0

    if action == "run":
        from .kanban_auto import run_board
        done = run_board(config, worker=_arg("worker") or "auto", on_event=_print)
        _print(f"completed {len(done)} task(s)")
        return 0

    _print("usage: aegis kanban [create|list|show|claim|complete|assign|block|unblock|link|"
           "promote|archive|runs|heartbeat|stats|dispatch|decompose|run]")
    return 1
