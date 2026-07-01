"""Stage Z MCP idle/background sampling contracts."""

from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace


def _write_idle_sampling_server(path) -> None:
    path.write_text(
        "import json, os, sys, time\n"
        "\n"
        "marker_path = sys.argv[1]\n"
        "out_path = sys.argv[2]\n"
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
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {\n"
        "                'protocolVersion': '2025-06-18',\n"
        "                'capabilities': {'tools': {}},\n"
        "                'serverInfo': {'name': 'idle-sampling', 'version': '1'},\n"
        "            },\n"
        "        })\n"
        "    elif method == 'notifications/initialized':\n"
        "        continue\n"
        "    elif method == 'tools/call':\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {\n"
        "                'content': [{'type': 'text', 'text': 'tool done'}],\n"
        "                'isError': False,\n"
        "            },\n"
        "        })\n"
        "        while not os.path.exists(marker_path):\n"
        "            time.sleep(0.01)\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': 'idle-sample',\n"
        "            'method': 'sampling/createMessage',\n"
        "            'params': {\n"
        "                'systemPrompt': 'Use idle context.',\n"
        "                'maxTokens': 55,\n"
        "                'messages': [{\n"
        "                    'role': 'user',\n"
        "                    'content': {'type': 'text', 'text': 'answer after return'},\n"
        "                }],\n"
        "            },\n"
        "        })\n"
        "        sampling_response = read_msg()\n"
        "        with open(out_path, 'w', encoding='utf-8') as f:\n"
        "            json.dump(sampling_response, f)\n"
        "    else:\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'error': {'code': -32601, 'message': 'method not found'},\n"
        "        })\n",
        encoding="utf-8",
    )


def _wait_for_json(path, *, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


class _SamplingProvider:
    model = "idle-provider-model"

    def __init__(self):
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        self.calls.append({
            "messages": messages,
            "tools": tools,
            "kwargs": kwargs,
        })
        return LLMResponse(text="idle sampled answer", finish_reason="stop")


def test_stdio_idle_sampling_after_tool_response_reuses_sampling_context(tmp_path) -> None:
    from aegis.mcp.client import MCPClient, MCPTool
    from aegis.tools.base import ToolContext

    server = tmp_path / "idle_sampling_server.py"
    marker = tmp_path / "ask_now"
    out = tmp_path / "sampling_response.json"
    _write_idle_sampling_server(server)

    provider = _SamplingProvider()
    ctx = ToolContext(
        cwd=tmp_path,
        agent=SimpleNamespace(provider=provider, reasoning="low", config=None),
    )
    client = MCPClient("idle_sampling", command=sys.executable, args=["-u", str(server), str(marker), str(out)])

    try:
        client.connect()
        tool = MCPTool(client, {"name": "start_idle", "inputSchema": {"type": "object"}})
        result = tool.run({}, ctx)
        assert result.is_error is False
        assert result.content == "tool done"
        assert client._current_tool_context() is None

        marker.write_text("go", encoding="utf-8")
        sampling_response = _wait_for_json(out)
    finally:
        client.close()

    assert sampling_response["id"] == "idle-sample"
    assert sampling_response["result"] == {
        "role": "assistant",
        "content": {"type": "text", "text": "idle sampled answer"},
        "model": "idle-provider-model",
        "stopReason": "endTurn",
    }
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert [message.role for message in call["messages"]] == ["system", "user"]
    assert call["messages"][0].content == "Use idle context."
    assert call["messages"][1].content == "answer after return"
    assert call["kwargs"]["max_tokens"] == 55
    assert call["kwargs"]["reasoning"] == "low"
    assert call["kwargs"]["cwd"] == tmp_path


def test_idle_sampling_snapshot_expires_fail_closed(tmp_path) -> None:
    from aegis.mcp.client import MCPClient

    class Provider:
        model = "expired-provider"

        def complete(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise AssertionError("expired idle sampling context must not call provider")

    client = MCPClient("idle_sampling", sampling={"idle_context_ttl": 0.01})
    client._sampling_context_snapshot = {
        "provider": Provider(),
        "config": None,
        "cwd": tmp_path,
        "reasoning": "low",
        "contextvars": None,
        "captured_at": time.monotonic() - 1.0,
    }

    response = client._server_request_response({
        "jsonrpc": "2.0",
        "id": "idle-expired",
        "method": "sampling/createMessage",
        "params": {
            "messages": [{
                "role": "user",
                "content": {"type": "text", "text": "too late"},
            }],
        },
    })

    assert response["id"] == "idle-expired"
    assert response["error"]["code"] == -32000
    assert "before an agent context was available" in response["error"]["message"]
    assert client._sampling_context_snapshot is None
    assert client.lifecycle_metadata["sampling"]["errors"] == 1
