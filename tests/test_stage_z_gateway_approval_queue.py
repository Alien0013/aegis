"""Gateway integration contracts for the shared pending approval queue."""

from __future__ import annotations

import threading
import time

import pytest

from aegis.gateway.base import BasePlatformAdapter, MessageEvent
from aegis.tools.thread_context import (
    await_pending_approval,
    clear_pending_approvals,
    pending_approval_count,
)


class FakeApprovalAdapter(BasePlatformAdapter):
    name = "fake"

    def __init__(self):
        self.sent: list[tuple[str, str, dict]] = []

    def send(self, chat_id: str, text: str, *, metadata: dict | None = None) -> None:
        self.sent.append((chat_id, text, dict(metadata or {})))


def _wait_until(predicate, timeout: float = 0.75) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _event(
    text: str,
    *,
    prompt_id: str,
    session_key: str,
    user_id: str,
) -> MessageEvent:
    return MessageEvent(
        platform="fake",
        chat_id="chat-1",
        text=text,
        user_id=user_id,
        session_key=session_key,
        metadata={
            "prompt_id": prompt_id,
            "prompt_kind": "exec_approval",
        },
    )


def _approval_payload(
    *,
    prompt_id: str,
    session_key: str,
    user_id: str,
) -> dict:
    return {
        "prompt": "Allow bash(ls)?",
        "prompt_id": prompt_id,
        "prompt_kind": "exec_approval",
        "session_key": session_key,
        "prompt_session_key": session_key,
        "user_id": user_id,
        "prompt_user_id": user_id,
    }


def test_inbound_exec_approval_response_resolves_thread_context_pending_approval():
    session_key = "stage-z-gateway-queue-match"
    prompt_id = "exec_approval:match"
    user_id = "user-1"
    clear_pending_approvals(session_key)
    adapter = FakeApprovalAdapter()
    dispatched: list[str] = []
    adapter._init_inbound_queue(lambda ev: dispatched.append(ev.text) or "unexpected")
    result = {}

    def wait_for_approval():
        result["decision"] = await_pending_approval(
            _approval_payload(
                prompt_id=prompt_id,
                session_key=session_key,
                user_id=user_id,
            ),
            session_key=session_key,
            timeout_seconds=1,
            poll_interval=0.01,
        )

    thread = threading.Thread(target=wait_for_approval)
    thread.start()
    try:
        assert _wait_until(lambda: pending_approval_count(session_key) == 1)

        handled = adapter._submit_inbound(
            _event(
                "approve",
                prompt_id=prompt_id,
                session_key=session_key,
                user_id=user_id,
            )
        )

        assert handled == ""
        thread.join(timeout=1)
        assert not thread.is_alive()
    finally:
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    decision = result["decision"]
    assert decision.resolved is True
    assert decision.choice == "approve"
    assert decision.outcome == "approved"
    assert pending_approval_count(session_key) == 0
    assert dispatched == []
    assert adapter.sent == []


def test_inbound_exec_approval_response_resolves_matching_prompt_with_multiple_pending():
    session_key = "stage-z-gateway-queue-multiple"
    first_prompt_id = "exec_approval:multiple-first"
    second_prompt_id = "exec_approval:multiple-second"
    user_id = "user-1"
    clear_pending_approvals(session_key)
    adapter = FakeApprovalAdapter()
    dispatched: list[str] = []
    adapter._init_inbound_queue(lambda ev: dispatched.append(ev.text) or "unexpected")
    result = {}

    def wait_for_approval(name: str, prompt_id: str):
        result[name] = await_pending_approval(
            _approval_payload(
                prompt_id=prompt_id,
                session_key=session_key,
                user_id=user_id,
            ),
            session_key=session_key,
            timeout_seconds=1,
            poll_interval=0.01,
        )

    first = threading.Thread(
        target=wait_for_approval,
        args=("first", first_prompt_id),
    )
    second = threading.Thread(
        target=wait_for_approval,
        args=("second", second_prompt_id),
    )
    second_started = False
    first.start()
    try:
        assert _wait_until(lambda: pending_approval_count(session_key) == 1)
        second.start()
        second_started = True
        assert _wait_until(lambda: pending_approval_count(session_key) == 2)

        handled = adapter._submit_inbound(
            _event(
                "approve",
                prompt_id=second_prompt_id,
                session_key=session_key,
                user_id=user_id,
            )
        )

        assert handled == ""
        second.join(timeout=1)
        assert not second.is_alive()
        assert "second" in result
        assert "first" not in result
        assert pending_approval_count(session_key) == 1

        assert clear_pending_approvals(session_key, choice="deny") == 1
        first.join(timeout=1)
        assert not first.is_alive()
    finally:
        clear_pending_approvals(session_key)
        first.join(timeout=1)
        if second_started:
            second.join(timeout=1)

    second_decision = result["second"]
    assert second_decision.resolved is True
    assert second_decision.choice == "approve"
    assert second_decision.outcome == "approved"
    first_decision = result["first"]
    assert first_decision.resolved is True
    assert first_decision.choice == "deny"
    assert first_decision.outcome == "denied"
    assert pending_approval_count(session_key) == 0
    assert dispatched == []
    assert adapter.sent == []


