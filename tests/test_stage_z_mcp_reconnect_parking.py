"""Stage Z MCP reconnect parking parity contracts.

These tests pin down small Hermes-like lifecycle gaps without pulling in the
full Hermes server-task implementation. They exercise AEGIS at the
MCPClient/MCPTool boundary where production behavior should surface.
"""

from __future__ import annotations

import time
from types import MethodType

import pytest


def _wait_until(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.mark.parametrize(
    "message",
    [
        "ClosedResourceError: transport is closed",
        "connection closed while reading MCP response",
        "Broken pipe while writing request",
        "End of file from MCP server",
    ],
)
def test_transport_closed_markers_trigger_one_reconnect_retry(message: str) -> None:
    """Closed transport variants should use the session-reconnect path.

    Hermes treats stale stdio/HTTP transport failures as reconnect-needed
    signals, not generic tool errors. AEGIS should reconnect once and retry the
    tool call for these markers just as it already does for "session expired".
    """
    from aegis.mcp.client import MCPClient, MCPError, MCPTool

    client = MCPClient("remote")
    calls = {"tool": 0, "reconnect": 0}

    def fake_call_tool(name, arguments, ctx=None):
        assert ctx is None
        calls["tool"] += 1
        if calls["tool"] == 1:
            raise MCPError(message)
        return "after reconnect", False

    def fake_reconnect():
        calls["reconnect"] += 1
        client._state = "connected"
        return client

    client.call_tool = fake_call_tool
    client.reconnect = fake_reconnect
    tool = MCPTool(client, {"name": "search", "inputSchema": {"type": "object"}})

    result = tool.run({}, ctx=None)

    assert result.is_error is False
    assert result.content == "after reconnect"
    assert calls == {"tool": 2, "reconnect": 1}


def test_resource_tool_reconnects_once_on_session_expiry() -> None:
    """Resource utility calls should share the tools/call reconnect behavior."""
    from aegis.mcp.client import MCPClient, MCPError, MCPReadResourceTool

    client = MCPClient("remote")
    calls = {"resource": 0, "reconnect": 0}

    def fake_read_resource(uri):
        calls["resource"] += 1
        if calls["resource"] == 1:
            raise MCPError("invalid or expired session")
        return "resource after reconnect"

    def fake_reconnect():
        calls["reconnect"] += 1
        return client

    client.read_resource = fake_read_resource
    client.reconnect = fake_reconnect
    tool = MCPReadResourceTool(client, [{"uri": "file://demo.txt"}])

    result = tool.run({"uri": "file://demo.txt"}, ctx=None)

    assert result.is_error is False
    assert result.content == "resource after reconnect"
    assert calls == {"resource": 2, "reconnect": 1}


def test_prompt_tool_reconnects_once_on_session_expiry() -> None:
    """Prompt utility calls should share the tools/call reconnect behavior."""
    from aegis.mcp.client import MCPClient, MCPError, MCPGetPromptTool

    client = MCPClient("remote")
    calls = {"prompt": 0, "reconnect": 0}

    def fake_get_prompt(name, arguments=None):
        calls["prompt"] += 1
        if calls["prompt"] == 1:
            raise MCPError("transport is closed")
        return "prompt after reconnect"

    def fake_reconnect():
        calls["reconnect"] += 1
        return client

    client.get_prompt = fake_get_prompt
    client.reconnect = fake_reconnect
    tool = MCPGetPromptTool(client, [{"name": "demo"}])

    result = tool.run({"name": "demo"}, ctx=None)

    assert result.is_error is False
    assert result.content == "prompt after reconnect"
    assert calls == {"prompt": 2, "reconnect": 1}


def test_keepalive_reconnect_needed_parks_tool_calls_until_reconnect() -> None:
    """A background liveness failure should park model-facing calls.

    Once keepalive marks a client ``reconnect_needed``, a tool invocation must
    not write into the known-dead transport or run a synchronous reconnect loop.
    It should return a clean reconnect/backoff error so the model pauses while
    the lifecycle owner rebuilds the connection.
    """
    from aegis.mcp.client import MCPClient, MCPError, MCPTool

    client = MCPClient("parked")

    def fake_request(self, method, params=None, notify=False):
        raise MCPError("connection closed")

    client._request = MethodType(fake_request, client)
    row = client.keepalive()
    assert row["state"] == "reconnect_needed"

    calls = {"tool": 0, "reconnect": 0}

    def dead_call_tool(name, arguments):
        calls["tool"] += 1
        raise AssertionError("parked MCP tool touched a dead transport")

    def direct_reconnect():
        calls["reconnect"] += 1
        raise AssertionError("parked MCP tool ran a synchronous reconnect")

    client.call_tool = dead_call_tool
    client.reconnect = direct_reconnect
    tool = MCPTool(client, {"name": "probe", "inputSchema": {"type": "object"}})

    result = tool.run({}, ctx=None)

    assert result.is_error is True
    content = result.content.lower()
    assert "reconnect" in content
    assert any(word in content for word in ("backoff", "wait", "do not retry"))
    assert calls == {"tool": 0, "reconnect": 0}
    assert client.state == "reconnect_needed"


def test_circuit_breaker_short_circuits_after_consecutive_tool_errors() -> None:
    """Repeated MCP tool errors should park before burning agent iterations."""
    from aegis.mcp import client as mcp_client
    from aegis.mcp.client import MCPClient, MCPTool

    client = MCPClient("flaky")
    calls = {"request": 0}

    def fake_request(self, method, params=None, notify=False):
        calls["request"] += 1
        assert method == "tools/call"
        return {
            "result": {
                "content": [{"type": "text", "text": "still broken"}],
                "isError": True,
            }
        }

    client._request = MethodType(fake_request, client)
    tool = MCPTool(client, {"name": "probe", "inputSchema": {"type": "object"}})

    for _ in range(mcp_client._MCP_CIRCUIT_BREAKER_THRESHOLD):
        result = tool.run({}, ctx=None)
        assert result.is_error is True
        assert result.content == "still broken"

    parked = tool.run({}, ctx=None)

    assert parked.is_error is True
    content = parked.content.lower()
    assert "unreachable" in content
    assert "do not retry" in content
    assert calls["request"] == mcp_client._MCP_CIRCUIT_BREAKER_THRESHOLD
    assert client.breaker_state == "open"
    assert client.state == "parked"


def test_circuit_breaker_half_open_success_resets_after_cooldown() -> None:
    """After cooldown, a successful probe should close the breaker."""
    import time

    from aegis.mcp import client as mcp_client
    from aegis.mcp.client import MCPClient, MCPTool

    client = MCPClient("recovering", url="https://example.test/mcp")
    client._initialized = True
    client._state = "parked"
    client._failure_count = mcp_client._MCP_CIRCUIT_BREAKER_THRESHOLD
    client._breaker_opened_at = (
        time.monotonic() - mcp_client._MCP_CIRCUIT_BREAKER_COOLDOWN_SEC - 1
    )
    calls = {"request": 0}

    def fake_request(self, method, params=None, notify=False):
        calls["request"] += 1
        assert method == "tools/call"
        return {"result": {"content": [{"type": "text", "text": "ok"}]}}

    client._request = MethodType(fake_request, client)
    tool = MCPTool(client, {"name": "probe", "inputSchema": {"type": "object"}})

    result = tool.run({}, ctx=None)

    assert result.is_error is False
    assert result.content == "ok"
    assert calls["request"] == 1
    assert client.breaker_state == "closed"
    assert client.failure_count == 0
    assert client.state == "connected"


def test_circuit_breaker_half_open_dead_transport_requests_reconnect() -> None:
    """Half-open against an absent transport should signal reconnect cleanly."""
    import time

    from aegis.mcp import client as mcp_client
    from aegis.mcp.client import MCPClient, MCPTool

    client = MCPClient("dead")
    client._initialized = True
    client._state = "parked"
    client._failure_count = mcp_client._MCP_CIRCUIT_BREAKER_THRESHOLD
    client._breaker_opened_at = (
        time.monotonic() - mcp_client._MCP_CIRCUIT_BREAKER_COOLDOWN_SEC - 1
    )
    calls = {"request": 0}

    def fake_request(self, method, params=None, notify=False):
        calls["request"] += 1
        raise AssertionError("half-open dead transport should not be called")

    client._request = MethodType(fake_request, client)
    tool = MCPTool(client, {"name": "probe", "inputSchema": {"type": "object"}})

    result = tool.run({}, ctx=None)

    assert result.is_error is True
    content = result.content.lower()
    assert "reconnect" in content
    assert "do not retry" in content
    assert calls["request"] == 0
    assert client.state == "reconnect_needed"
    assert client.reconnect_requested is True


def test_reconnect_signal_is_consumed_by_lifecycle_worker() -> None:
    """A reconnect signal should be handled by the long-lived lifecycle owner."""
    from aegis.mcp.client import MCPClient

    client = MCPClient("live")
    calls = {"connect": 0}

    def fake_connect():
        calls["connect"] += 1
        client._initialized = True
        client._state = "connected"
        client._record_remote_success()
        return client

    client.connect = fake_connect
    client._ensure_lifecycle_worker()

    try:
        client.request_reconnect("session expired")

        assert _wait_until(lambda: calls["connect"] == 1 and client.state == "connected")
        assert client.reconnect_requested is False
    finally:
        client.close()


def test_lifecycle_worker_parks_after_budget_then_revives(monkeypatch) -> None:
    """Reconnect exhaustion parks, but a later signal revives the same worker."""
    from aegis.mcp import client as mcp_client
    from aegis.mcp.client import MCPClient, MCPError

    monkeypatch.setattr(mcp_client, "_MCP_RECONNECT_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(mcp_client, "_MCP_RECONNECT_INITIAL_BACKOFF_SEC", 0.01)
    monkeypatch.setattr(mcp_client, "_MCP_RECONNECT_MAX_BACKOFF_SEC", 0.01)

    client = MCPClient("flaky")
    attempts = {"connect": 0}
    allow_success = {"value": False}

    def fake_connect():
        attempts["connect"] += 1
        if not allow_success["value"]:
            raise MCPError("subprocess died")
        client._initialized = True
        client._state = "connected"
        client._record_remote_success()
        return client

    client.connect = fake_connect
    client._ensure_lifecycle_worker()

    try:
        client.request_reconnect("transport down")

        assert _wait_until(lambda: client.state == "parked")
        first_budget = attempts["connect"]
        assert first_budget == 2
        assert client.reconnect_requested is True

        allow_success["value"] = True
        client.request_reconnect("manual refresh")

        assert _wait_until(lambda: client.state == "connected")
        assert attempts["connect"] == first_budget + 1
        assert client.reconnect_requested is False
    finally:
        client.close()
