"""Stage Z discovery tests for the Codex app-server provider lane.

These tests pin the Hermes parity gap for Codex dynamic inline tools without
touching production code. They intentionally describe the desired behavior:
inline tool work should be queued off the provider pump, and completed
``dynamicToolCall`` items should be projected into provider-visible history.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from aegis.providers.base import ApiMode
from aegis.providers.codex_app_server import CodexAppServerTransport
from aegis.tools.base import ToolResult
from aegis.types import LLMResponse, Message


@pytest.fixture(autouse=True)
def isolated_runtime_homes(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "aegis-home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))


class _Auth:
    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return "test auth"


class _RecordingClient:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages = list(messages or [])
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[Any, dict[str, Any] | None]] = []
        self.errors: list[tuple[Any, int, str]] = []

    def request(self, method, params, timeout=20, server_request_handler=None):
        self.requests.append((method, params))
        return {"turn": {"id": "turn_1"}}

    def take_message(self, timeout=0.25):
        return self._messages.pop(0) if self._messages else None

    def is_alive(self):
        return True

    def stderr_tail(self):
        return ""

    def respond(self, request_id, result=None):
        self.responses.append((request_id, result))

    def respond_error(self, request_id, message, code=-32603):
        self.errors.append((request_id, code, message))


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_dynamic_tool_server_request_is_queued_off_provider_pump():
    """A long-running inline tool must not block the Codex message pump.

    Hermes keeps app-server reading/projection responsive while tool-shaped
    work is handled through its queue/session adapter. AEGIS currently runs
    the dynamic tool synchronously inside ``_handle_server_request``.
    """
    client = _RecordingClient()
    transport = CodexAppServerTransport()
    transport._client = client

    handler_done = threading.Event()
    release_tool = threading.Event()

    def tool_runner(_call):
        release_tool.wait(timeout=1.0)
        return ToolResult.ok("queued output")

    def invoke_handler():
        transport._handle_server_request(
            {
                "id": "req_1",
                "method": "item/tool/call",
                "params": {
                    "callId": "call_status",
                    "tool": "system_status",
                    "arguments": {"verbose": True},
                },
            },
            tool_runner=tool_runner,
            approver=None,
        )
        handler_done.set()

    thread = threading.Thread(target=invoke_handler)
    thread.start()
    try:
        returned_before_tool_finished = handler_done.wait(timeout=0.05)
    finally:
        release_tool.set()

    assert handler_done.wait(timeout=1.0)
    thread.join(timeout=1.0)
    assert _wait_for(lambda: bool(client.responses))
    assert client.responses == [
        (
            "req_1",
            {
                "contentItems": [{"type": "inputText", "text": "queued output"}],
                "success": True,
            },
        )
    ]
    assert returned_before_tool_finished, (
        "dynamic inline tools should be queued so the provider pump can keep "
        "draining Codex notifications while the worker runs"
    )


def test_completed_dynamic_tool_call_projects_hermes_style_history():
    """Completed dynamic tools should survive the turn as projected history.

    Hermes projects one completed ``dynamicToolCall`` into an assistant tool
    call plus a correlated tool result, without re-exposing it as a fresh
    ``LLMResponse.tool_calls`` item for the outer loop to execute again.
    """
    client = _RecordingClient(
        [
            {
                "id": "req_1",
                "method": "item/tool/call",
                "params": {
                    "callId": "call_status",
                    "tool": "system_status",
                    "arguments": {"verbose": True},
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "dynamicToolCall",
                        "id": "dyn_1",
                        "tool": "system_status",
                        "arguments": {"verbose": True},
                        "status": "completed",
                        "contentItems": [{"type": "inputText", "text": "tool output"}],
                        "success": True,
                    }
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "item": {"type": "agentMessage", "id": "msg_1", "text": "done"}
                },
            },
            {
                "method": "turn/completed",
                "params": {"turn": {"id": "turn_1", "status": "completed"}},
            },
        ]
    )
    transport = CodexAppServerTransport()
    transport._thread_id = "thread_1"
    transport._client = client
    transport._ensure_thread = lambda **_kwargs: client

    def tool_runner(call):
        assert call.id == "call_status"
        assert call.name == "system_status"
        assert call.arguments == {"verbose": True}
        return ToolResult.ok("tool output")

    response = transport.complete(
        base_url="codex://app-server",
        auth=_Auth(),
        model="gpt-5.5",
        messages=[Message.user("check status")],
        tools=[
            {
                "name": "system_status",
                "description": "Inspect install state",
                "parameters": {
                    "type": "object",
                    "properties": {"verbose": {"type": "boolean"}},
                },
            }
        ],
        stream=True,
        tool_runner=tool_runner,
        cwd=Path.cwd(),
    )

    assert response.text == "done"
    assert response.tool_calls == []
    assert client.responses == [
        (
            "req_1",
            {
                "contentItems": [{"type": "inputText", "text": "tool output"}],
                "success": True,
            },
        )
    ]

    projected = response.raw.get("projected_messages") if isinstance(response.raw, dict) else None
    assert projected and len(projected) >= 2
    assistant, tool = projected[:2]
    assert assistant["role"] == "assistant"
    tool_call = assistant["tool_calls"][0]
    assert tool_call["name"] == "system_status"
    assert tool_call["arguments"] == {"verbose": True}
    assert tool["role"] == "tool"
    assert tool["tool_call_id"] == tool_call["id"]
    assert "tool output" in tool["content"]


class _ProjectedCodexProvider:
    name = "codex-app-server"
    model = "gpt-5.5"
    context_length = 272_000
    api_mode = ApiMode.CODEX_APP_SERVER
    auth = _Auth()

    def complete(self, messages, tools=None, **_kwargs):
        return LLMResponse(
            text="done",
            finish_reason="completed",
            raw={
                "turn_id": "turn_1",
                "projected_messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "codex_dyn_system_status_dyn_1",
                                "name": "system_status",
                                "arguments": {"verbose": True},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "content": "tool output",
                        "tool_call_id": "codex_dyn_system_status_dyn_1",
                        "name": "system_status",
                    },
                    {"role": "assistant", "content": "done"},
                ],
                "tool_iterations": 1,
            },
        )

    def describe(self):
        return "codex-app-server/gpt-5.5"


def test_agent_splices_codex_projected_messages_into_session_history(tmp_path):
    """The provider projector is not enough if the agent loop drops its output."""
    from aegis.agent.agent import Agent
    from aegis.config import Config, DEFAULT_CONFIG
    from aegis.session import Session
    import copy

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["agent"]["max_iterations"] = 1
    cfg.data["agent"]["stream"] = False
    cfg.data["tools"]["toolsets"] = ["core"]
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}

    agent = Agent(
        config=cfg,
        provider=_ProjectedCodexProvider(),
        session=Session.create(),
        cwd=tmp_path,
    )
    result = agent.run("check status")

    assert result.content == "done"
    assistant_tools = [m for m in agent.session.messages if m.role == "assistant" and m.tool_calls]
    tool_results = [m for m in agent.session.messages if m.role == "tool"]
    assert assistant_tools, (
        "codex-app-server raw.projected_messages should be spliced into session "
        "history so inline dynamic tool calls survive memory/replay"
    )
    assert assistant_tools[0].tool_calls[0].name == "system_status"
    assert assistant_tools[0].tool_calls[0].arguments == {"verbose": True}
    assert tool_results and tool_results[0].tool_call_id == assistant_tools[0].tool_calls[0].id
    assert tool_results[0].content == "tool output"
    assert agent.session.messages[-1].role == "assistant"
    assert agent.session.messages[-1].content == "done"
