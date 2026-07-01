"""Stage Z MCP live lifecycle contracts."""

from __future__ import annotations

import copy
import io
import json
import os
import time
from types import MethodType


def test_list_changed_notification_refreshes_registered_tools() -> None:
    from aegis.mcp.client import MCPClient, MCPManager
    from aegis.tools.registry import ToolRegistry

    client = MCPClient("live")
    tool_lists = [
        [{"name": "old_tool", "description": "old", "inputSchema": {"type": "object"}}],
        [{"name": "new_tool", "description": "new", "inputSchema": {"type": "object"}}],
    ]

    def fake_request(self, method, params=None, notify=False):
        if method == "tools/list":
            return {"result": {"tools": tool_lists.pop(0)}}
        raise AssertionError(method)

    client.connect = lambda: client
    client._request = MethodType(fake_request, client)
    manager = MCPManager()
    manager.add(client)
    registry = ToolRegistry()

    initial = manager.connect_all()
    registry.register_all(initial)
    assert registry.get("mcp__live__old_tool") is not None
    assert registry.get("mcp__live__old_tool").toolset == "mcp-live"
    assert registry.toolset_names(display_aliases=False) == ["core", "mcp-live"]
    assert registry.toolset_names() == ["core", "live"]
    assert [tool.name for tool in registry.available(["mcp"])] == ["mcp__live__old_tool"]

    client._handle_notification({"method": "notifications/tools/list_changed"})
    refreshed = manager.refresh_changed_tools(registry)

    assert [tool.name for tool in refreshed] == ["mcp__live__new_tool"]
    assert registry.get("mcp__live__old_tool") is None
    assert registry.get("mcp__live__new_tool") is not None
    assert client.tools_stale is False


def test_list_changed_replaces_same_name_tool_schema() -> None:
    from aegis.mcp.client import MCPClient, MCPManager
    from aegis.tools.registry import ToolRegistry

    client = MCPClient("live")
    tool_lists = [
        [{
            "name": "probe",
            "description": "old behavior",
            "inputSchema": {
                "type": "object",
                "properties": {"old_arg": {"type": "string"}},
            },
        }],
        [{
            "name": "probe",
            "description": "new behavior",
            "inputSchema": {
                "type": "object",
                "properties": {"new_arg": {"type": "string"}},
                "required": ["new_arg"],
            },
        }],
    ]

    def fake_request(self, method, params=None, notify=False):
        if method == "tools/list":
            return {"result": {"tools": tool_lists.pop(0)}}
        raise AssertionError(method)

    client.connect = lambda: client
    client._request = MethodType(fake_request, client)
    manager = MCPManager()
    manager.add(client)
    registry = ToolRegistry()
    registry.register_all(manager.connect_all())

    client._handle_notification({"method": "notifications/tools/list_changed"})
    refreshed = manager.refresh_changed_tools(registry)

    assert [tool.name for tool in refreshed] == ["mcp__live__probe"]
    schema = registry.get("mcp__live__probe").schema()
    assert schema["description"] == "new behavior"
    assert "new_arg" in schema["parameters"]["properties"]
    assert "old_arg" not in schema["parameters"]["properties"]


