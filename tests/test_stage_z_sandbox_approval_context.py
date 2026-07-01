"""Stage Z sandbox/backend context for approval prompts."""

from __future__ import annotations

import threading
import time

from aegis.config import Config
from aegis.tools.base import ToolContext
from aegis.tools.builtin import BashTool
from aegis.tools.permissions import PermissionEngine
from aegis.tools.process import ProcessTool
from aegis.tools.thread_context import (
    await_pending_approval,
    clear_pending_approvals,
    pending_approval_count,
    pending_approvals,
    propagate_context_to_thread,
    register_approval_notifier,
    reset_current_observability_context,
    reset_current_runtime_context,
    reset_current_session_key,
    resolve_pending_approval,
    set_current_observability_context,
    set_current_runtime_context,
    set_current_session_key,
    unregister_approval_notifier,
)


def _config(backend: str = "local") -> Config:
    cfg = Config.load()
    cfg.set("security.scan_enabled", False)
    cfg.set("tools.allowlist", [])
    cfg.set("tools.deny_groups", [])
    cfg.set("tools.exec_mode", "ask")
    cfg.set("tools.terminal_backend", backend)
    cfg.set("tools.approval_timeout_seconds", 1.0)
    cfg.set("tools.approval_poll_interval_seconds", 0.01)
    return cfg


def _wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _threaded_authorize(
    engine: PermissionEngine,
    tool,
    args: dict,
    ctx: ToolContext,
    session_key: str,
) -> tuple[threading.Thread, dict[str, tuple[bool, str]]]:
    result: dict[str, tuple[bool, str]] = {}
    session_token = set_current_session_key(session_key)
    obs_tokens = set_current_observability_context(
        turn_id="turn-stage-z-sandbox",
        tool_call_id="call-stage-z-sandbox",
    )
    try:
        target = propagate_context_to_thread(
            lambda: result.setdefault("value", engine.authorize(tool, args, ctx))
        )
    finally:
        reset_current_observability_context(obs_tokens)
        reset_current_session_key(session_token)
    thread = threading.Thread(target=target)
    thread.start()
    return thread, result


def test_bash_approval_payload_carries_effective_docker_context(tmp_path):
    from aegis.tools.backends import clear_task_env_overrides, register_task_env_overrides

    session_key = "stage-z-sandbox-context-bash"
    task_id = "stage-z-docker-task"
    effective_cwd = tmp_path / "docker-work"
    cfg = _config("local")
    notifications: list[dict] = []
    clear_pending_approvals(session_key)
    register_task_env_overrides(
        task_id,
        {"terminal_backend": "docker", "cwd": str(effective_cwd)},
    )
    register_approval_notifier(session_key, notifications.append)

    thread, result = _threaded_authorize(
        PermissionEngine(cfg),
        BashTool(),
        {"command": "printf ok", "timeout": 7},
        ToolContext(cwd=tmp_path, config=cfg, task_id=task_id),
        session_key,
    )
    try:
        assert _wait_until(lambda: len(notifications) == 1)
        payload = notifications[0]
        queued = pending_approvals(session_key)[0]

        assert payload["session_key"] == session_key
        assert payload["turn_id"] == "turn-stage-z-sandbox"
        assert payload["tool_call_id"] == "call-stage-z-sandbox"
        assert payload["tool"] == "bash"
        assert payload["target"] == "printf ok"
        assert payload["configured_backend"] == "local"
        assert payload["backend"] == "docker"
        assert payload["task_id"] == task_id
        assert payload["cwd"] == str(effective_cwd)
        assert payload["execution"]["backend"] == "docker"
        assert payload["execution"]["timeout"] == 7
        assert payload["sandbox"]["is_sandbox"] is True
        assert payload["sandbox"]["host_workspace_mounted"] is True
        assert queued["backend"] == "docker"

        assert resolve_pending_approval(session_key, "once") == 1
        thread.join(timeout=1)
    finally:
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)
        clear_task_env_overrides(task_id)
        thread.join(timeout=1)

    assert not thread.is_alive()
    ok, reason = result["value"]
    assert ok is True
    assert "approved" in reason.lower()
    assert pending_approval_count(session_key) == 0


def test_process_start_denial_keeps_backend_context_until_decision(tmp_path):
    session_key = "stage-z-sandbox-context-process-deny"
    task_id = "stage-z-process-task"
    cfg = _config("docker")
    notifications: list[dict] = []
    clear_pending_approvals(session_key)
    register_approval_notifier(session_key, notifications.append)

    thread, result = _threaded_authorize(
        PermissionEngine(cfg),
        ProcessTool(),
        {"action": "start", "command": "python -m http.server", "timeout": 9},
        ToolContext(cwd=tmp_path, config=cfg, task_id=task_id),
        session_key,
    )
    try:
        assert _wait_until(lambda: pending_approval_count(session_key) == 1)
        payload = pending_approvals(session_key)[0]

        assert notifications[0]["backend"] == "docker"
        assert payload["tool"] == "process"
        assert payload["backend"] == "docker"
        assert payload["task_id"] == task_id
        assert payload["execution_surface"] == "process_start"
        assert payload["execution"]["process_action"] == "start"
        assert payload["background"] is True
        assert payload["sandbox"]["is_sandbox"] is True

        assert resolve_pending_approval(session_key, "deny") == 1
        thread.join(timeout=1)
    finally:
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    assert not thread.is_alive()
    ok, reason = result["value"]
    assert ok is False
    assert "denied" in reason.lower()
    assert pending_approval_count(session_key) == 0


def test_direct_pending_approval_inherits_runtime_context_across_worker_thread(tmp_path):
    session_key = "stage-z-sandbox-context-direct"
    notifications: list[dict] = []
    result = {}
    clear_pending_approvals(session_key)
    register_approval_notifier(session_key, notifications.append)

    session_token = set_current_session_key(session_key)
    obs_tokens = set_current_observability_context(
        turn_id="turn-stage-z-direct",
        tool_call_id="call-stage-z-direct",
    )
    runtime_tokens = set_current_runtime_context(
        tool_name="bash",
        backend="modal",
        task_id="stage-z-direct-task",
        cwd=str(tmp_path),
        surface="sandbox-rpc",
    )
    try:
        target = propagate_context_to_thread(
            lambda: result.setdefault(
                "decision",
                await_pending_approval(
                    {"command": "printf direct"},
                    timeout_seconds=1,
                    poll_interval=0.01,
                ),
            )
        )
    finally:
        reset_current_runtime_context(runtime_tokens)
        reset_current_observability_context(obs_tokens)
        reset_current_session_key(session_token)

    thread = threading.Thread(target=target)
    thread.start()
    try:
        assert _wait_until(lambda: len(notifications) == 1)
        payload = notifications[0]

        assert payload["session_key"] == session_key
        assert payload["turn_id"] == "turn-stage-z-direct"
        assert payload["tool_call_id"] == "call-stage-z-direct"
        assert payload["tool"] == "bash"
        assert payload["backend"] == "modal"
        assert payload["task_id"] == "stage-z-direct-task"
        assert payload["cwd"] == str(tmp_path)
        assert payload["surface"] == "sandbox-rpc"

        assert resolve_pending_approval(session_key, "approve") == 1
        thread.join(timeout=1)
    finally:
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    assert not thread.is_alive()
    decision = result["decision"]
    assert decision.resolved is True
    assert decision.choice == "approve"
    assert decision.outcome == "approved"
