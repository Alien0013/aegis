"""Stage Z approval wiring contracts for production permission prompts."""

from __future__ import annotations

import threading
import time

from aegis.config import Config
from aegis.tools.base import ToolContext
from aegis.tools.builtin import BashTool
from aegis.tools.permissions import PermissionEngine
from aegis.tools.thread_context import (
    clear_pending_approvals,
    pending_approval_count,
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


def _config() -> Config:
    cfg = Config.load()
    cfg.set("security.scan_enabled", False)
    cfg.set("tools.allowlist", [])
    cfg.set("tools.deny_groups", [])
    cfg.set("tools.exec_mode", "ask")
    cfg.set("tools.approval_timeout_seconds", 0.05)
    cfg.set("tools.approval_poll_interval_seconds", 0.01)
    return cfg


def _wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _authorize_bash(engine: PermissionEngine, ctx: ToolContext) -> tuple[bool, str]:
    return engine.authorize(BashTool(), {"command": "printf ok"}, ctx)


def _threaded_authorize(
    engine: PermissionEngine,
    session_key: str,
    *,
    turn_id: str = "turn-stage-z",
    tool_call_id: str = "call-stage-z",
) -> tuple[threading.Thread, dict[str, tuple[bool, str]]]:
    result: dict[str, tuple[bool, str]] = {}
    session_token = set_current_session_key(session_key)
    obs_tokens = set_current_observability_context(
        turn_id=turn_id,
        tool_call_id=tool_call_id,
    )
    try:
        target = propagate_context_to_thread(
            lambda: result.setdefault(
                "value",
                _authorize_bash(engine, ToolContext(config=engine.config)),
            )
        )
    finally:
        reset_current_observability_context(obs_tokens)
        reset_current_session_key(session_token)
    thread = threading.Thread(target=target)
    thread.start()
    return thread, result


def test_direct_ctx_approver_wins_and_does_not_enqueue():
    session_key = "stage-z-wiring-direct"
    clear_pending_approvals(session_key)
    notifications: list[dict] = []
    direct_prompts: list[str] = []
    context_prompts: list[str] = []
    register_approval_notifier(session_key, notifications.append)
    session_token = set_current_session_key(session_key)
    approver_token = set_current_approver(
        lambda prompt: context_prompts.append(prompt) or False
    )
    try:
        ctx = ToolContext(
            config=_config(),
            approver=lambda prompt: direct_prompts.append(prompt) or True,
        )
        ok, reason = _authorize_bash(PermissionEngine(ctx.config), ctx)
    finally:
        reset_current_approver(approver_token)
        reset_current_session_key(session_token)
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)

    assert ok is True
    assert reason == "approved by user"
    assert direct_prompts == ["Allow bash(printf ok)?"]
    assert context_prompts == []
    assert notifications == []
    assert pending_approval_count(session_key) == 0


def test_permission_engine_falls_back_to_context_local_approver_when_ctx_absent():
    session_key = "stage-z-wiring-context-approver"
    clear_pending_approvals(session_key)
    prompts: list[str] = []
    session_token = set_current_session_key(session_key)
    approver_token = set_current_approver(lambda prompt: prompts.append(prompt) or True)
    try:
        ok, reason = _authorize_bash(
            PermissionEngine(_config()),
            ToolContext(config=_config()),
        )
    finally:
        reset_current_approver(approver_token)
        reset_current_session_key(session_token)
        clear_pending_approvals(session_key)

    assert ok is True
    assert reason == "approved by user"
    assert prompts == ["Allow bash(printf ok)?"]
    assert pending_approval_count(session_key) == 0


def test_queued_approval_notifies_and_resolve_unblocks_authorize():
    session_key = "stage-z-wiring-queue-allow"
    clear_pending_approvals(session_key)
    notifications: list[dict] = []
    cfg = _config()
    register_approval_notifier(session_key, notifications.append)

    thread, result = _threaded_authorize(PermissionEngine(cfg), session_key)
    try:
        assert _wait_until(lambda: len(notifications) == 1)
        assert notifications[0]["session_key"] == session_key
        assert notifications[0]["prompt"] == "Allow bash(printf ok)?"
        assert pending_approval_count(session_key) == 1

        assert resolve_pending_approval(session_key, "once") == 1
        thread.join(timeout=1)
    finally:
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    assert not thread.is_alive()
    ok, reason = result["value"]
    assert ok is True
    assert "approved" in reason.lower()
    assert pending_approval_count(session_key) == 0


def test_queued_approval_unregister_denies_and_unblocks_authorize():
    session_key = "stage-z-wiring-queue-unregister"
    clear_pending_approvals(session_key)
    notifications: list[dict] = []
    cfg = _config()
    register_approval_notifier(session_key, notifications.append)

    thread, result = _threaded_authorize(PermissionEngine(cfg), session_key)
    try:
        assert _wait_until(lambda: pending_approval_count(session_key) == 1)
        assert len(notifications) == 1
        assert unregister_approval_notifier(session_key, choice="deny") == 1
        thread.join(timeout=1)
    finally:
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    assert not thread.is_alive()
    ok, reason = result["value"]
    assert ok is False
    assert "denied" in reason.lower() or "rejected" in reason.lower()
    assert pending_approval_count(session_key) == 0


def test_queued_approval_timeout_denies_and_clears_pending_authorize():
    session_key = "stage-z-wiring-queue-timeout"
    clear_pending_approvals(session_key)
    notifications: list[dict] = []
    cfg = _config()
    register_approval_notifier(session_key, notifications.append)

    thread, result = _threaded_authorize(PermissionEngine(cfg), session_key)
    try:
        assert _wait_until(lambda: len(notifications) == 1)
        thread.join(timeout=1)
        if thread.is_alive():
            clear_pending_approvals(session_key)
            thread.join(timeout=1)
    finally:
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    assert not thread.is_alive()
    ok, reason = result["value"]
    assert ok is False
    assert "timed out" in reason.lower() or "timeout" in reason.lower()
    assert pending_approval_count(session_key) == 0