def test_manager_tracks_mcp_utility_tools_in_server_toolset() -> None:
    from aegis.mcp.client import MCPClient, MCPManager
    from aegis.tools.registry import ToolRegistry

    client = MCPClient("utility")
    tool_lists = [
        [{"name": "search", "description": "Search", "inputSchema": {"type": "object"}}],
        [{"name": "lookup", "description": "Lookup", "inputSchema": {"type": "object"}}],
    ]

    def list_tools(*, force=False):
        return tool_lists.pop(0)

    client.connect = lambda: client
    client.list_tools = list_tools
    client.list_resources = lambda: [{"uri": "note://a", "name": "Note A"}]
    client.list_prompts = lambda: [{"name": "review", "description": "Review"}]
    manager = MCPManager()
    manager.add(client)
    registry = ToolRegistry()

    tools = manager.connect_all()
    registry.register_all(tools)

    assert [tool.name for tool in tools] == [
        "mcp__utility__search",
        "mcp__utility__read_resource",
        "mcp__utility__get_prompt",
    ]
    assert {tool.toolset for tool in tools} == {"mcp-utility"}
    assert manager._registered_tool_names["utility"] == [tool.name for tool in tools]

    client._handle_notification({"method": "notifications/tools/list_changed"})
    refreshed = manager.refresh_changed_tools(registry)

    assert [tool.name for tool in refreshed] == [
        "mcp__utility__lookup",
        "mcp__utility__read_resource",
        "mcp__utility__get_prompt",
    ]
    assert registry.get("mcp__utility__search") is None
    assert registry.get("mcp__utility__read_resource") is not None
    assert registry.get("mcp__utility__get_prompt") is not None


def test_manager_tracks_exact_mcp_tool_provenance_for_parallel_safety() -> None:
    from aegis.mcp.client import MCPClient, MCPManager

    serial = MCPClient("alpha", supports_parallel_tool_calls=False)
    parallel = MCPClient("alpha__beta", supports_parallel_tool_calls=True)
    tool_lists = [
        [{"name": "probe", "description": "Probe", "inputSchema": {"type": "object"}}],
        [{"name": "fresh", "description": "Fresh", "inputSchema": {"type": "object"}}],
    ]

    def connect(client):
        client.initialize_result = {"capabilities": {"tools": {}}}
        return client

    def serial_tools(*, apply_filter=True, force=False):
        return [{"name": "noop", "description": "Noop", "inputSchema": {"type": "object"}}]

    def parallel_tools(*, apply_filter=True, force=False):
        return tool_lists.pop(0)

    serial.connect = lambda: connect(serial)
    serial.list_tools = serial_tools
    parallel.connect = lambda: connect(parallel)
    parallel.list_tools = parallel_tools
    manager = MCPManager()
    manager.add(serial)
    manager.add(parallel)

    tools = manager.connect_all()

    assert [tool.name for tool in tools] == [
        "mcp__alpha__noop",
        "mcp__alpha__beta__probe",
    ]
    assert manager.mcp_tool_server_name("mcp__alpha__beta__probe") == "alpha__beta"
    assert manager.mcp_tool_provenance("mcp__alpha__beta__probe") == {
        "server": "alpha__beta",
        "parallel_safe": True,
    }
    assert manager.is_mcp_tool_parallel_safe("mcp__alpha__beta__probe") is True
    assert manager.is_mcp_tool_parallel_safe("mcp__alpha__noop") is False
    assert manager.is_mcp_tool_parallel_safe("read_file") is False

    parallel._handle_notification({"method": "notifications/tools/list_changed"})
    refreshed = manager.refresh_changed_tools()

    assert [tool.name for tool in refreshed] == ["mcp__alpha__beta__fresh"]
    assert manager.mcp_tool_server_name("mcp__alpha__beta__probe") is None
    assert manager.mcp_tool_server_name("mcp__alpha__beta__fresh") == "alpha__beta"
    assert manager.is_mcp_tool_parallel_safe("mcp__alpha__beta__fresh") is True


def test_non_sdk_connect_captures_initialize_capabilities() -> None:
    from aegis.mcp.client import MCPClient

    client = MCPClient("caps", command="node")
    calls: list[str] = []

    def fake_request(self, method, params=None, notify=False):
        calls.append(method)
        if method == "initialize":
            return {
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}, "resources": {}},
                }
            }
        if method == "notifications/initialized":
            return None
        raise AssertionError(method)

    client._spawn = lambda: None
    client._request = MethodType(fake_request, client)
    client._mark_transport_ready = lambda generation: None
    client._ensure_lifecycle_worker = lambda: None

    client.connect()

    assert calls == ["initialize", "notifications/initialized"]
    assert client.initialize_result == {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {}, "resources": {}},
    }


