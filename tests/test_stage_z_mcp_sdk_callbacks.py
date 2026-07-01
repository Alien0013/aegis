"""Stage Z MCP SDK lifecycle and callback compatibility contracts."""

from __future__ import annotations

import asyncio
import sys
import textwrap
from types import SimpleNamespace

import pytest


class _FakeStdioParams:
    def __init__(self, command, args=None, env=None, cwd=None):  # noqa: ANN001
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd


class _FakeStdioClient:
    async def __aenter__(self):
        return object(), object()

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


def _fake_stdio_client(_params):
    return _FakeStdioClient()


def _install_fake_sdk(monkeypatch, session_cls, *, types: dict[str, object] | None = None) -> None:
    import aegis.mcp.client as mcp_client

    monkeypatch.setattr(mcp_client, "_MCP_SDK_AVAILABLE", True)
    monkeypatch.setattr(mcp_client, "_MCP_SDK_CLIENT_SESSION", session_cls)
    monkeypatch.setattr(mcp_client, "_MCP_SDK_STDIO_SERVER_PARAMETERS", _FakeStdioParams)
    monkeypatch.setattr(mcp_client, "_MCP_SDK_STDIO_CLIENT", _fake_stdio_client)
    for name, value in (types or {}).items():
        monkeypatch.setattr(mcp_client, name, value)


def test_sdk_client_session_lifecycle_and_rpcs_run_in_one_owner_task(monkeypatch) -> None:
    from aegis.mcp.client import MCPClient

    seen: dict[str, int] = {}

    def mark(name: str) -> None:
        seen[name] = id(asyncio.current_task())

    class FakeClientSession:
        def __init__(self, read_stream, write_stream, *, message_handler=None):  # noqa: ANN001
            assert read_stream is not None
            assert write_stream is not None
            assert message_handler is not None
            mark("init")

        async def __aenter__(self):
            mark("enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            mark("exit")
            return False

        async def initialize(self):
            mark("initialize")
            return SimpleNamespace(capabilities=SimpleNamespace(tools={}))

        async def list_tools(self):
            mark("list_tools")
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="probe",
                        description="Probe",
                        inputSchema={"type": "object", "properties": {}},
                    )
                ]
            )

        async def call_tool(self, name, arguments=None):  # noqa: ANN001
            mark("call_tool")
            assert name == "probe"
            assert arguments == {"x": 1}
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="sdk ok")],
                isError=False,
            )

    _install_fake_sdk(monkeypatch, FakeClientSession)
    client = MCPClient("sdk", command="fake-mcp", sdk=True)
    try:
        client.connect()
        assert client.list_tools()[0]["name"] == "probe"
        assert client.call_tool("probe", {"x": 1}) == ("sdk ok", False)
    finally:
        client.close()

    task_ids = {
        seen["init"],
        seen["enter"],
        seen["initialize"],
        seen["list_tools"],
        seen["call_tool"],
        seen["exit"],
    }
    assert len(task_ids) == 1


class _SDKType:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _SamplingCapability(_SDKType):
    pass


class _SamplingToolsCapability(_SDKType):
    pass


class _CreateMessageResult(_SDKType):
    pass


class _TextContent(_SDKType):
    pass


class _ElicitResult(_SDKType):
    pass


class _ListRootsResult(_SDKType):
    pass


class _Root(_SDKType):
    pass


