"""Stage Z approval/thread-context primitives."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from aegis.tools.interrupt import _interrupt_event, clear_interrupt, is_interrupted, set_interrupt
from aegis.tools.thread_context import (
    await_pending_approval,
    clear_pending_approvals,
    get_current_approval_context,
    get_current_approver,
    has_pending_approval,
    pending_approval_count,
    pending_approvals,
    propagate_context_to_thread,
    register_approval_notifier,
    reset_current_approver,
    reset_current_observability_context,
    reset_current_session_key,
    resolve_pending_approval,
    set_current_approver,
    set_current_observability_context,
    set_current_session_key,
    unregister_approval_notifier,
)


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_propagate_context_to_thread_carries_approval_context_and_clears(monkeypatch):
    monkeypatch.delenv("AEGIS_SESSION_KEY", raising=False)
    monkeypatch.delenv("AEGIS_SESSION_ID", raising=False)

    def approver(prompt: str) -> str:
        return f"approved:{prompt}"

    session_token = set_current_session_key("session-a")
    obs_tokens = set_current_observability_context(turn_id="turn-1", tool_call_id="call-1")
    approver_token = set_current_approver(approver)
    try:
        def read_context():
            ctx = get_current_approval_context(default_session_key="none")
            cb = get_current_approver()
            return {
                "session_key": ctx.session_key,
                "turn_id": ctx.turn_id,
                "tool_call_id": ctx.tool_call_id,
                "approval": cb("prompt") if cb else None,
            }

        with ThreadPoolExecutor(max_workers=1) as pool:
            propagated = pool.submit(propagate_context_to_thread(read_context)).result()
            plain_afterward = pool.submit(read_context).result()
    finally:
        reset_current_approver(approver_token)
        reset_current_observability_context(obs_tokens)
        reset_current_session_key(session_token)

    assert propagated == {
        "session_key": "session-a",
        "turn_id": "turn-1",
        "tool_call_id": "call-1",
        "approval": "approved:prompt",
    }
    assert plain_afterward == {
        "session_key": "none",
        "turn_id": "",
        "tool_call_id": "",
        "approval": None,
    }
    assert get_current_approval_context(default_session_key="none").session_key == "none"
    assert get_current_approver() is None


def test_interrupt_state_is_thread_scoped_and_event_proxy_compatible():
    clear_interrupt()
    _interrupt_event.set()
    assert is_interrupted()
    assert _interrupt_event.is_set()
    _interrupt_event.clear()
    assert not is_interrupted()

    ready = threading.Event()
    saw_interrupt = threading.Event()
    thread_id: dict[str, int] = {}

    def worker():
        thread_id["value"] = threading.current_thread().ident or 0
        ready.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if is_interrupted():
                saw_interrupt.set()
                break
            time.sleep(0.01)
        clear_interrupt()

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert ready.wait(1)
        assert not is_interrupted()
        set_interrupt(True, thread_id["value"])
        assert saw_interrupt.wait(1)
    finally:
        clear_interrupt(thread_id.get("value"))
        thread.join(timeout=1)

    assert not is_interrupted(thread_id["value"])


def test_pending_approval_queue_is_fifo_and_session_scoped():
    session_key = "stage-z-fifo"
    clear_pending_approvals(session_key)
    notifications: list[str] = []
    results = {}

    def wait_for(label: str):
        results[label] = await_pending_approval(
            {"command": label},
            session_key=session_key,
            notify=lambda data: notifications.append(data["command"]),
            timeout_seconds=2,
            poll_interval=0.01,
        )

    first = threading.Thread(target=wait_for, args=("first",))
    second = threading.Thread(target=wait_for, args=("second",))
    first.start()
    try:
        assert _wait_until(lambda: pending_approval_count(session_key) == 1)
        second.start()
        assert _wait_until(lambda: pending_approval_count(session_key) == 2)
        assert [row["command"] for row in pending_approvals(session_key)] == [
            "first",
            "second",
        ]
        assert notifications == ["first", "second"]

        assert resolve_pending_approval(session_key, "once") == 1
        assert _wait_until(lambda: pending_approval_count(session_key) == 1)
        assert resolve_pending_approval(session_key, "session") == 1

        first.join(timeout=1)
        second.join(timeout=1)
    finally:
        clear_pending_approvals(session_key)
        first.join(timeout=1)
        second.join(timeout=1)

    assert results["first"].resolved is True
    assert results["first"].choice == "once"
    assert results["first"].outcome == "approved"
    assert results["second"].resolved is True
    assert results["second"].choice == "session"
    assert results["second"].outcome == "approved"
    assert not has_pending_approval(session_key)


def test_unregister_notifier_unblocks_waiting_approval():
    session_key = "stage-z-unregister"
    clear_pending_approvals(session_key)
    register_approval_notifier(session_key, lambda _data: None)
    result = {}

    def wait_for_approval():
        result["decision"] = await_pending_approval(
            {"command": "write"},
            session_key=session_key,
            timeout_seconds=5,
            poll_interval=0.01,
        )

    thread = threading.Thread(target=wait_for_approval)
    thread.start()
    try:
        assert _wait_until(lambda: pending_approval_count(session_key) == 1)
        assert unregister_approval_notifier(session_key) == 1
        thread.join(timeout=1)
    finally:
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    decision = result["decision"]
    assert decision.resolved is True
    assert decision.choice == "deny"
    assert decision.outcome == "denied"
    assert not has_pending_approval(session_key)


def test_pending_approval_wait_honors_thread_interrupt():
    session_key = "stage-z-interrupt"
    clear_pending_approvals(session_key)
    ready = threading.Event()
    thread_id: dict[str, int] = {}
    result = {}

    def notify(_data):
        thread_id["value"] = threading.current_thread().ident or 0
        ready.set()

    def wait_for_approval():
        result["decision"] = await_pending_approval(
            {"command": "delete"},
            session_key=session_key,
            notify=notify,
            timeout_seconds=5,
            poll_interval=0.01,
        )

    thread = threading.Thread(target=wait_for_approval)
    thread.start()
    try:
        assert ready.wait(1)
        assert pending_approval_count(session_key) == 1
        set_interrupt(True, thread_id["value"])
        thread.join(timeout=1)
    finally:
        clear_interrupt(thread_id.get("value"))
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    decision = result["decision"]
    assert decision.resolved is True
    assert decision.choice == "deny"
    assert decision.outcome == "interrupted"
    assert decision.interrupted is True
    assert not has_pending_approval(session_key)
