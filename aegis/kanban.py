"""Multi-agent kanban task board, SQLite-backed.

A small work queue that several agents (or a human + agents) can share. Tasks
move ``ready -> in_progress -> done`` with a ``blocked`` side state. Claiming is
atomic (a single conditional UPDATE) so two concurrent workers never grab the
same task. ``dispatch`` pops the next ready task, runs it through an
:class:`~aegis.agent.agent.Agent`, and marks it done.

Storage lives at ``cfg.sub("kanban.db")`` with two tables: ``tasks`` and
``comments``. Everything is plain SQLite — no external services.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from . import config as cfg
from .types import new_id
from .util import now_iso, truncate

# status flow: ready -> in_progress -> done, plus blocked
STATUSES = ("ready", "in_progress", "done", "blocked")


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

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Task":
        return Task(
            id=row["id"],
            title=row["title"],
            body=row["body"] or "",
            status=row["status"],
            assignee=row["assignee"] or "",
            priority=row["priority"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            run_id=row["run_id"] if "run_id" in row.keys() else "",
            session_id=row["session_id"] if "session_id" in row.keys() else "",
            trace_id=row["trace_id"] if "trace_id" in row.keys() else "",
        )


@dataclass
class Comment:
    id: str
    task_id: str
    text: str
    created_at: str

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Comment":
        return Comment(
            id=row["id"], task_id=row["task_id"], text=row["text"], created_at=row["created_at"]
        )


class KanbanStore:
    """SQLite-backed task board. Safe for concurrent claimers."""

    def __init__(self):
        self.db = cfg.sub("kanban.db")
        self._init()

    def _conn(self) -> sqlite3.Connection:
        cfg.get_home().mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        # WAL lets a reader and the claimer coexist without blocking.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                       id TEXT PRIMARY KEY,
                       title TEXT NOT NULL,
                       body TEXT,
                       status TEXT NOT NULL DEFAULT 'ready',
                       assignee TEXT,
                       priority INTEGER NOT NULL DEFAULT 0,
                       created_at TEXT,
                       updated_at TEXT,
                       run_id TEXT,
                       session_id TEXT,
                       trace_id TEXT
                   )"""
            )
            cols = {r[1] for r in c.execute("PRAGMA table_info(tasks)").fetchall()}
            for name in ("run_id", "session_id", "trace_id"):
                if name not in cols:
                    c.execute(f"ALTER TABLE tasks ADD COLUMN {name} TEXT")
            c.execute(
                """CREATE TABLE IF NOT EXISTS comments (
                       id TEXT PRIMARY KEY,
                       task_id TEXT NOT NULL,
                       text TEXT NOT NULL,
                       created_at TEXT
                   )"""
            )

    # -- mutations ----------------------------------------------------------
    def create(self, title: str, body: str = "", priority: int = 0) -> Task:
        now = now_iso()
        task = Task(
            id=new_id("task"),
            title=title,
            body=body,
            status="ready",
            assignee="",
            priority=int(priority),
            created_at=now,
            updated_at=now,
            run_id="",
            session_id="",
            trace_id="",
        )
        with self._conn() as c:
            c.execute(
                """INSERT INTO tasks
                   (id,title,body,status,assignee,priority,created_at,updated_at,
                    run_id,session_id,trace_id)
                   VALUES
                   (:id,:title,:body,:status,:assignee,:priority,:created_at,:updated_at,
                    :run_id,:session_id,:trace_id)""",
                task.__dict__,
            )
        return task

    def claim(self, task_id: str, worker: str) -> bool:
        """Atomically claim a *ready* task for ``worker``.

        Returns ``True`` only if this call won the race: a single conditional
        UPDATE flips ``ready -> in_progress`` and stamps the assignee. Concurrent
        callers see ``rowcount == 0`` and get ``False``.
        """
        with self._conn() as c:
            cur = c.execute(
                """UPDATE tasks SET status='in_progress', assignee=?, updated_at=?
                   WHERE id=? AND status='ready'""",
                (worker, now_iso(), task_id),
            )
            return cur.rowcount > 0

    def claim_next(self, worker: str, lane: str = "") -> Task | None:
        """Atomically claim the highest-priority, oldest ready task, if any.
        With ``lane``, cards pre-assigned to another lane are skipped — a card whose
        assignee is set while still ready belongs to that lane's worker only."""
        with self._conn() as c:
            # IMMEDIATE takes a write lock up front so the select+update is atomic
            # against other claimers sharing this database.
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                """SELECT * FROM tasks WHERE status='ready'
                     AND (assignee='' OR assignee IS NULL OR assignee=?)
                   ORDER BY priority DESC, created_at ASC LIMIT 1""",
                (lane or worker,)
            ).fetchone()
            if row is None:
                c.execute("COMMIT")
                return None
            c.execute(
                "UPDATE tasks SET status='in_progress', assignee=?, updated_at=? WHERE id=?",
                (worker, now_iso(), row["id"]),
            )
            c.execute("COMMIT")
            task = Task.from_row(row)
            task.status = "in_progress"
            task.assignee = worker
            return task

    def _set_status(self, task_id: str, status: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, now_iso(), task_id),
            )
            return cur.rowcount > 0

    def complete(self, task_id: str) -> bool:
        return self._set_status(task_id, "done")

    def assign(self, task_id: str, who: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE tasks SET assignee=?, updated_at=? WHERE id=?",
                (who, now_iso(), task_id),
            )
            return cur.rowcount > 0

    def block(self, task_id: str, reason: str = "") -> bool:
        ok = self._set_status(task_id, "blocked")
        if ok and reason:
            self.comment(task_id, f"BLOCKED: {reason}")
        return ok

    def record_breadcrumbs(
        self,
        task_id: str,
        *,
        run_id: str = "",
        session_id: str = "",
        trace_id: str = "",
    ) -> bool:
        with self._conn() as c:
            cur = c.execute(
                """UPDATE tasks SET run_id=?, session_id=?, trace_id=?, updated_at=?
                   WHERE id=?""",
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
            return cur.rowcount > 0

    def comment(self, task_id: str, text: str) -> Comment:
        cm = Comment(id=new_id("cmt"), task_id=task_id, text=text, created_at=now_iso())
        with self._conn() as c:
            c.execute(
                "INSERT INTO comments (id,task_id,text,created_at) VALUES (?,?,?,?)",
                (cm.id, cm.task_id, cm.text, cm.created_at),
            )
        return cm

    # -- queries ------------------------------------------------------------
    def _resolve(self, c: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
        # exact id first, then unique prefix (so short ids work on the CLI).
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row:
            return row
        rows = c.execute(
            "SELECT * FROM tasks WHERE id LIKE ? LIMIT 2", (task_id + "%",)
        ).fetchall()
        return rows[0] if len(rows) == 1 else None

    def show(self, task_id: str) -> Task | None:
        with self._conn() as c:
            row = self._resolve(c, task_id)
            return Task.from_row(row) if row else None

    def comments(self, task_id: str) -> list[Comment]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM comments WHERE task_id=? ORDER BY created_at ASC", (task_id,)
            ).fetchall()
            return [Comment.from_row(r) for r in rows]

    def list(self, status: str | None = None, assignee: str | None = None) -> list[Task]:
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if assignee:
            clauses.append("assignee=?")
            params.append(assignee)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM tasks{where} ORDER BY priority DESC, created_at ASC", params
            ).fetchall()
            return [Task.from_row(r) for r in rows]