def test_manager_skips_resource_prompt_utilities_when_capabilities_absent() -> None:
    from aegis.mcp.client import MCPClient, MCPManager

    client = MCPClient("tools-only")
    client.initialize_result = {"capabilities": {"tools": {}}}
    client.connect = lambda: client
    client.list_tools = lambda: [{"name": "search", "description": "Search", "inputSchema": {"type": "object"}}]

    def unsupported_capability():
        raise AssertionError("capability was probed despite initialize_result omission")

    client.list_resources = unsupported_capability
    client.list_prompts = unsupported_capability
    manager = MCPManager()
    manager.add(client)

    tools = manager.connect_all()

    assert [tool.name for tool in tools] == ["mcp__tools_only__search"]
    assert manager._registered_tool_names["tools-only"] == ["mcp__tools_only__search"]


def test_manager_keeps_resource_prompt_utilities_when_capabilities_advertised() -> None:
    from aegis.mcp.client import MCPClient, MCPManager

    client = MCPClient("all-caps")
    client.initialize_result = {"capabilities": {"tools": {}, "resources": {}, "prompts": {}}}
    client.connect = lambda: client
    client.list_tools = lambda: [{"name": "search", "description": "Search", "inputSchema": {"type": "object"}}]
    client.list_resources = lambda: [{"uri": "note://a", "name": "Note A"}]
    client.list_prompts = lambda: [{"name": "review", "description": "Review"}]
    manager = MCPManager()
    manager.add(client)

    tools = manager.connect_all()

    assert [tool.name for tool in tools] == [
        "mcp__all_caps__search",
        "mcp__all_caps__read_resource",
        "mcp__all_caps__get_prompt",
    ]


def test_stdio_background_reader_marks_tools_stale_between_requests() -> None:
    """Stdio notifications should be observed even while no RPC is waiting."""
    from aegis.mcp.client import MCPClient

    read_fd, write_fd = os.pipe()
    stdout = os.fdopen(read_fd, "r", encoding="utf-8", buffering=1)
    writer = os.fdopen(write_fd, "w", encoding="utf-8", buffering=1)

    class FakeProc:
        def poll(self):
            return None

        def terminate(self):
            try:
                writer.close()
            except OSError:
                pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminate()

    client = MCPClient("live")
    proc = FakeProc()
    proc.stdin = io.StringIO()
    proc.stdout = stdout
    client._proc = proc
    client._start_stdio_reader()

    try:
        writer.write(json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
        }) + "\n")
        writer.flush()

        deadline = time.monotonic() + 2.0
        while not client.tools_stale and time.monotonic() < deadline:
            time.sleep(0.01)

        assert client.tools_stale is True
    finally:
        client.close()


def test_mcp_tool_reconnects_once_on_session_expiry() -> None:
    from aegis.mcp.client import MCPClient, MCPTool, MCPError

    client = MCPClient("remote")
    calls = {"tool": 0, "reconnect": 0}

    def fake_call_tool(name, arguments, ctx=None):
        assert ctx is None
        calls["tool"] += 1
        if calls["tool"] == 1:
            raise MCPError("session expired")
        return "after reconnect", False

    def fake_reconnect():
        calls["reconnect"] += 1
        return client

    client.call_tool = fake_call_tool
    client.reconnect = fake_reconnect
    tool = MCPTool(client, {"name": "search", "inputSchema": {"type": "object"}})

    result = tool.run({}, ctx=None)

    assert result.is_error is False
    assert result.content == "after reconnect"
    assert calls == {"tool": 2, "reconnect": 1}


