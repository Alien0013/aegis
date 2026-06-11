"""Kanban automation: turn a goal into task cards, then autonomously work the board.

Two primitives on top of the kanban store + the agent:
  * decompose(goal)  — the agent breaks a goal into discrete cards (ready column)
  * run_board()      — a worker loop claims the next ready card, runs the agent on it,
                       comments the result, and marks it done — until the board drains
"""

from __future__ import annotations

import json
import re

from .kanban import KanbanStore

_DECOMPOSE_PROMPT = (
    "Break the following goal into 3–8 concrete, independent subtasks that each could be done "
    "in one focused sitting. Return ONLY a JSON array of objects with keys 'title' (short) and "
    "'body' (what to do, one or two sentences). No prose, no markdown fences.\n\nGOAL: {goal}"
)


def _extract_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def decompose(goal: str, config, store: KanbanStore | None = None) -> list:
    """Ask the agent to split ``goal`` into subtasks and create a card for each. Returns cards."""
    from .surface import SurfaceRunner
    store = store or KanbanStore()
    runner = SurfaceRunner(config, include_mcp=True, reuse_agents=False)
    result = runner.run_prompt(
        _DECOMPOSE_PROMPT.format(goal=goal),
        title="kanban decomposition",
        surface="kanban",
        meta={"kanban_action": "decompose"},
    )
    created = []
    for t in _extract_json_array(result.text or ""):
        if isinstance(t, dict) and t.get("title"):
            created.append(store.create(str(t["title"])[:120], str(t.get("body", ""))))
    return created


def run_board(config, worker: str = "auto", max_tasks: int = 20,
              store: KanbanStore | None = None, on_event=None,
              workers: int | None = None) -> list[str]:
    """Claim and complete ready cards until the board drains (or max_tasks).

    With ``workers`` > 1 (or config ``kanban.workers``), that many lane workers run in
    parallel: each claims unassigned cards, plus cards whose assignee was pre-set to
    its lane name (``lane-1`` … ``lane-N``) — so you can pin related cards to one lane
    to keep them serialized while everything else fans out."""
    n = workers if workers is not None else int((config.get("kanban.workers", 1) if config is not None else 1) or 1)
    if n > 1:
        from concurrent.futures import ThreadPoolExecutor
        store = store or KanbanStore()
        # Warm shared stores before lane threads start; their lazy schema migrations
        # are process-safe after initialization, but concurrent first init can race.
        from .runs import RunStore
        from .session import SessionStore
        RunStore()
        SessionStore()
        lanes = [f"lane-{i + 1}" for i in range(n)]
        per = max(1, max_tasks // n)
        with ThreadPoolExecutor(max_workers=n) as ex:
            results = ex.map(lambda lane: run_board(config, worker=lane, max_tasks=per,
                                                    store=store, on_event=on_event, workers=1),
                             lanes)
        return [tid for r in results for tid in r]
    from .surface import SurfaceRunner
    store = store or KanbanStore()
    done: list[str] = []
    runner = SurfaceRunner(config, include_mcp=True, reuse_agents=False)
    for _ in range(max_tasks):
        task = store.claim_next(worker, lane=worker)
        if task is None:
            break
        if on_event:
            on_event(f"▸ {task.title}")
        prompt = f"{task.title}\n\n{task.body}".strip()
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
            store.comment(task.id, (result.text or "")[:1000])
            store.complete(task.id)
            done.append(task.id)
        except Exception as e:  # noqa: BLE001 - one bad task must not halt the board
            store.comment(task.id, f"error: {e}")
    return done
