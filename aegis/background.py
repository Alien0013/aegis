"""Background task manager: run agent prompts in daemon threads (for /background)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from .types import new_id


@dataclass
class BgTask:
    id: str
    prompt: str
    status: str = "running"     # running | done | error
    result: str = ""
    error: str = ""
    run_id: str = ""


class BackgroundManager:
    def __init__(self):
        self._tasks: dict[str, BgTask] = {}
        self._lock = threading.Lock()

    def spawn(self, config: Any, prompt: str, *, cwd=None, on_done=None,
              parent_session=None) -> str:
        """Run ``prompt`` in a background agent. ``on_done(task)`` (if given) fires
        when it finishes — used to announce the result back into a chat."""
        from .surface import SurfaceRunner, runtime_controls_meta, session_runtime_controls

        task = BgTask(id=new_id("bg"), prompt=prompt)
        with self._lock:
            self._tasks[task.id] = task

        meta = {
            "background_task_id": task.id,
            **runtime_controls_meta(session_runtime_controls(parent_session)),
        }

        def _work():
            try:
                runner = SurfaceRunner(config, cwd=cwd, include_mcp=True)
                result = runner.run_prompt(
                    prompt,
                    session_id=f"background:{task.id}",
                    title=f"background {task.id}",
                    surface="background",
                    meta=meta,
                )
                with self._lock:
                    task.result = result.text or ""
                    task.status = "done"
                    task.run_id = result.run_id
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    task.error = f"{type(e).__name__}: {e}"
                    task.status = "error"
            if on_done is not None:
                try:
                    on_done(task)
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_work, daemon=True).start()
        return task.id

    def list(self) -> list[dict]:
        with self._lock:
            return [{"id": t.id, "status": t.status, "prompt": t.prompt[:60],
                     "result_preview": (t.result or t.error)[:80], "run_id": t.run_id}
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
