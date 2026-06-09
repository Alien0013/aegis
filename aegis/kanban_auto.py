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
    from .agent.agent import Agent
    from .session import Session
    store = store or KanbanStore()
    agent = Agent.create(config, session=Session.create())
    resp = agent.run(_DECOMPOSE_PROMPT.format(goal=goal))
    created = []
    for t in _extract_json_array(resp.content or ""):
        if isinstance(t, dict) and t.get("title"):
            created.append(store.create(str(t["title"])[:120], str(t.get("body", ""))))
    return created


def run_board(config, worker: str = "auto", max_tasks: int = 20,
              store: KanbanStore | None = None, on_event=None) -> list[str]:
    """Claim and complete ready cards one at a time until the board drains (or max_tasks)."""
    from .agent.agent import Agent
    from .session import Session
    store = store or KanbanStore()
    done: list[str] = []
    for _ in range(max_tasks):
        task = store.claim_next(worker)
        if task is None:
            break
        if on_event:
            on_event(f"▸ {task.title}")
        agent = Agent.create(config, session=Session.create())
        prompt = f"{task.title}\n\n{task.body}".strip()
        try:
            resp = agent.run(prompt)
            store.comment(task.id, (resp.content or "")[:1000])
            store.complete(task.id)
            done.append(task.id)
        except Exception as e:  # noqa: BLE001 - one bad task must not halt the board
            store.comment(task.id, f"error: {e}")
    return done
