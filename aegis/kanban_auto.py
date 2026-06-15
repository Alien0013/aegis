"""Kanban automation: decomposition, worker execution, and the dispatcher pass.

Three layers on top of the kanban kernel (:mod:`aegis.kanban`):

* ``decompose(goal)`` — the agent breaks a goal into cards (optionally a dependency graph).
* ``run_board()``      — a worker loop: claim the next ready card, run the agent on it inside
  its resolved workspace, record the attempt as a *run*, and complete (or block) it.
* ``dispatch_pass()``  — one full reconcile: reclaim stale claims → promote gated cards whose
  parents are done → spawn workers for ready cards. Mirrors Hermes' dispatcher.

Every dispatched worker's prompt is prefixed with :data:`WORKER_GUIDANCE` — the lifecycle
contract (orient → work → heartbeat → block/complete) Hermes auto-injects as KANBAN_GUIDANCE.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .kanban import KanbanStore, Task

_DECOMPOSE_PROMPT = (
    "Break the following goal into 3–8 concrete subtasks that each could be done in one "
    "focused sitting. Return ONLY a JSON array of objects with keys 'title' (short), "
    "'body' (what to do, 1–2 sentences), and optional 'deps' (a list of 1-based indices of "
    "earlier subtasks this one depends on). No prose, no markdown fences.\n\nGOAL: {goal}"
)

WORKER_GUIDANCE = (
    "# Kanban task execution protocol\n"
    "You have been dispatched ONE task from the shared board. Your task id is "
    "`{task_id}`. The `kanban` tool is your coordination surface — it writes to the shared "
    "board and survives crashes.\n\n"
    "## Lifecycle\n"
    "1. **Orient.** Call `kanban(action=\"show\", id=\"{task_id}\")` first — it returns the "
    "title/body, parent-task handoffs (summary + metadata), any prior attempts on this task "
    "(if you are a retry, read their outcomes and don't repeat what failed), and the comment "
    "thread.\n"
    "2. **Work in your workspace.** Your working directory is already the task workspace "
    "(`{workspace}`). Keep file changes inside it unless the task says otherwise.\n"
    "3. **Heartbeat on long work.** Call `kanban(action=\"heartbeat\", id=\"{task_id}\", "
    "text=\"<progress>\")` every few minutes during long operations so the dispatcher doesn't "
    "reclaim you as stale. Skip it for short tasks.\n"
    "4. **Block on genuine ambiguity.** If you need a human decision you can't infer, call "
    "`kanban(action=\"block\", id=\"{task_id}\", text=\"<one-line question>\")` and stop — "
    "don't guess.\n"
    "5. **Complete with a structured handoff.** Call `kanban(action=\"complete\", "
    "id=\"{task_id}\", text=\"<1-3 sentence summary>\")` plus `metadata` "
    "(`{{changed_files: [...], tests_run: N, decisions: [...]}}`) so downstream workers can "
    "read what you did. For code changes that still need human review, comment the details "
    "then `block` with `review-required: <summary>` instead of completing.\n"
    "6. **Spawn follow-ups; don't scope-creep.** If new work appears, "
    "`kanban(action=\"create\", title=..., parents=[\"{task_id}\"])` for the right specialist "
    "rather than doing it yourself.\n"
)


# -- workspace resolution ---------------------------------------------------
def resolve_workspace(task: Task, base_cwd: Path | None = None) -> Path:
    """Resolve a task's workspace to a real directory.

    ``scratch`` → a fresh temp dir; ``dir:<path>`` → that persistent directory;
    ``worktree`` → a git worktree off ``base_cwd`` (falls back to a plain dir if git is
    unavailable). Returns the resolved absolute path.
    """
    base = Path(base_cwd or Path.cwd())
    kind = task.workspace_kind or "scratch"
    if kind == "dir" and task.workspace_path:
        p = Path(task.workspace_path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    if kind == "worktree":
        branch = task.branch_name or f"wt/{task.id[:12]}"
        path = Path(task.workspace_path).expanduser() if task.workspace_path \
            else base / ".aegis" / "worktrees" / task.id[:12]
        if (base / ".git").exists() and shutil.which("git"):
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(["git", "worktree", "add", "-f", "-B", branch, str(path)],
                               cwd=str(base), capture_output=True, timeout=60, check=False)
                if path.exists():
                    return path
            except (OSError, subprocess.SubprocessError):
                pass
        path.mkdir(parents=True, exist_ok=True)
        return path
    # scratch (default)
    return Path(tempfile.mkdtemp(prefix=f"aegis-kanban-{task.id[:8]}-"))


def _worker_prompt(task: Task, workspace: Path) -> str:
    guidance = WORKER_GUIDANCE.format(task_id=task.id, workspace=workspace)
    body = task.title if not task.body else f"{task.title}\n\n{task.body}"
    tenant = f"\n\nTENANT: {task.tenant} — namespace any persistent memory you write."\
        if task.tenant else ""
    return f"<system-reminder>{guidance}</system-reminder>\n\nTASK: {body}{tenant}"


# -- decomposition ----------------------------------------------------------
def _extract_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def decompose(goal: str, config, store: KanbanStore | None = None) -> list[Task]:
    """Split ``goal`` into cards, honoring any ``deps`` the model returns by linking
    children to parents (so dependent cards start gated in ``todo``)."""
    from .surface import SurfaceRunner

    store = store or KanbanStore()
    runner = SurfaceRunner(config, include_mcp=True, reuse_agents=False)
    result = runner.run_prompt(
        _DECOMPOSE_PROMPT.format(goal=goal), title="kanban decomposition",
        surface="kanban", meta={"kanban_action": "decompose"},
    )
    specs = _extract_json_array(result.text or "")
    created: list[Task] = []
    index_to_id: dict[int, str] = {}
    for i, t in enumerate(specs, 1):
        if not (isinstance(t, dict) and t.get("title")):
            continue
        deps = [index_to_id[d] for d in t.get("deps", []) or []
                if isinstance(d, int) and d in index_to_id]
        card = store.create(str(t["title"])[:120], str(t.get("body", "")), parents=deps)
        index_to_id[i] = card.id
        created.append(card)
    return created


# -- worker execution -------------------------------------------------------
def _run_one(store: KanbanStore, task: Task, config, worker: str,
             base_cwd: Path | None = None, on_event=None) -> str:
    """Execute one claimed task: record a run, run the agent in its workspace, then
    complete or block. Returns the outcome ('completed' | 'blocked')."""
    from .surface import SurfaceRunner

    run_id = store.start_run(task.id, profile=worker)
    workspace = resolve_workspace(task, base_cwd)
    if workspace and task.workspace_kind != "dir":
        store.set_workspace(task.id, str(workspace))   # record the resolved path
    if on_event:
        on_event(f"▸ {task.title}  ({task.workspace_kind} @ {workspace})")
    runner = SurfaceRunner(config, cwd=workspace, include_mcp=True, reuse_agents=False)
    try:
        result = runner.run_prompt(
            _worker_prompt(task, workspace), title=task.title, surface="kanban",
            meta={"kanban_task_id": task.id, "kanban_worker": worker, "kanban_run_id": run_id},
        )
        store.record_breadcrumbs(task.id, run_id=result.run_id, session_id=result.session.id,
                                 trace_id=result.trace_id)
        # If the worker already moved the card off in_progress (blocked/done via the tool),
        # respect that; otherwise complete it with the agent's reply as the summary.
        current = store.show(task.id)
        if current and current.status == "in_progress":
            store.end_run(run_id, "completed", summary=(result.text or "")[:1000])
            store.complete(task.id, summary=(result.text or "")[:1000])
            return "completed"
        store.end_run(run_id, current.status if current else "completed",
                      summary=(result.text or "")[:1000])
        return current.status if current else "completed"
    except Exception as e:  # noqa: BLE001 — one bad task must not halt the board
        store.end_run(run_id, "crashed", error=str(e))
        store.block(task.id, f"agent error: {e}")
        return "blocked"


def run_board(config, worker: str = "auto", max_tasks: int = 20,
              store: KanbanStore | None = None, on_event=None,
              workers: int | None = None) -> list[str]:
    """Claim and run ready cards until the board drains (or ``max_tasks``).

    With ``workers`` > 1 (or config ``kanban.workers``), that many lane workers run in
    parallel: each claims unassigned cards plus cards pre-assigned to its lane name
    (``lane-1`` … ``lane-N``)."""
    n = workers if workers is not None else int(
        (config.get("kanban.workers", 1) if config is not None else 1) or 1)
    if n > 1:
        from concurrent.futures import ThreadPoolExecutor

        from .runs import RunStore
        from .session import SessionStore
        store = store or KanbanStore()
        RunStore()
        SessionStore()
        lanes = [f"lane-{i + 1}" for i in range(n)]
        per = max(1, max_tasks // n)
        with ThreadPoolExecutor(max_workers=n) as ex:
            results = ex.map(lambda lane: run_board(config, worker=lane, max_tasks=per,
                                                    store=store, on_event=on_event, workers=1),
                             lanes)
        return [tid for r in results for tid in r]
    store = store or KanbanStore()
    base_cwd = Path.cwd()
    done: list[str] = []
    for _ in range(max_tasks):
        store.promote_ready()
        task = store.claim_next(worker, lane=worker)
        if task is None:
            break
        outcome = _run_one(store, task, config, worker, base_cwd=base_cwd, on_event=on_event)
        if outcome == "completed":
            done.append(task.id)
    return done


# -- dispatcher pass --------------------------------------------------------
def dispatch_pass(config, worker: str = "agent", *, spawn: bool = True,
                  stale_timeout: float | None = None, max_tasks: int = 20,
                  store: KanbanStore | None = None) -> dict:
    """One dispatcher reconcile, mirroring Hermes:

    1. **Reclaim stale** — tasks whose worker went silent past the timeout go back to ready.
    2. **Promote** — gated ``todo`` cards whose parents are all done become ``ready``.
    3. **Spawn** — run ready cards through workers (unless ``spawn=False``).

    Returns a summary dict (reclaimed/promoted/completed/blocked)."""
    store = store or KanbanStore()
    timeout = stale_timeout if stale_timeout is not None else float(
        (config.get("kanban.dispatch_stale_timeout_seconds", 14400) if config else 14400) or 14400)
    reclaimed = store.reclaim_stale(timeout)
    promoted = store.promote_ready()
    completed: list[str] = []
    blocked: list[str] = []
    if spawn:
        base_cwd = Path.cwd()
        for _ in range(max_tasks):
            task = store.claim_next(worker, lane=worker)
            if task is None:
                break
            outcome = _run_one(store, task, config, worker, base_cwd=base_cwd)
            (completed if outcome == "completed" else blocked).append(task.id)
    return {"reclaimed": len(reclaimed), "promoted": len(promoted),
            "completed": completed, "blocked": blocked}
