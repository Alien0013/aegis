"""Context and approval primitives for tool worker threads.

This module gives AEGIS one place to bind approval/session metadata, propagate
it into worker threads, and coordinate blocking approval waits. It deliberately
does not wire itself into the tool executor yet; the integration points are the
small helpers exported here.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .interrupt import is_interrupted

logger = logging.getLogger(__name__)

Approver = Callable[[str], bool | str]
ApprovalNotifier = Callable[[dict[str, Any]], None]
InterruptCheck = Callable[[], bool]

_approval_session_key: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_approval_session_key",
    default="",
)
_approval_turn_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_approval_turn_id",
    default="",
)
_approval_tool_call_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_approval_tool_call_id",
    default="",
)
_approval_tool_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_approval_tool_name",
    default="",
)
_approval_backend: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_approval_backend",
    default="",
)
_approval_task_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_approval_task_id",
    default="",
)
_approval_cwd: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_approval_cwd",
    default="",
)
_approval_surface: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aegis_approval_surface",
    default="",
)
_approval_approver: contextvars.ContextVar[Approver | None] = contextvars.ContextVar(
    "aegis_approval_approver",
    default=None,
)


@dataclass(frozen=True)
class ApprovalContext:
    """Current context-local approval identity."""

    session_key: str
    turn_id: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    backend: str = ""
    task_id: str = ""
    cwd: str = ""
    surface: str = ""


@dataclass
class PendingApproval:
    """One queued approval request for a session."""

    session_key: str
    data: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    choice: str | None = None


@dataclass(frozen=True)
class ApprovalDecision:
    """Result returned by ``await_pending_approval``."""

    resolved: bool
    choice: str | None
    outcome: str
    notify_failed: bool = False
    interrupted: bool = False


_LOCK = threading.Lock()
_PENDING: dict[str, deque[PendingApproval]] = {}
_NOTIFIERS: dict[str, ApprovalNotifier] = {}


def set_current_session_key(session_key: str) -> contextvars.Token[str]:
    """Bind an approval session key to the current context."""
    return _approval_session_key.set(str(session_key or ""))


def reset_current_session_key(token: contextvars.Token[str]) -> None:
    """Restore the previous approval session key."""
    _approval_session_key.reset(token)


def get_current_session_key(default: str = "default") -> str:
    """Return the active context-local session key with environment fallback."""
    session_key = _approval_session_key.get()
    if session_key:
        return session_key
    return os.getenv("AEGIS_SESSION_KEY") or os.getenv("AEGIS_SESSION_ID") or default


def set_current_observability_context(
    *,
    turn_id: str = "",
    tool_call_id: str = "",
) -> tuple[contextvars.Token[str], contextvars.Token[str]]:
    """Bind active turn/tool-call ids to the current context."""
    return (
        _approval_turn_id.set(str(turn_id or "")),
        _approval_tool_call_id.set(str(tool_call_id or "")),
    )


def reset_current_observability_context(
    tokens: tuple[contextvars.Token[str], contextvars.Token[str]],
) -> None:
    """Restore the previous turn/tool-call ids."""
    turn_token, tool_token = tokens
    _approval_tool_call_id.reset(tool_token)
    _approval_turn_id.reset(turn_token)


def set_current_runtime_context(
    *,
    tool_name: str = "",
    backend: str = "",
    task_id: str = "",
    cwd: str = "",
    surface: str = "",
) -> tuple[contextvars.Token[str], contextvars.Token[str], contextvars.Token[str],
           contextvars.Token[str], contextvars.Token[str]]:
    """Bind runtime/backend approval metadata to the current context."""
    return (
        _approval_tool_name.set(str(tool_name or "")),
        _approval_backend.set(str(backend or "")),
        _approval_task_id.set(str(task_id or "")),
        _approval_cwd.set(str(cwd or "")),
        _approval_surface.set(str(surface or "")),
    )


def reset_current_runtime_context(
    tokens: tuple[contextvars.Token[str], contextvars.Token[str], contextvars.Token[str],
                  contextvars.Token[str], contextvars.Token[str]],
) -> None:
    """Restore the previous runtime/backend approval metadata."""
    tool_token, backend_token, task_token, cwd_token, surface_token = tokens
    _approval_surface.reset(surface_token)
    _approval_cwd.reset(cwd_token)
    _approval_task_id.reset(task_token)
    _approval_backend.reset(backend_token)
    _approval_tool_name.reset(tool_token)


def get_current_approval_context(default_session_key: str = "default") -> ApprovalContext:
    """Return the current approval identity as a value object."""
    return ApprovalContext(
        session_key=get_current_session_key(default_session_key),
        turn_id=_approval_turn_id.get(),
        tool_call_id=_approval_tool_call_id.get(),
        tool_name=_approval_tool_name.get(),
        backend=_approval_backend.get(),
        task_id=_approval_task_id.get(),
        cwd=_approval_cwd.get(),
        surface=_approval_surface.get(),
    )


def set_current_approver(approver: Approver | None) -> contextvars.Token[Approver | None]:
    """Bind an approver callback to the current context."""
    return _approval_approver.set(approver)


def reset_current_approver(token: contextvars.Token[Approver | None]) -> None:
    """Restore the previous approver callback."""
    _approval_approver.reset(token)


def get_current_approver(default: Approver | None = None) -> Approver | None:
    """Return the current context-local approver callback."""
    return _approval_approver.get() or default


def propagate_context_to_thread(target: Callable) -> Callable:
    """Wrap *target* so it runs with the caller's current ``contextvars``.

    Call this on the parent thread and pass the returned callable to
    ``threading.Thread`` or ``ThreadPoolExecutor``. The copied context is active
    only for the wrapped call, so reused executor threads do not retain stale
    session ids or approvers after the call returns.
    """
    ctx = contextvars.copy_context()

    def _runner(*args, **kwargs):
        # One copied Context cannot be entered concurrently by two executor
        # workers, so each invocation gets its own child copy of the captured
        # parent context.
        return ctx.copy().run(target, *args, **kwargs)

    return _runner


def register_approval_notifier(session_key: str, cb: ApprovalNotifier) -> None:
    """Register a callback that surfaces queued approval requests to a user."""
    if not session_key:
        return
    with _LOCK:
        _NOTIFIERS[str(session_key)] = cb


def has_approval_notifier(session_key: str) -> bool:
    """Return whether *session_key* has a registered approval notifier."""
    if not session_key:
        return False
    with _LOCK:
        return str(session_key) in _NOTIFIERS


def unregister_approval_notifier(session_key: str, *, choice: str = "deny") -> int:
    """Remove a notifier and resolve any blocked waits for the session."""
    if not session_key:
        return 0
    with _LOCK:
        _NOTIFIERS.pop(str(session_key), None)
        entries = list(_PENDING.pop(str(session_key), deque()))
    for entry in entries:
        entry.choice = choice
        entry.event.set()
    return len(entries)


def pending_approval_count(session_key: str) -> int:
    """Return the number of queued approval waits for *session_key*."""
    with _LOCK:
        return len(_PENDING.get(str(session_key), ()))


def has_pending_approval(session_key: str) -> bool:
    """Return whether *session_key* has pending approval waits."""
    return pending_approval_count(session_key) > 0


def pending_approvals(session_key: str) -> list[dict[str, Any]]:
    """Return copies of queued approval payloads for diagnostics/UI surfaces."""
    with _LOCK:
        return [dict(entry.data) for entry in _PENDING.get(str(session_key), ())]


def all_pending_approvals() -> list[dict[str, Any]]:
    """Return copies of all queued approval payloads for resolver surfaces."""
    with _LOCK:
        rows: list[dict[str, Any]] = []
        for queue in _PENDING.values():
            rows.extend(dict(entry.data) for entry in queue)
        return rows


def _approval_prompt_id(data: dict[str, Any]) -> str:
    return str(
        data.get("prompt_id")
        or data.get("callback_prompt_id")
        or data.get("action_prompt_id")
        or ""
    ).strip()


def resolve_pending_approval(
    session_key: str,
    choice: str,
    *,
    resolve_all: bool = False,
) -> int:
    """Resolve one or all queued approvals for *session_key* in FIFO order."""
    if not session_key:
        return 0
    with _LOCK:
        queue = _PENDING.get(str(session_key))
        if not queue:
            return 0
        if resolve_all:
            targets = list(queue)
            queue.clear()
        else:
            targets = [queue.popleft()]
        if not queue:
            _PENDING.pop(str(session_key), None)
    for entry in targets:
        entry.choice = choice
        entry.event.set()
    return len(targets)


def resolve_pending_approval_by_prompt_id(
    session_key: str,
    prompt_id: str,
    choice: str,
) -> int:
    """Resolve one queued approval in *session_key* matching *prompt_id*."""
    if not session_key or not prompt_id:
        return 0
    target: PendingApproval | None = None
    with _LOCK:
        queue = _PENDING.get(str(session_key))
        if not queue:
            return 0
        for entry in list(queue):
            if _approval_prompt_id(entry.data) == str(prompt_id).strip():
                target = entry
                queue.remove(entry)
                break
        if target is None:
            return 0
        if not queue:
            _PENDING.pop(str(session_key), None)
    target.choice = choice
    target.event.set()
    return 1


def clear_pending_approvals(session_key: str, *, choice: str = "deny") -> int:
    """Resolve and remove all queued approvals for *session_key*."""
    return resolve_pending_approval(session_key, choice, resolve_all=True)


def await_pending_approval(
    approval_data: dict[str, Any],
    *,
    session_key: str | None = None,
    notify: ApprovalNotifier | None = None,
    timeout_seconds: float = 300,
    poll_interval: float = 1.0,
    interrupt_check: InterruptCheck | None = None,
) -> ApprovalDecision:
    """Queue an approval request and wait for a user decision.

    The wait loop polls in short slices so an interrupt can return a denial
    immediately instead of waiting for the full approval timeout. Missing
    notifiers are allowed at this primitive layer; callers can decide whether
    to treat that as a compatibility ``pending`` state or a fail-closed block.
    """
    key = str(session_key or get_current_session_key(default="default"))
    payload = dict(approval_data or {})
    payload.setdefault("session_key", key)
    context = get_current_approval_context(default_session_key=key)
    payload.setdefault("turn_id", context.turn_id)
    payload.setdefault("tool_call_id", context.tool_call_id)
    payload.setdefault("tool", context.tool_name)
    payload.setdefault("backend", context.backend)
    payload.setdefault("task_id", context.task_id)
    payload.setdefault("cwd", context.cwd)
    payload.setdefault("surface", context.surface)
    entry = PendingApproval(session_key=key, data=payload)
    with _LOCK:
        _PENDING.setdefault(key, deque()).append(entry)
        notify_cb = notify or _NOTIFIERS.get(key)

    def _drop_entry() -> None:
        with _LOCK:
            queue = _PENDING.get(key)
            if queue and entry in queue:
                queue.remove(entry)
            if queue is not None and not queue:
                _PENDING.pop(key, None)

    if notify_cb is not None:
        try:
            notify_cb(dict(payload))
        except Exception:
            logger.warning("Approval notifier failed for session %s", key, exc_info=True)
            _drop_entry()
            return ApprovalDecision(
                resolved=False,
                choice=None,
                outcome="notify_failed",
                notify_failed=True,
            )

    check_interrupt = interrupt_check or is_interrupted
    deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
    resolved = False
    interrupted = False
    while True:
        if check_interrupt():
            entry.choice = "deny"
            entry.event.set()
            resolved = True
            interrupted = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait_for = min(max(float(poll_interval), 0.01), remaining)
        if entry.event.wait(timeout=wait_for):
            resolved = True
            break

    _drop_entry()
    choice = entry.choice
    if interrupted:
        outcome = "interrupted"
    elif not resolved:
        outcome = "timeout"
    elif choice in {None, "", "deny", "denied", "reject", "rejected"}:
        outcome = "denied"
    else:
        outcome = "approved"
    return ApprovalDecision(
        resolved=resolved,
        choice=choice,
        outcome=outcome,
        interrupted=interrupted,
    )


__all__ = [
    "ApprovalContext",
    "ApprovalDecision",
    "PendingApproval",
    "all_pending_approvals",
    "await_pending_approval",
    "clear_pending_approvals",
    "get_current_approval_context",
    "get_current_approver",
    "get_current_session_key",
    "has_approval_notifier",
    "has_pending_approval",
    "pending_approval_count",
    "pending_approvals",
    "propagate_context_to_thread",
    "register_approval_notifier",
    "reset_current_approver",
    "reset_current_observability_context",
    "reset_current_runtime_context",
    "reset_current_session_key",
    "resolve_pending_approval",
    "resolve_pending_approval_by_prompt_id",
    "set_current_approver",
    "set_current_observability_context",
    "set_current_runtime_context",
    "set_current_session_key",
    "unregister_approval_notifier",
]