def test_failed_tool_call_marks_error_state_without_crashing() -> None:
    from aegis.mcp.client import MCPClient, MCPError, MCPTool

    client = MCPClient("live")

    def fake_stdio_request(self, payload, timeout=30.0):
        assert payload["method"] == "tools/call"
        raise MCPError("server closed the connection")

    client._stdio_request = MethodType(fake_stdio_request, client)
    tool = MCPTool(client, {"name": "probe", "inputSchema": {"type": "object"}})

    result = tool.run({}, ctx=None)

    assert result.is_error is True
    assert "mcp call failed after session reconnect" in result.content
    assert client.state == "reconnect_needed"
    assert "no command or url configured" in client.last_error


def test_keepalive_prefers_ping_and_latches_tools_list_fallback() -> None:
    from aegis.mcp.client import MCPClient, MCPError

    client = MCPClient("live")
    calls: list[str] = []

    def fake_request(self, method, params=None, notify=False):
        calls.append(method)
        if method == "ping":
            raise MCPError("JSON-RPC -32601: Method not found")
        if method == "tools/list":
            return {"result": {"tools": [{"name": "probe", "inputSchema": {"type": "object"}}]}}
        raise AssertionError(method)

    client._request = MethodType(fake_request, client)

    first = client.keepalive()
    second = client.keepalive()

    assert first["ok"] is True
    assert first["method"] == "tools/list"
    assert second["ok"] is True
    assert second["method"] == "tools/list"
    assert calls == ["ping", "tools/list", "tools/list"]


def test_keepalive_latches_tools_list_fallback_on_unknown_method_text() -> None:
    from aegis.mcp.client import MCPClient, MCPError

    client = MCPClient("live")
    calls: list[str] = []

    def fake_request(self, method, params=None, notify=False):
        calls.append(method)
        if method == "ping":
            raise MCPError("Unknown method: ping")
        if method == "tools/list":
            return {"result": {"tools": []}}
        raise AssertionError(method)

    client._request = MethodType(fake_request, client)

    row = client.keepalive()

    assert row["ok"] is True
    assert row["method"] == "tools/list"
    assert calls == ["ping", "tools/list"]


def test_keepalive_failure_marks_reconnect_needed() -> None:
    from aegis.mcp.client import MCPClient, MCPError

    client = MCPClient("dead")

    def fake_request(self, method, params=None, notify=False):
        raise MCPError("connection closed")

    client._request = MethodType(fake_request, client)

    row = client.keepalive()

    assert row["ok"] is False
    assert row["state"] == "reconnect_needed"
    assert client.state == "reconnect_needed"
    assert "connection closed" in client.last_error


def test_manager_keepalive_reports_all_servers_without_raising() -> None:
    from aegis.mcp.client import MCPClient, MCPManager

    ok = MCPClient("ok")
    bad = MCPClient("bad")
    ok.keepalive = lambda: {"server": "ok", "ok": True, "method": "ping", "state": "connected"}

    def boom():
        raise RuntimeError("socket gone")

    bad.keepalive = boom
    manager = MCPManager()
    manager.add(ok)
    manager.add(bad)

    rows = manager.keepalive_all()

    assert rows[0] == {"server": "ok", "ok": True, "method": "ping", "state": "connected"}
    assert rows[1]["server"] == "bad"
    assert rows[1]["ok"] is False
    assert rows[1]["state"] == "reconnect_needed"
    assert "socket gone" in rows[1]["error"]


def test_lifecycle_metadata_tracks_transport_owner_generation() -> None:
    from aegis.mcp.client import MCPClient

    client = MCPClient("meta")

    generation = client._begin_transport_owner("stdio")
    client._mark_transport_ready(generation)

    metadata = client.lifecycle_metadata
    assert metadata["server"] == "meta"
    assert metadata["generation"] == generation
    assert metadata["owner"]["generation"] == generation
    assert metadata["owner"]["transport"] == "stdio"
    assert metadata["owner"]["state"] == "ready"

    client._close_transport(mark_disconnected=True)
    closed = client.lifecycle_metadata

    assert closed["generation"] > generation
    assert closed["owner"]["state"] == "closed"
    assert closed["initialized"] is False


