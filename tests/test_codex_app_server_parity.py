"""Focused Hermes parity for the Codex app-server provider transport."""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from aegis.providers.codex_app_server import CodexAppServerError, CodexAppServerTransport
from aegis.tools.base import ToolResult
from aegis.types import Message


class _Auth:
    def available(self) -> bool:
        return True


class FakeClient:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages = deque(messages or [])
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[Any, dict[str, Any] | None]] = []
        self.errors: list[tuple[Any, int, str]] = []
        self.closed = False

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        server_request_handler=None,
    ) -> dict[str, Any]:
        self.requests.append((method, params or {}))
        if method == "turn/start":
            return {"turn": {"id": "turn_1"}}
        if method == "turn/interrupt":
            return {}
        return {}

    def take_message(self, timeout: float = 0.0) -> dict[str, Any] | None:
        return self._messages.popleft() if self._messages else None

    def is_alive(self) -> bool:
        return not self.closed

    def stderr_tail(self) -> str:
        return ""

    def respond(self, request_id: Any, result: dict[str, Any] | None = None) -> None:
        self.responses.append((request_id, result))

    def respond_error(self, request_id: Any, message: str, code: int = -32603) -> None:
        self.errors.append((request_id, code, message))

    def close(self) -> None:
        self.closed = True


def _transport(client: FakeClient) -> CodexAppServerTransport:
    transport = CodexAppServerTransport()
    transport._thread_id = "thread_1"
    transport._client = client
    transport._ensure_thread = lambda **_kw: client  # type: ignore[method-assign]
    return transport


def _complete(transport: CodexAppServerTransport):
    return transport.complete(
        base_url="codex://app-server",
        auth=_Auth(),
        model="gpt-5.5",
        messages=[Message.user("hello")],
        tools=None,
        stream=True,
        timeout=1.0,
    )


def test_codex_app_server_projects_tool_items_into_raw_message_history() -> None:
    client = FakeClient([
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "commandExecution",
                    "id": "exec-1",
                    "command": "false",
                    "cwd": "/tmp",
                    "aggregatedOutput": "nope",
                    "exitCode": 2,
                }
            },
        },
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "dynamicToolCall",
                    "id": "dyn-1",
                    "tool": "system_status",
                    "arguments": {"verbose": True},
                    "contentItems": [{"type": "inputText", "text": "ok"}],
                    "success": True,
                }
            },
        },
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "id": "m1", "text": "done"}},
        },
        {"method": "turn/completed", "params": {"turn": {"id": "turn_1", "status": "completed"}}},
    ])

    response = _complete(_transport(client))

    assert response.text == "done"
    assert response.raw["tool_iterations"] == 2
    names = [
        call["name"]
        for message in response.raw["projected_messages"]
        for call in message.get("tool_calls", [])
    ]
    assert names == ["exec_command", "system_status"]
    tool_contents = [
        message["content"]
        for message in response.raw["projected_messages"]
        if message["role"] == "tool"
    ]
    assert any(content.startswith("[exit 2]") for content in tool_contents)


def test_codex_app_server_handles_hermes_style_server_requests() -> None:
    client = FakeClient()
    transport = _transport(client)

    transport._handle_server_request(
        {"id": "elic-1", "method": "mcpServer/elicitation/request", "params": {"serverName": "hermes-tools"}},
        tool_runner=None,
        approver=None,
    )
    transport._handle_server_request(
        {"id": "elic-2", "method": "mcpServer/elicitation/request", "params": {"serverName": "third-party"}},
        tool_runner=None,
        approver=None,
    )
    transport._handle_server_request(
        {"id": "perm-1", "method": "item/permissions/requestApproval", "params": {}},
        tool_runner=None,
        approver=None,
    )
    transport._handle_server_request(
        {"id": "nope-1", "method": "totally/unknown", "params": {}},
        tool_runner=None,
        approver=None,
    )

    assert ("elic-1", {"action": "accept", "content": None, "_meta": None}) in client.responses
    assert ("elic-2", {"action": "decline", "content": None, "_meta": None}) in client.responses
    assert ("perm-1", {"decision": "decline"}) in client.responses
    assert any(error[0] == "nope-1" and error[1] == -32601 for error in client.errors)


def test_codex_app_server_file_change_approval_uses_cached_started_summary() -> None:
    client = FakeClient()
    transport = _transport(client)
    transport._track_pending_file_change(
        {
            "method": "item/started",
            "params": {
                "item": {
                    "type": "fileChange",
                    "id": "fc-1",
                    "changes": [
                        {"kind": {"type": "add"}, "path": "/tmp/new.py"},
                        {"kind": {"type": "update"}, "path": "/tmp/old.py"},
                    ],
                }
            },
        }
    )
    prompts: list[str] = []

    def approve(prompt: str):
        prompts.append(prompt)
        return "session"

    transport._handle_server_request(
        {
            "id": "patch-1",
            "method": "item/fileChange/requestApproval",
            "params": {"itemId": "fc-1", "reason": "apply files"},
        },
        tool_runner=None,
        approver=approve,
    )

    assert ("patch-1", {"decision": "acceptForSession"}) in client.responses
    assert "1 add" in prompts[0]
    assert "1 update" in prompts[0]
    assert "/tmp/new.py" in prompts[0]


def test_codex_app_server_turn_aborted_marker_is_terminal() -> None:
    client = FakeClient([
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "agentMessage",
                    "id": "m1",
                    "text": "partial <turn_aborted/>",
                }
            },
        }
    ])

    response = _complete(_transport(client))

    assert response.finish_reason == "interrupted"
    assert response.raw["interrupted"] is True
    assert response.raw["errors"] == ["codex reported turn_aborted"]


def test_codex_app_server_post_tool_quiet_watchdog_interrupts_and_retires() -> None:
    client = FakeClient([
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "commandExecution",
                    "id": "exec-1",
                    "command": "echo hi",
                    "cwd": "/tmp",
                    "aggregatedOutput": "hi",
                    "exitCode": 0,
                }
            },
        }
    ])
    transport = _transport(client)
    transport.post_tool_quiet_timeout = 0.01

    with pytest.raises(CodexAppServerError, match="went silent"):
        transport.complete(
            base_url="codex://app-server",
            auth=_Auth(),
            model="gpt-5.5",
            messages=[Message.user("hello")],
            tools=None,
            stream=True,
            timeout=0.2,
        )

    assert any(method == "turn/interrupt" for method, _params in client.requests)
    assert transport._client is None


def test_codex_app_server_dynamic_tool_request_maps_runner_result() -> None:
    client = FakeClient()
    transport = _transport(client)
    seen = []

    def run(call):
        seen.append(call)
        return ToolResult.ok("tool output")

    transport._handle_server_request(
        {
            "id": 7,
            "method": "item/tool/call",
            "params": {"callId": "call_1", "tool": "system_status", "arguments": {"verbose": True}},
        },
        tool_runner=run,
        approver=None,
    )

    assert seen[0].name == "system_status"
    assert client.responses[-1] == (
        7,
        {"contentItems": [{"type": "inputText", "text": "tool output"}], "success": True},
    )