def test_sdk_session_kwargs_and_callbacks_use_sdk_native_shapes(monkeypatch, tmp_path) -> None:
    from aegis.mcp.client import MCPClient

    seen: dict[str, object] = {}

    class FakeClientSession:
        def __init__(
            self,
            read_stream,  # noqa: ANN001
            write_stream,  # noqa: ANN001
            *,
            sampling_callback=None,
            sampling_capabilities=None,
            elicitation_callback=None,
            list_roots_callback=None,
            message_handler=None,
            logging_callback=None,
        ):
            seen["kwargs"] = {
                "sampling_callback": sampling_callback,
                "sampling_capabilities": sampling_capabilities,
                "elicitation_callback": elicitation_callback,
                "list_roots_callback": list_roots_callback,
                "message_handler": message_handler,
                "logging_callback": logging_callback,
            }

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        async def initialize(self):
            return SimpleNamespace(capabilities=SimpleNamespace(tools={}))

        async def call_tool(self, name, arguments=None):  # noqa: ANN001
            kwargs = seen["kwargs"]
            seen["sampling_result"] = await kwargs["sampling_callback"](
                None,
                SimpleNamespace(messages=[], maxTokens=8),
            )
            seen["elicitation_result"] = await kwargs["elicitation_callback"](
                None,
                SimpleNamespace(mode="form", message="Approve?", requested_schema={}),
            )
            seen["roots_result"] = await kwargs["list_roots_callback"]()
            await kwargs["message_handler"](
                SimpleNamespace(
                    root=SimpleNamespace(
                        method="notifications/tools/list_changed",
                        params={},
                    )
                )
            )
            await kwargs["logging_callback"](
                None,
                SimpleNamespace(level="info", data="hello"),
            )
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="callbacks ok")],
                isError=False,
            )

    _install_fake_sdk(
        monkeypatch,
        FakeClientSession,
        types={
            "_MCP_SDK_SAMPLING_CAPABILITY": _SamplingCapability,
            "_MCP_SDK_SAMPLING_TOOLS_CAPABILITY": _SamplingToolsCapability,
            "_MCP_SDK_CREATE_MESSAGE_RESULT": _CreateMessageResult,
            "_MCP_SDK_TEXT_CONTENT": _TextContent,
            "_MCP_SDK_ELICIT_RESULT": _ElicitResult,
            "_MCP_SDK_LIST_ROOTS_RESULT": _ListRootsResult,
            "_MCP_SDK_ROOT": _Root,
        },
    )
    client = MCPClient(
        "sdk-callbacks",
        command="fake-mcp",
        sdk=True,
        roots=[str(tmp_path)],
    )
    client._handle_sampling_request = lambda _params: {
        "role": "assistant",
        "content": {"type": "text", "text": "sampled"},
        "model": "sdk-test",
        "stopReason": "endTurn",
    }
    client._handle_elicitation_request = lambda _params: {
        "action": "accept",
        "content": {},
    }

    try:
        client.connect()
        assert client.call_tool("probe", {}) == ("callbacks ok", False)
    finally:
        client.close()

    kwargs = seen["kwargs"]
    assert callable(kwargs["sampling_callback"])
    assert isinstance(kwargs["sampling_capabilities"], _SamplingCapability)
    assert isinstance(kwargs["sampling_capabilities"].tools, _SamplingToolsCapability)
    assert callable(kwargs["elicitation_callback"])
    assert callable(kwargs["list_roots_callback"])
    assert callable(kwargs["message_handler"])
    assert callable(kwargs["logging_callback"])

    sampling_result = seen["sampling_result"]
    assert isinstance(sampling_result, _CreateMessageResult)
    assert isinstance(sampling_result.content, _TextContent)
    assert sampling_result.content.text == "sampled"

    elicitation_result = seen["elicitation_result"]
    assert isinstance(elicitation_result, _ElicitResult)
    assert elicitation_result.action == "accept"
    assert elicitation_result.content == {}

    roots_result = seen["roots_result"]
    assert isinstance(roots_result, _ListRootsResult)
    assert isinstance(roots_result.roots[0], _Root)
    assert roots_result.roots[0].uri.startswith("file://")

    assert client.tools_stale is True
    assert client.recent_notifications[-1]["method"] == "notifications/logging/message"


def test_real_mcp_sdk_stdio_server_tools_resources_and_prompts(tmp_path) -> None:
    pytest.importorskip("mcp")
    pytest.importorskip("mcp.server.fastmcp")

    from aegis.mcp.client import MCPClient

    server = tmp_path / "real_sdk_server.py"
    server.write_text(
        textwrap.dedent(
            """
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("aegis-real-sdk-proof")

            @mcp.tool()
            def echo(text: str) -> str:
                return "real sdk: " + text

            @mcp.resource("memory://status")
            def status() -> str:
                return "resource ok"

            @mcp.prompt()
            def greet(name: str) -> str:
                return f"hello {name}"

            if __name__ == "__main__":
                mcp.run(transport="stdio")
            """
        ),
        encoding="utf-8",
    )

    client = MCPClient(
        "real-sdk",
        command=sys.executable,
        args=["-u", str(server)],
        sdk=True,
    )
    try:
        client.connect()

        tools = client.list_tools()
        assert [tool["name"] for tool in tools] == ["echo"]
        assert tools[0]["inputSchema"]["properties"]["text"]["type"] == "string"

        text, is_error = client.call_tool("echo", {"text": "ok"})
        assert is_error is False
        assert "real sdk: ok" in text
        assert "<structuredContent>" in text

        resources = client.list_resources()
        assert resources[0]["uri"] == {}
        assert client.read_resource("memory://status") == (
            '<resource uri="memory://status" mime="text/plain">\n'
            "resource ok\n"
            "</resource>"
        )

        prompts = client.list_prompts()
        assert prompts[0]["name"] == "greet"
        assert client.get_prompt("greet", {"name": "AEGIS"}) == "<user>\nhello AEGIS\n</user>"
    finally:
        client.close()