def test_stale_stdio_reader_error_from_prior_generation_is_ignored() -> None:
    from aegis.mcp.client import MCPClient

    client = MCPClient("stale")
    old_generation = client._begin_transport_owner("stdio")
    current_generation = client._begin_transport_owner("stdio")

    client._record_stdio_reader_error(
        "server closed the connection",
        generation=old_generation,
    )

    assert client._stdio_reader_error is None
    assert client.state == "disconnected"

    client._record_stdio_reader_error(
        "server closed the connection",
        generation=current_generation,
    )

    assert "server closed the connection" in client._stdio_reader_error
    assert client.state == "reconnect_needed"


def test_stale_sse_event_from_prior_generation_cannot_mark_tools_stale() -> None:
    from aegis.mcp.client import MCPClient

    client = MCPClient("stale-sse", url="https://example.test/sse", transport="sse")
    old_generation = client._begin_transport_owner("sse")
    current_generation = client._begin_transport_owner("sse")
    notification = json.dumps({
        "jsonrpc": "2.0",
        "method": "notifications/tools/list_changed",
    })

    client._dispatch_background_sse_event(
        "message",
        notification,
        generation=old_generation,
    )
    assert client.tools_stale is False

    client._dispatch_background_sse_event(
        "message",
        notification,
        generation=current_generation,
    )
    assert client.tools_stale is True


class _SchemaCaptureProvider:
    context_length = 200_000
    name = "stage-z"
    model = "mcp-refresh"
    api_mode = None
    auth = None

    def __init__(self):
        self.tool_batches: list[list[dict]] = []

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        self.tool_batches.append([dict(tool) for tool in (tools or [])])
        return LLMResponse(text="done")


def _agent_mcp_config():
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data.setdefault("agent", {})["stream"] = False
    cfg.data.setdefault("agent", {})["self_verify"] = False
    cfg.data.setdefault("memory", {})["enabled"] = False
    cfg.data.setdefault("skills", {})["auto_load"] = False
    cfg.data.setdefault("tools", {})["toolsets"] = ["core", "mcp"]
    cfg.data.setdefault("tools", {})["defer_schemas"] = False
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}
    cfg.data.setdefault("learn", {})["auto"] = False
    cfg.data.setdefault("learn", {})["auto_apply_skills"] = False
    return cfg


def test_agent_refreshes_mcp_registry_before_provider_schema_snapshot(tmp_path) -> None:
    from aegis.agent.agent import Agent
    from aegis.mcp.client import MCPClient, MCPManager
    from aegis.session import Session
    from aegis.tools.registry import ToolRegistry

    client = MCPClient("live")
    tool_lists = [
        [{"name": "old_tool", "description": "old", "inputSchema": {"type": "object"}}],
        [{"name": "new_tool", "description": "new", "inputSchema": {"type": "object"}}],
    ]

    def fake_request(self, method, params=None, notify=False):
        if method == "tools/list":
            return {"result": {"tools": tool_lists.pop(0)}}
        raise AssertionError(method)

    client.connect = lambda: client
    client._request = MethodType(fake_request, client)
    manager = MCPManager()
    manager.add(client)
    registry = ToolRegistry()
    registry.register_all(manager.connect_all())
    provider = _SchemaCaptureProvider()
    agent = Agent(
        config=_agent_mcp_config(),
        provider=provider,
        session=Session.create(),
        registry=registry,
        cwd=tmp_path,
    )
    agent._mcp = manager
    events: list[dict] = []

    client._handle_notification({"method": "notifications/tools/list_changed"})
    result = agent.run("show live tools", on_event=events.append)

    assert result.content == "done"
    visible_names = {tool["name"] for tool in provider.tool_batches[0]}
    assert "mcp__live__old_tool" not in visible_names
    assert "mcp__live__new_tool" in visible_names
    assert any(event["type"] == "mcp_tools_refreshed" for event in events)
    assert agent.session.meta["last_mcp_refresh"]["added"] == ["mcp__live__new_tool"]