@pytest.mark.parametrize(
    ("case", "event_prompt_id", "event_session_key", "event_user_id", "rejection"),
    [
        (
            "prompt",
            "exec_approval:stale",
            "stage-z-gateway-queue-reject-prompt",
            "user-1",
            "That prompt is no longer active.",
        ),
        (
            "session",
            "exec_approval:reject-session",
            "other-session",
            "user-1",
            "That prompt belongs to another session.",
        ),
        (
            "user",
            "exec_approval:reject-user",
            "stage-z-gateway-queue-reject-user",
            "other-user",
            "Only the original requester can answer that prompt.",
        ),
    ],
)
def test_inbound_exec_approval_response_rejects_mismatched_pending_approval(
    case: str,
    event_prompt_id: str,
    event_session_key: str,
    event_user_id: str,
    rejection: str,
):
    session_key = f"stage-z-gateway-queue-reject-{case}"
    prompt_id = f"exec_approval:reject-{case}"
    user_id = "user-1"
    clear_pending_approvals(session_key)
    adapter = FakeApprovalAdapter()
    dispatched: list[str] = []
    adapter._init_inbound_queue(lambda ev: dispatched.append(ev.text) or "unexpected")
    result = {}

    def wait_for_approval():
        result["decision"] = await_pending_approval(
            _approval_payload(
                prompt_id=prompt_id,
                session_key=session_key,
                user_id=user_id,
            ),
            session_key=session_key,
            timeout_seconds=1,
            poll_interval=0.01,
        )

    thread = threading.Thread(target=wait_for_approval)
    thread.start()
    try:
        assert _wait_until(lambda: pending_approval_count(session_key) == 1)

        handled = adapter._submit_inbound(
            _event(
                "approve",
                prompt_id=event_prompt_id,
                session_key=event_session_key,
                user_id=event_user_id,
            )
        )

        assert handled == ""
        assert pending_approval_count(session_key) == 1
        assert result == {}
    finally:
        clear_pending_approvals(session_key)
        thread.join(timeout=1)

    assert dispatched == []
    assert [(chat_id, text) for chat_id, text, _metadata in adapter.sent] == [
        ("chat-1", rejection)
    ]


def test_direct_exec_approval_waiter_still_consumes_matching_response():
    session_key = "stage-z-gateway-direct-waiter"
    user_id = "user-1"
    adapter = FakeApprovalAdapter()
    dispatched: list[str] = []
    adapter._init_inbound_queue(lambda ev: dispatched.append(ev.text) or "unexpected")
    answer: dict[str, str] = {}
    prompt_event = MessageEvent(
        platform="fake",
        chat_id="chat-1",
        text="ask",
        user_id=user_id,
        session_key=session_key,
    )

    def ask():
        answer["value"] = adapter.ask_exec_approval(
            prompt_event,
            "Allow bash(ls)?",
            timeout=1,
        )

    thread = threading.Thread(target=ask)
    thread.start()
    try:
        assert _wait_until(lambda: len(adapter.sent) == 1)
        prompt_id = adapter.sent[0][2]["prompt_id"]

        handled = adapter._submit_inbound(
            _event(
                "always",
                prompt_id=prompt_id,
                session_key=session_key,
                user_id=user_id,
            )
        )

        assert handled == ""
        thread.join(timeout=1)
        assert not thread.is_alive()
    finally:
        thread.join(timeout=1)

    assert answer["value"] == "always"
    assert dispatched == []
