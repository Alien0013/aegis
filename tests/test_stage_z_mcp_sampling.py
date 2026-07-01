"""Stage Z MCP sampling/server-request parity contracts."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace


def _write_sampling_server(path) -> None:
    path.write_text(
        "import json, sys\n"
        "\n"
        "client_capabilities = {}\n"
        "\n"
        "def send(obj):\n"
        "    sys.stdout.write(json.dumps(obj) + chr(10))\n"
        "    sys.stdout.flush()\n"
        "\n"
        "def read_msg():\n"
        "    line = sys.stdin.readline()\n"
        "    if not line:\n"
        "        raise SystemExit(0)\n"
        "    return json.loads(line)\n"
        "\n"
        "while True:\n"
        "    msg = read_msg()\n"
        "    mid = msg.get('id')\n"
        "    method = msg.get('method')\n"
        "    if method == 'initialize':\n"
        "        client_capabilities = msg.get('params', {}).get('capabilities', {})\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {\n"
        "                'protocolVersion': '2025-06-18',\n"
        "                'capabilities': {'tools': {}},\n"
        "                'serverInfo': {'name': 'sampling', 'version': '1'},\n"
        "            },\n"
        "        })\n"
        "    elif method == 'notifications/initialized':\n"
        "        continue\n"
        "    elif method == 'tools/list':\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {'tools': [{\n"
        "                'name': 'ask',\n"
        "                'description': 'asks the client to sample',\n"
        "                'inputSchema': {'type': 'object', 'properties': {}},\n"
        "            }]},\n"
        "        })\n"
        "    elif method == 'tools/call':\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': 'sample-1',\n"
        "            'method': 'sampling/createMessage',\n"
        "            'params': {\n"
        "                'systemPrompt': 'Be concise.',\n"
        "                'maxTokens': 77,\n"
        "                'modelPreferences': {'hints': [{'name': 'hint-model'}]},\n"
        "                'messages': [{\n"
        "                    'role': 'user',\n"
        "                    'content': {'type': 'text', 'text': 'write a haiku'},\n"
        "                }],\n"
        "                'tools': [{\n"
        "                    'name': 'lookup',\n"
        "                    'description': 'Lookup things',\n"
        "                    'inputSchema': {\n"
        "                        'type': 'object',\n"
        "                        'properties': {'q': {'type': 'string'}},\n"
        "                    },\n"
        "                }],\n"
        "            },\n"
        "        })\n"
        "        sampling_response = read_msg()\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {\n"
        "                'content': [{\n"
        "                    'type': 'text',\n"
        "                    'text': json.dumps({\n"
        "                        'sampling_response': sampling_response,\n"
        "                        'client_capabilities': client_capabilities,\n"
        "                    }),\n"
        "                }],\n"
        "                'isError': False,\n"
        "            },\n"
        "        })\n"
        "    else:\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'error': {'code': -32601, 'message': 'method not found'},\n"
        "        })\n",
        encoding="utf-8",
    )


class _SamplingProvider:
    model = "provider-model"

    def __init__(self):
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        self.calls.append({
            "messages": messages,
            "tools": tools,
            "kwargs": kwargs,
        })
        return LLMResponse(text="sampled answer", finish_reason="stop")


def test_stdio_sampling_create_message_uses_active_tool_provider(tmp_path) -> None:
    from aegis.mcp.client import MCPClient, MCPTool
    from aegis.tools.base import ToolContext

    server = tmp_path / "sampling_server.py"
    _write_sampling_server(server)
    provider = _SamplingProvider()
    ctx = ToolContext(
        cwd=tmp_path,
        agent=SimpleNamespace(provider=provider, reasoning="low"),
    )
    client = MCPClient("sampling", command=sys.executable, args=["-u", str(server)])
    try:
        client.connect()
        tool = MCPTool(client, {"name": "ask", "inputSchema": {"type": "object"}})
        result = tool.run({}, ctx)
    finally:
        client.close()

    assert result.is_error is False
    payload = json.loads(result.content)
    sampling_response = payload["sampling_response"]
    assert payload["client_capabilities"]["sampling"] == {}
    assert sampling_response["id"] == "sample-1"
    assert sampling_response["result"] == {
        "role": "assistant",
        "content": {"type": "text", "text": "sampled answer"},
        "model": "provider-model",
        "stopReason": "endTurn",
    }
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert [message.role for message in call["messages"]] == ["system", "user"]
    assert call["messages"][0].content == "Be concise."
    assert call["messages"][1].content == "write a haiku"
    assert call["tools"][0]["name"] == "lookup"
    assert call["tools"][0]["parameters"]["properties"]["q"]["type"] == "string"
    assert call["kwargs"]["max_tokens"] == 77
    assert call["kwargs"]["model"] == "hint-model"
    assert call["kwargs"]["reasoning"] == "low"


def test_sampling_create_message_maps_provider_tool_calls(tmp_path) -> None:
    from aegis.mcp.client import MCPClient
    from aegis.tools.base import ToolContext
    from aegis.types import LLMResponse, ToolCall

    class ToolUseProvider:
        model = "tool-model"

        def complete(self, messages, tools=None, **kwargs):
            return LLMResponse(
                tool_calls=[ToolCall("call_1", "lookup", {"q": "aegis"})],
                finish_reason="tool_calls",
            )

    client = MCPClient("sampling")
    ctx = ToolContext(
        cwd=tmp_path,
        agent=SimpleNamespace(provider=ToolUseProvider(), reasoning="off"),
    )
    with client._stdio_context_lock:
        client._pending_tool_context = ctx
    try:
        response = client._server_request_response({
            "jsonrpc": "2.0",
            "id": "sample-2",
            "method": "sampling/createMessage",
            "params": {"messages": [{"role": "user", "content": "find it"}]},
        })
    finally:
        with client._stdio_context_lock:
            client._pending_tool_context = None

    assert response["id"] == "sample-2"
    assert response["result"] == {
        "role": "assistant",
        "content": [{
            "type": "tool_use",
            "id": "call_1",
            "name": "lookup",
            "input": {"q": "aegis"},
        }],
        "model": "tool-model",
        "stopReason": "toolUse",
    }


def test_streamable_http_server_request_posts_response_without_completing_waiter() -> None:
    from aegis.mcp.client import MCPClient

    client = MCPClient("remote", url="https://example.test/mcp")
    sent: list[dict] = []

    def fake_http_request(payload, timeout=60.0):
        sent.append(payload)
        return None

    client._http_request = fake_http_request
    event_result = client._handle_sse_event(
        "message",
        json.dumps({
            "jsonrpc": "2.0",
            "id": "elicit-1",
            "method": "elicitation/create",
            "params": {"mode": "url"},
        }),
    )

    assert event_result is None
    assert sent == [{
        "jsonrpc": "2.0",
        "id": "elicit-1",
        "result": {"action": "decline"},
    }]
