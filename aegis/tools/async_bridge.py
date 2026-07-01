"""Sync-to-async bridge for tool execution.

Tool execution is synchronous from the agent loop's point of view, but plugin,
MCP, SDK, or future provider-backed tools may return awaitables. This module is
the single core-harness bridge for resolving those awaitables without creating
and closing an event loop for every tool call.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from typing import Any

_TOOL_LOOP: asyncio.AbstractEventLoop | None = None
_TOOL_LOOP_LOCK = threading.Lock()
_WORKER_THREAD_LOCAL = threading.local()
DEFAULT_ASYNC_TIMEOUT = 300.0


def _get_tool_loop() -> asyncio.AbstractEventLoop:
    global _TOOL_LOOP
    with _TOOL_LOOP_LOCK:
        if _TOOL_LOOP is None or _TOOL_LOOP.is_closed():
            _TOOL_LOOP = asyncio.new_event_loop()
        return _TOOL_LOOP


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_WORKER_THREAD_LOCAL, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _WORKER_THREAD_LOCAL.loop = loop
    return loop


def _cancel_pending(loop: asyncio.AbstractEventLoop) -> None:
    try:
        pending = asyncio.all_tasks(loop)
    except RuntimeError:
        return
    for task in pending:
        task.cancel()
    if not pending:
        return
    try:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:  # noqa: BLE001
        pass


def _run_in_isolated_thread(awaitable: Any, *, timeout: float) -> Any:
    worker_loop: asyncio.AbstractEventLoop | None = None
    loop_ready = threading.Event()

    def run_worker() -> Any:
        nonlocal worker_loop
        worker_loop = asyncio.new_event_loop()
        loop_ready.set()
        try:
            asyncio.set_event_loop(worker_loop)
            return worker_loop.run_until_complete(awaitable)
        finally:
            _cancel_pending(worker_loop)
            worker_loop.close()

    try:
        from .thread_context import propagate_context_to_thread
        target = propagate_context_to_thread(run_worker)
    except Exception:  # noqa: BLE001
        target = run_worker

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(target)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        if loop_ready.wait(timeout=1.0) and worker_loop is not None:
            try:
                for task in asyncio.all_tasks(worker_loop):
                    worker_loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        raise
    finally:
        pool.shutdown(wait=False)


def run_sync_awaitable(value: Any, *, timeout: float = DEFAULT_ASYNC_TIMEOUT) -> Any:
    """Resolve *value* if it is awaitable, otherwise return it unchanged."""
    if not inspect.isawaitable(value):
        return value
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if running_loop is not None and running_loop.is_running():
        return _run_in_isolated_thread(value, timeout=float(timeout or DEFAULT_ASYNC_TIMEOUT))
    if threading.current_thread() is not threading.main_thread():
        return _get_worker_loop().run_until_complete(value)
    return _get_tool_loop().run_until_complete(value)
