"""Stage Z MCP stdio background notification contracts."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class _DaemonThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def _write_idle_notification_server(path) -> None:
    path.write_text(
        "import json, os, sys, threading, time\n"
        "\n"
        "notify_path = sys.argv[1]\n"
        "tools_list_calls = 0\n"
        "notification_started = False\n"
        "\n"
        "def send(obj):\n"
        "    sys.stdout.write(json.dumps(obj) + chr(10))\n"
        "    sys.stdout.flush()\n"
        "\n"
        "def emit_idle_notification():\n"
        "    while not os.path.exists(notify_path):\n"
        "        time.sleep(0.01)\n"
        "    send({'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'})\n"
        "\n"
        "def schedule_idle_notification():\n"
        "    global notification_started\n"
        "    if notification_started:\n"
        "        return\n"
        "    notification_started = True\n"
        "    threading.Thread(target=emit_idle_notification, daemon=True).start()\n"
        "\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line:\n"
        "        continue\n"
        "    msg = json.loads(line)\n"
        "    mid = msg.get('id')\n"
        "    method = msg.get('method')\n"
        "    if method == 'initialize':\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {\n"
        "                'protocolVersion': '2025-06-18',\n"
        "                'capabilities': {'tools': {}, 'resources': {}, 'prompts': {}},\n"
        "                'serverInfo': {'name': 'idle', 'version': '1'},\n"
        "            },\n"
        "        })\n"
        "    elif method == 'notifications/initialized':\n"
        "        continue\n"
        "    elif method == 'tools/list':\n"
        "        tools_list_calls += 1\n"
        "        name = 'old_tool' if tools_list_calls == 1 else 'new_tool'\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {'tools': [{\n"
        "                'name': name,\n"
        "                'description': name,\n"
        "                'inputSchema': {'type': 'object', 'properties': {}},\n"
        "            }]},\n"
        "        })\n"
        "    elif method == 'resources/list':\n"
        "        send({'jsonrpc': '2.0', 'id': mid, 'result': {'resources': []}})\n"
        "    elif method == 'prompts/list':\n"
        "        send({'jsonrpc': '2.0', 'id': mid, 'result': {'prompts': []}})\n"
        "        schedule_idle_notification()\n"
        "    else:\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'error': {'code': -32601, 'message': 'method not found'},\n"
        "        })\n",
        encoding="utf-8",
    )


def _wait_until(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _OpenSSEHTTPResponseServer:
    """Streamable HTTP response that stays open after the matching SSE event."""

    def __init__(self) -> None:
        self._server = _DaemonThreadingHTTPServer(("127.0.0.1", 0), self._handler_cls())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}{path}"

    def _handler_cls(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002, ANN001
                return

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                response = {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"tools": [{"name": "new_tool", "inputSchema": {"type": "object"}}]},
                }
                notification = {
                    "jsonrpc": "2.0",
                    "method": "notifications/tools/list_changed",
                }
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps(notification)}\n\n".encode("utf-8"))
                self.wfile.write(f"data: {json.dumps(response)}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(1.5)

        return Handler


class _LegacySSEMCPServer:
    """Minimal legacy MCP SSE transport with idle server notifications."""

    def __init__(self) -> None:
        self.events: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.tools_list_calls = 0
        self._server = _DaemonThreadingHTTPServer(("127.0.0.1", 0), self._handler_cls())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop.set()
        self.events.put(("message", "{}"))
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}{path}"

    def emit_tools_changed(self) -> None:
        self._send_sse({
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
        })

    def _send_sse(self, payload: dict, *, event: str = "message") -> None:
        self.events.put((event, json.dumps(payload)))

    def _handler_cls(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002, ANN001
                return

            def do_GET(self):  # noqa: N802
                if urlparse(self.path).path != "/sse":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"event: endpoint\ndata: /message\n\n")
                self.wfile.flush()
                outer.ready.set()
                while not outer.stop.is_set():
                    try:
                        event, data = outer.events.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        self.wfile.write(f"event: {event}\n".encode("utf-8"))
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionError, OSError):
                        return

            def do_POST(self):  # noqa: N802
                if urlparse(self.path).path != "/message":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                mid = payload.get("id")
                method = payload.get("method")
                if mid is not None:
                    if method == "initialize":
                        result = {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                            "serverInfo": {"name": "legacy-sse", "version": "1"},
                        }
                    elif method == "tools/list":
                        outer.tools_list_calls += 1
                        tool_name = "old_tool" if outer.tools_list_calls == 1 else "new_tool"
                        result = {
                            "tools": [{
                                "name": tool_name,
                                "description": tool_name,
                                "inputSchema": {"type": "object", "properties": {}},
                            }]
                        }
                    elif method == "resources/list":
                        result = {"resources": []}
                    elif method == "prompts/list":
                        result = {"prompts": []}
                    else:
                        outer._send_sse({
                            "jsonrpc": "2.0",
                            "id": mid,
                            "error": {"code": -32601, "message": "method not found"},
                        })
                        self.send_response(202)
                        self.end_headers()
                        return
                    outer._send_sse({"jsonrpc": "2.0", "id": mid, "result": result})
                self.send_response(202)
                self.end_headers()

        return Handler


def test_streamable_http_sse_response_returns_before_stream_closes() -> None:
    from aegis.mcp.client import MCPClient

    with _OpenSSEHTTPResponseServer() as server:
        client = MCPClient("stream", url=server.url("/mcp"))
        started = time.monotonic()
        response = client._http_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }, timeout=5)
        elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert response["id"] == 1
    assert client.tools_stale is True


def test_legacy_sse_idle_notification_refreshes_registered_tools() -> None:
    from aegis.mcp.client import MCPClient, MCPManager
    from aegis.tools.registry import ToolRegistry

    with _LegacySSEMCPServer() as server:
        client = MCPClient("legacy", url=server.url("/sse"), transport="sse")
        manager = MCPManager()
        manager.add(client)
        registry = ToolRegistry()

        try:
            registry.register_all(manager.connect_all())
            assert registry.get("mcp__legacy__old_tool") is not None
            assert client.tools_stale is False

            server.emit_tools_changed()
            assert _wait_until(lambda: client.tools_stale)
            refreshed = manager.refresh_changed_tools(registry)

            assert [tool.name for tool in refreshed] == ["mcp__legacy__new_tool"]
            assert registry.get("mcp__legacy__old_tool") is None
            assert registry.get("mcp__legacy__new_tool") is not None
            assert client.tools_stale is False
        finally:
            manager.close_all()


def test_stdio_tools_list_changed_received_while_idle_refreshes_registered_tools(tmp_path):
    from aegis.mcp.client import MCPClient, MCPManager
    from aegis.tools.registry import ToolRegistry

    server = tmp_path / "idle_mcp_server.py"
    notify_marker = tmp_path / "emit_notification"
    _write_idle_notification_server(server)
    client = MCPClient("idle", command=sys.executable, args=["-u", str(server), str(notify_marker)])
    manager = MCPManager()
    manager.add(client)
    registry = ToolRegistry()

    try:
        registry.register_all(manager.connect_all())
        assert registry.get("mcp__idle__old_tool") is not None
        assert client.tools_stale is False

        notify_marker.write_text("go", encoding="utf-8")
        assert _wait_until(lambda: client.tools_stale), (
            "stdio notifications/tools/list_changed was not received while "
            "no MCP request was actively waiting"
        )
        refreshed = manager.refresh_changed_tools(registry)

        assert [tool.name for tool in refreshed] == ["mcp__idle__new_tool"]
        assert registry.get("mcp__idle__old_tool") is None
        assert registry.get("mcp__idle__new_tool") is not None
        assert client.tools_stale is False
    finally:
        manager.close_all()
