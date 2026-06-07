"""Background task manager: run agent prompts in daemon threads (for /background)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .types import new_id


@dataclass
class BgTask:
    id: str
    prompt: str
    status: str = "running"     # running | done | error
    result: str = ""
    error: str = ""


class BackgroundManager:
    def __init__(self):
        self._tasks: dict[str, BgTask] = {}
        self._lock = threading.Lock()

    def spawn(self, config: Any, prompt: str) -> str:
        from .agent.agent import Agent
        from .session import Session

        task = BgTask(id=new_id("bg"), prompt=prompt)
        with self._lock:
            self._tasks[task.id] = task

        def _work():
            try:
                agent = Agent.create(config, session=Session.create())
                result = agent.run(prompt)
                with self._lock:
                    task.result = result.content or ""
                    task.status = "done"
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    task.error = f"{type(e).__name__}: {e}"
                    task.status = "error"

        threading.Thread(target=_work, daemon=True).start()
        return task.id

    def list(self) -> list[dict]:
        with self._lock:
            return [{"id": t.id, "status": t.status, "prompt": t.prompt[:60],
                     "result_preview": (t.result or t.error)[:80]}
                    for t in self._tasks.values()]

    def get(self, task_id: str) -> BgTask | None:
        with self._lock:
            for t in self._tasks.values():
                if t.id.startswith(task_id):
                    return t
        return None


_MANAGER: BackgroundManager | None = None


def get_manager() -> BackgroundManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = BackgroundManager()
    return _MANAGER
