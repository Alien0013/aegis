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
              parent_session=None, registry=None, include_mcp: bool = True,
              session_meta: dict | None = None, approver=None) -> str:
        """Run ``prompt`` in a background agent. ``on_done(task)`` (if given) fires
        when it finishes — used to announce the result back into a chat."""
        from .surface import SurfaceRunner, runtime_controls_meta, session_runtime_controls

        task = BgTask(id=new_id("bg"), prompt=prompt)
        session_id = f"background:{task.id}"
        with self._lock:
            self._tasks[task.id] = task
        try:
            backend = str(config.get("tools.subagent_terminal_backend", "") or "").strip().lower()
            if backend and backend not in {"inherit", "parent"}:
                from .tools.backends import register_task_env_overrides

                for env_task_id in (task.id, session_id):
                    register_task_env_overrides(env_task_id, {"terminal_backend": backend})
        except Exception:  # noqa: BLE001
            pass

        meta = {
            "background_task_id": task.id,
            **runtime_controls_meta(session_runtime_controls(parent_session)),
            **(session_meta or {}),
        }

        try:
            timeout = max(0.0, float(config.get("delegation.child_timeout_seconds", 0) or 0))
        except (TypeError, ValueError):
            timeout = 0.0

        def _work():
            runner = None
            watchdog = None
            try:
                runner = SurfaceRunner(config, cwd=cwd, include_mcp=include_mcp)
                session = runner.load_or_create_session(
                    session_id=session_id,
                    title=f"background {task.id}",
                    surface="background",
                    meta=meta,
                )
                agent = runner.make_agent(
                    session=session,
                    cwd=cwd,
                    include_mcp=include_mcp,
                    registry=registry,
                    approver=approver,
                )
                if timeout > 0:                  # wall-clock budget: cancel at the next safe point
                    def _expire(a=agent, t=task):
                        t.error = t.error or f"child timed out after {timeout:g}s"
                        try:
                            a.cancel()
                        except Exception:  # noqa: BLE001
                            pass
                    watchdog = threading.Timer(timeout, _expire)
                    watchdog.daemon = True
                    watchdog.start()
                result = runner.run_prompt(
                    prompt,
                    session=session,
                    agent=agent,
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
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                close = getattr(runner, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    from .tools.backends import clear_task_env_overrides

                    for env_task_id in (task.id, session_id):
                        clear_task_env_overrides(env_task_id)
                except Exception:  # noqa: BLE001
                    pass
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
