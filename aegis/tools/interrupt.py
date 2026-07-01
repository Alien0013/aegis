"""Thread-scoped interrupt signaling for tool workers.

AEGIS agents can run multiple tool workers in one process. A process-global
event makes an interrupt for one turn visible to unrelated workers, so this
module keeps interrupt state keyed by thread id. Tool code can call
``is_interrupted()`` without knowing which agent owns the current worker.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

_LOCK = threading.Lock()
_INTERRUPTED_THREADS: set[int] = set()
_INTERRUPT_HOOKS: dict[int, list[Callable[[], None]]] = {}


def _current_thread_id() -> int | None:
    return threading.current_thread().ident


def set_interrupt(active: bool, thread_id: int | None = None) -> None:
    """Set or clear interrupt state for *thread_id*.

    When *thread_id* is omitted, the current thread is targeted. Unknown thread
    ids are accepted so callers can mark a worker before it polls.
    """
    tid = thread_id if thread_id is not None else _current_thread_id()
    if tid is None:
        return
    hooks: list[Callable[[], None]] = []
    with _LOCK:
        if active:
            _INTERRUPTED_THREADS.add(tid)
            hooks = list(_INTERRUPT_HOOKS.get(tid, ()))
        else:
            _INTERRUPTED_THREADS.discard(tid)
    for hook in hooks:
        try:
            hook()
        except Exception:
            pass


def clear_interrupt(thread_id: int | None = None) -> None:
    """Clear interrupt state for *thread_id* or the current thread."""
    set_interrupt(False, thread_id=thread_id)


def is_interrupted(thread_id: int | None = None) -> bool:
    """Return whether *thread_id* or the current thread has been interrupted."""
    tid = thread_id if thread_id is not None else _current_thread_id()
    if tid is None:
        return False
    with _LOCK:
        return tid in _INTERRUPTED_THREADS


def interrupted_thread_ids() -> set[int]:
    """Return a snapshot of interrupted thread ids for diagnostics/tests."""
    with _LOCK:
        return set(_INTERRUPTED_THREADS)


def register_interrupt_hook(
    hook: Callable[[], None],
    thread_id: int | None = None,
) -> Callable[[], None]:
    """Register *hook* to run when *thread_id* is interrupted.

    The returned callable unregisters the hook. Hooks are best-effort and run
    outside the interrupt lock so they can terminate subprocesses without
    blocking unrelated interrupt state.
    """
    tid = thread_id if thread_id is not None else _current_thread_id()
    if tid is None:
        return lambda: None
    with _LOCK:
        _INTERRUPT_HOOKS.setdefault(tid, []).append(hook)
        already_interrupted = tid in _INTERRUPTED_THREADS
    if already_interrupted:
        try:
            hook()
        except Exception:
            pass

    def unregister() -> None:
        with _LOCK:
            hooks = _INTERRUPT_HOOKS.get(tid)
            if not hooks:
                return
            try:
                hooks.remove(hook)
            except ValueError:
                return
            if not hooks:
                _INTERRUPT_HOOKS.pop(tid, None)

    return unregister


class _ThreadInterruptEvent:
    """Compatibility proxy with the subset of ``threading.Event`` AEGIS needs."""

    def is_set(self) -> bool:
        return is_interrupted()

    def set(self) -> None:  # noqa: A003
        set_interrupt(True)

    def clear(self) -> None:
        clear_interrupt()

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        if timeout and timeout > 0:
            threading.Event().wait(timeout)
        return self.is_set()


_interrupt_event = _ThreadInterruptEvent()


__all__ = [
    "_interrupt_event",
    "clear_interrupt",
    "interrupted_thread_ids",
    "is_interrupted",
    "register_interrupt_hook",
    "set_interrupt",
]
