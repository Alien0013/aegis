"""Background task manager: run agent prompts in daemon threads (for /background)."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .types import new_id


@dataclass
class BgTask:
    id: str
    prompt: str
    status: str = "running"     # running | cancelling | cancelled | done | error
    result: str = ""
    error: str = ""
    run_id: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    parent_session_id: str = ""
    agent_type: str = "general"
    role: str = "leaf"
    model: str = ""
    platform: str = ""
    chat_id: str = ""
    user_id: str = ""
    user_name: str = ""
    thread_id: str = ""
    message_id: str = ""
    cancel_requested: bool = False
    retry_of: str = ""


class BackgroundCapacityError(RuntimeError):
    """Raised when background subagent admission would exceed the configured cap."""


def _default_config_value(dotted: str) -> Any:
    try:
        from .config import DEFAULT_CONFIG
    except Exception:  # noqa: BLE001
        return None
    node: Any = DEFAULT_CONFIG
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _positive_config_int(config: Any, dotted: str) -> int:
    try:
        value = int(config.get(dotted, 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


class BackgroundManager:
    def __init__(self):
        self._tasks: dict[str, BgTask] = {}
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._executor_size = 0
        self._completion_events: list[dict[str, Any]] = []
        self._active_agents: dict[str, Any] = {}

    def _max_workers(self, config: Any) -> int:
        async_value = _positive_config_int(config, "delegation.max_async_children")
        default_async = _default_config_value("delegation.max_async_children")
        alias_keys = (
            "delegation.max_background_children",
            "delegation.max_concurrent_children",
            "tools.subagent_concurrency",
            "agent.subagent_concurrency",
        )
        alias_values = []
        for key in alias_keys:
            value = _positive_config_int(config, key)
            if value and value != _default_config_value(key):
                alias_values.append(value)
        if async_value and (async_value != default_async or not any(alias_values)):
            return max(1, min(async_value, 32))
        for value in alias_values:
            if value > 0:
                return max(1, min(value, 32))
        if async_value:
            return max(1, min(async_value, 32))
        return 4

    def _retained_completed(self, config: Any) -> int:
        for key in ("delegation.retain_completed_background_tasks",
                    "delegation.max_retained_background_tasks"):
            try:
                value = int(config.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return max(1, min(value, 1000))
        return 50

    def _running_count_locked(self) -> int:
        return sum(1 for task in self._tasks.values() if task.status in {"running", "cancelling"})

    def capacity(self, config: Any) -> dict[str, int]:
        with self._lock:
            maximum = self._max_workers(config)
            running = self._running_count_locked()
        return {
            "max": maximum,
            "running": running,
            "available": max(0, maximum - running),
        }

    def require_capacity(self, config: Any, requested: int) -> None:
        requested = max(1, int(requested or 1))
        snapshot = self.capacity(config)
        if requested > snapshot["available"]:
            raise BackgroundCapacityError(
                "async background delegation capacity reached "
                f"({snapshot['running']}/{snapshot['max']} running, "
                f"{requested} requested, {snapshot['available']} available). "
                "Wait for tasks to finish or raise delegation.max_async_children."
            )

    def _prune_completed_locked(self, config: Any) -> None:
        keep = self._retained_completed(config)
        completed = [
            (task_id, task)
            for task_id, task in self._tasks.items()
            if task.status != "running"
        ]
        if len(completed) <= keep:
            return
        completed.sort(key=lambda item: item[1].finished_at or item[1].started_at or item[1].created_at)
        for task_id, _task in completed[:len(completed) - keep]:
            self._tasks.pop(task_id, None)

    def _submit(self, config: Any, fn) -> None:
        size = self._max_workers(config)
        with self._lock:
            if self._executor is None or self._executor_size != size:
                old = self._executor
                self._executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="aegis-bg")
                self._executor_size = size
                if old is not None:
                    old.shutdown(wait=False, cancel_futures=False)
            executor = self._executor
        executor.submit(fn)

    def _new_task(
        self,
        config: Any,
        prompt: str,
        *,
        parent_session=None,
        session_meta: dict | None = None,
        delivery: dict[str, Any] | None = None,
    ) -> BgTask:
        parent_session_id = str(getattr(parent_session, "id", "") or (session_meta or {}).get("parent_session_id") or "")
        return BgTask(
            id=new_id("bg"),
            prompt=prompt,
            parent_session_id=parent_session_id,
            agent_type=str((session_meta or {}).get("agent_type") or "general"),
            role=str((session_meta or {}).get("role") or "leaf"),
            model=str((session_meta or {}).get("model") or config.get("delegation.model", "")
                      or config.get("model.default", "") or ""),
            platform=str((delivery or {}).get("platform") or ""),
            chat_id=str((delivery or {}).get("chat_id") or ""),
            user_id=str((delivery or {}).get("user_id") or ""),
            user_name=str((delivery or {}).get("user_name") or ""),
            thread_id=str((delivery or {}).get("thread_id") or ""),
            message_id=str((delivery or {}).get("message_id") or ""),
        )

    def _record_completion(self, task: BgTask, config: Any) -> None:
        event = {
            "type": "subagent_done",
            "id": task.id,
            "status": task.status,
            "background": True,
            "agent_type": task.agent_type,
            "run_id": task.run_id,
            "parent_session_id": task.parent_session_id,
            "prompt": task.prompt[:200],
            "result": task.result[:8000],
            "error": task.error[:1000],
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
        }
        with self._lock:
            self._completion_events.append(event)
            self._completion_events = self._completion_events[-200:]
            self._prune_completed_locked(config)
        self._queue_async_delegation_event(task)

    def _queue_async_delegation_event(self, task: BgTask) -> None:
        try:
            from .tools.process_registry import process_registry
        except Exception:  # noqa: BLE001
            return
        completed_at = task.finished_at or time.time()
        event = {
            "type": "async_delegation",
            "session_id": task.id,
            "session_key": task.parent_session_id,
            "delegation_id": task.id,
            "goal": task.prompt,
            "context": "",
            "toolsets": None,
            "role": task.role,
            "model": task.model,
            "agent_type": task.agent_type,
            "status": "completed" if task.status == "done" else task.status,
            "summary": task.result,
            "error": task.error,
            "run_id": task.run_id,
            "api_calls": 0,
            "duration_seconds": round(completed_at - (task.started_at or task.created_at), 2),
            "dispatched_at": task.created_at,
            "completed_at": completed_at,
            "platform": task.platform,
            "chat_id": task.chat_id,
            "user_id": task.user_id,
            "user_name": task.user_name,
            "thread_id": task.thread_id,
            "message_id": task.message_id,
        }
        try:
            process_registry.completion_queue.put(event)
        except Exception:  # noqa: BLE001
            pass

    def _start_registered(
        self,
        config: Any,
        task: BgTask,
        *,
        cwd=None,
        on_done=None,
        parent_session=None,
        registry=None,
        include_mcp: bool = True,
        session_meta: dict | None = None,
        approver=None,
    ) -> None:
        from .surface import SurfaceRunner, runtime_controls_meta, session_runtime_controls

        session_id = f"background:{task.id}"
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
            "creator_kind": "background",
            **runtime_controls_meta(session_runtime_controls(parent_session)),
            **(session_meta or {}),
        }
        parent_session_id = getattr(parent_session, "id", "") if parent_session is not None else ""
        if parent_session_id:
            meta.setdefault("parent_session_id", parent_session_id)

        try:
            timeout = max(0.0, float(config.get("delegation.child_timeout_seconds", 0) or 0))
        except (TypeError, ValueError):
            timeout = 0.0

        def _work():
            runner = None
            watchdog = None
            with self._lock:
                task.started_at = time.time()
            try:
                runner = SurfaceRunner(config, cwd=cwd, include_mcp=include_mcp)
                session = runner.load_or_create_session(
                    session_id=session_id,
                    title=f"background {task.id}",
                    surface="background",
                    meta=meta,
                )
                if parent_session_id and not session.parent_id:
                    session.parent_id = parent_session_id
                    runner.store.save(session)
                agent = runner.make_agent(
                    session=session,
                    cwd=cwd,
                    include_mcp=include_mcp,
                    registry=registry,
                    approver=approver,
                )
                with self._lock:
                    self._active_agents[task.id] = agent
                    cancel_requested = task.cancel_requested
                if cancel_requested:
                    try:
                        agent.cancel()
                    except Exception:  # noqa: BLE001
                        pass
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
                    task.prompt,
                    session=session,
                    agent=agent,
                    surface="background",
                    meta=meta,
                )
                with self._lock:
                    task.result = result.text or ""
                    if task.cancel_requested:
                        task.status = "cancelled"
                        task.error = task.error or "cancel requested"
                    else:
                        task.status = "done"
                    task.run_id = result.run_id
                    task.finished_at = time.time()
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    task.error = f"{type(e).__name__}: {e}"
                    task.status = "cancelled" if task.cancel_requested else "error"
                    task.finished_at = time.time()
            finally:
                with self._lock:
                    self._active_agents.pop(task.id, None)
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
                self._record_completion(task, config)
            if on_done is not None:
                try:
                    on_done(task)
                except Exception:  # noqa: BLE001
                    pass

        self._submit(config, _work)

    def spawn_many(self, config: Any, prompts: list[str], *, cwd=None, on_done=None,
                   parent_session=None, registry=None, include_mcp: bool = True,
                   session_meta: dict | None = None, approver=None,
                   delivery: dict[str, Any] | None = None) -> list[str]:
        """Run several prompts in the background after atomically reserving capacity."""
        clean_prompts = [str(prompt) for prompt in prompts]
        if not clean_prompts:
            return []
        tasks = [
            self._new_task(
                config,
                prompt,
                parent_session=parent_session,
                session_meta=session_meta,
                delivery=delivery,
            )
            for prompt in clean_prompts
        ]
        requested = len(tasks)
        with self._lock:
            max_workers = self._max_workers(config)
            running = self._running_count_locked()
            available = max(0, max_workers - running)
            if requested > available:
                raise BackgroundCapacityError(
                    "async background delegation capacity reached "
                    f"({running}/{max_workers} running, {requested} requested, "
                    f"{available} available). Wait for tasks to finish or raise "
                    "delegation.max_async_children."
                )
            for task in tasks:
                self._tasks[task.id] = task
        for task in tasks:
            self._start_registered(
                config,
                task,
                cwd=cwd,
                on_done=on_done,
                parent_session=parent_session,
                registry=registry,
                include_mcp=include_mcp,
                session_meta=session_meta,
                approver=approver,
            )
        return [task.id for task in tasks]

    def spawn(self, config: Any, prompt: str, *, cwd=None, on_done=None,
              parent_session=None, registry=None, include_mcp: bool = True,
              session_meta: dict | None = None, approver=None,
              delivery: dict[str, Any] | None = None) -> str:
        """Run ``prompt`` in a background agent. ``on_done(task)`` (if given) fires
        when it finishes — used to announce the result back into a chat."""
        return self.spawn_many(
            config,
            [prompt],
            cwd=cwd,
            on_done=on_done,
            parent_session=parent_session,
            registry=registry,
            include_mcp=include_mcp,
            session_meta=session_meta,
            approver=approver,
            delivery=delivery,
        )[0]

    def list(self) -> list[dict]:
        with self._lock:
            return [{"id": t.id, "status": t.status, "prompt": t.prompt[:60],
                     "result_preview": (t.result or t.error)[:80], "run_id": t.run_id,
                     "agent_type": t.agent_type, "parent_session_id": t.parent_session_id,
                     "created_at": t.created_at, "started_at": t.started_at,
                     "finished_at": t.finished_at, "cancel_requested": t.cancel_requested,
                     "retry_of": t.retry_of, "error": t.error[:500],
                     "platform": t.platform, "chat_id": t.chat_id,
                     "thread_id": t.thread_id, "message_id": t.message_id}
                    for t in self._tasks.values()]

    def get(self, task_id: str) -> BgTask | None:
        with self._lock:
            for t in self._tasks.values():
                if t.id.startswith(task_id):
                    return t
        return None

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            return {"ok": False, "error": "background task not found", "id": task_id}
        agent = None
        with self._lock:
            task.cancel_requested = True
            if task.status == "running":
                task.status = "cancelling"
            elif task.status not in {"cancelling", "done", "error", "cancelled"}:
                task.status = "cancelled"
                task.finished_at = task.finished_at or time.time()
            agent = self._active_agents.get(task.id)
        if agent is not None:
            try:
                cancel = getattr(agent, "cancel", None)
                if callable(cancel):
                    cancel()
                cancel_event = getattr(agent, "cancel_event", None)
                if cancel_event is not None:
                    cancel_event.set()
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"cancel failed: {type(exc).__name__}: {exc}", "id": task.id}
        return {"ok": True, "id": task.id, "status": task.status, "cancel_requested": True}

    def retry(self, config: Any, task_id: str, *, cwd=None, on_done=None,
              registry=None, include_mcp: bool = True, approver=None) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            return {"ok": False, "error": "background task not found", "id": task_id}
        if task.status in {"running", "cancelling"}:
            return {"ok": False, "error": "background task is still running", "id": task.id}
        new_id = self.spawn(
            config,
            task.prompt,
            cwd=cwd,
            on_done=on_done,
            registry=registry,
            include_mcp=include_mcp,
            session_meta={
                "agent_type": task.agent_type,
                "role": task.role,
                "model": task.model,
                "parent_session_id": task.parent_session_id,
            },
            approver=approver,
            delivery={
                "platform": task.platform,
                "chat_id": task.chat_id,
                "user_id": task.user_id,
                "user_name": task.user_name,
                "thread_id": task.thread_id,
                "message_id": task.message_id,
            },
        )
        retried = self.get(new_id)
        if retried is not None:
            retried.retry_of = task.id
        return {"ok": True, "id": new_id, "retry_of": task.id}

    def completions(
        self,
        *,
        consume: bool = False,
        parent_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        parent_session_id = str(parent_session_id or "")
        with self._lock:
            if parent_session_id:
                events = [
                    event for event in self._completion_events
                    if str(event.get("parent_session_id") or "") == parent_session_id
                ]
            else:
                events = list(self._completion_events)
            if consume:
                if parent_session_id:
                    self._completion_events = [
                        event for event in self._completion_events
                        if str(event.get("parent_session_id") or "") != parent_session_id
                    ]
                else:
                    self._completion_events.clear()
            return events


_MANAGER: BackgroundManager | None = None


def get_manager() -> BackgroundManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = BackgroundManager()
    return _MANAGER