# -- dispatch ---------------------------------------------------------------
def dispatch(config, worker: str = "agent") -> Task | None:
    """Claim the next ready task, run it through an Agent, mark it done.

    The task title + body become the agent prompt. The agent's reply is stored
    as a comment for audit. On failure the task is blocked (not lost) with the
    error recorded. Returns the task that was processed, or ``None`` if the board
    had no ready work.
    """
    from .surface import SurfaceRunner

    store = KanbanStore()
    task = store.claim_next(worker)
    if task is None:
        return None

    prompt = task.title if not task.body else f"{task.title}\n\n{task.body}"
    runner = SurfaceRunner(config, include_mcp=True, reuse_agents=False)
    try:
        result = runner.run_prompt(
            prompt,
            title=task.title,
            surface="kanban",
            meta={"kanban_task_id": task.id, "kanban_worker": worker},
        )
        store.record_breadcrumbs(
            task.id,
            run_id=result.run_id,
            session_id=result.session.id,
            trace_id=result.trace_id,
        )
        store.comment(task.id, result.text)
        store.complete(task.id)
    except Exception as e:  # noqa: BLE001
        store.block(task.id, f"agent error: {e}")
    return store.show(task.id)


# -- CLI --------------------------------------------------------------------
def _print(s: str = "") -> None:
    print(s)


def _fmt_task(t: Task) -> str:
    who = f" @{t.assignee}" if t.assignee else ""
    return f"  {t.id[:13]}  [{t.status:<11}] P{t.priority}{who}  {truncate(t.title, 60)}"


def cmd_kanban(args, config) -> int:
    """CLI: aegis kanban <create|list|show|claim|complete|assign|dispatch> ...

    Reads optional attributes off ``args`` defensively so the parent wiring can
    expose whichever flags it likes (e.g. ``--status``, ``--assignee``,
    ``--priority``, ``--body``, ``--worker``).
    """
    action = getattr(args, "action", None)
    store = KanbanStore()

    def _arg(name: str, default: Any = None) -> Any:
        return getattr(args, name, default)

    if action == "create":
        title = _arg("title")
        if not title:
            _print("usage: aegis kanban create <title> [--body ...] [--priority N]")
            return 1
        task = store.create(title, body=_arg("body", "") or "", priority=int(_arg("priority", 0) or 0))
        _print(f"created {task.id}")
        return 0

    if action == "list":
        tasks = store.list(status=_arg("status"), assignee=_arg("assignee"))
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
               + (f"  @{task.assignee}" if task.assignee else ""))
        _print(f"title: {task.title}")
        if task.body:
            _print(f"\n{task.body}")
        _print(f"\ncreated {task.created_at}  updated {task.updated_at}")
        comments = store.comments(task.id)
        if comments:
            _print("\ncomments:")
            for cm in comments:
                _print(f"  [{cm.created_at}] {cm.text}")
        return 0

    if action == "claim":
        tid = _arg("id")
        worker = _arg("worker") or "cli"
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
        if store.complete(tid):
            _print(f"completed {tid}")
            return 0
        _print(f"task '{tid}' not found")
        return 1

    if action == "assign":
        tid = _arg("id")
        who = _arg("assignee") or _arg("worker")
        if not tid or not who:
            _print("usage: aegis kanban assign <id> <assignee>")
            return 1
        if store.assign(tid, who):
            _print(f"assigned {tid} -> @{who}")
            return 0
        _print(f"task '{tid}' not found")
        return 1

    if action == "dispatch":
        worker = _arg("worker") or "agent"
        task = dispatch(config, worker=worker)
        if task is None:
            _print("(no ready tasks)")
            return 0
        _print(f"dispatched {task.id} -> {task.status}")
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
        worker = _arg("worker") or "auto"
        done = run_board(config, worker=worker, on_event=_print)
        _print(f"completed {len(done)} task(s)")
        return 0

    _print("usage: aegis kanban [create|list|show|claim|complete|assign|dispatch|decompose|run]")
    return 1
