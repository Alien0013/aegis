"""Stage Z MCP elicitation routing contracts."""

from __future__ import annotations

import json
import sys
import threading
import time


def _write_elicitation_server(
    path,
    *,
    mode: str = "form",
    reuse_request_id: bool = False,
) -> None:
    path.write_text(
        "import json, sys\n"
        "\n"
        f"MODE = {mode!r}\n"
        f"REUSE_REQUEST_ID = {reuse_request_id!r}\n"
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
        "                'serverInfo': {'name': 'elicit', 'version': '1'},\n"
        "            },\n"
        "        })\n"
        "    elif method == 'notifications/initialized':\n"
        "        continue\n"
        "    elif method == 'tools/list':\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {'tools': [{\n"
        "                'name': 'pay',\n"
        "                'description': 'requires consent',\n"
        "                'inputSchema': {'type': 'object', 'properties': {}},\n"
        "            }]},\n"
        "        })\n"
        "    elif method == 'tools/call':\n"
        "        elicitation_id = mid if REUSE_REQUEST_ID else 'elicit-1'\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': elicitation_id,\n"
        "            'method': 'elicitation/create',\n"
        "            'params': {\n"
        "                'mode': MODE,\n"
        "                'message': 'Approve payment?',\n"
        "                'requested_schema': {\n"
        "                    'type': 'object',\n"
        "                    'properties': {\n"
        "                        'amount': {'type': 'number', 'description': 'USD amount'},\n"
        "                    },\n"
        "                },\n"
        "            },\n"
        "        })\n"
        "        elicitation_response = read_msg()\n"
        "        send({\n"
        "            'jsonrpc': '2.0',\n"
        "            'id': mid,\n"
        "            'result': {\n"
        "                'content': [{\n"
        "                    'type': 'text',\n"
        "                    'text': json.dumps(elicitation_response),\n"
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


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_stdio_elicitation_form_routes_through_context_approver(tmp_path) -> None:
    from aegis.mcp.client import MCPClient
    from aegis.tools.thread_context import reset_current_approver, set_current_approver

    server = tmp_path / "elicit_server.py"
    _write_elicitation_server(server, mode="form")
    prompts: list[str] = []

    def approver(prompt: str) -> bool:
        prompts.append(prompt)
        return True

    token = set_current_approver(approver)
    client = MCPClient("elicit", command=sys.executable, args=["-u", str(server)])
    try:
        client.connect()
        content, is_error = client.call_tool("pay", {})
    finally:
        client.close()
        reset_current_approver(token)

    assert is_error is False
    response = json.loads(content)
    assert response["id"] == "elicit-1"
    assert response["result"] == {"action": "accept", "content": {}}
    assert prompts
    assert "Approve payment?" in prompts[0]
    assert "amount (number): USD amount" in prompts[0]


def test_stdio_elicitation_form_declines_when_context_approver_denies(tmp_path) -> None:
    from aegis.mcp.client import MCPClient
    from aegis.tools.thread_context import reset_current_approver, set_current_approver

    server = tmp_path / "elicit_server.py"
    _write_elicitation_server(server, mode="form")
    prompts: list[str] = []

    def approver(prompt: str) -> bool:
        prompts.append(prompt)
        return False

    token = set_current_approver(approver)
    client = MCPClient("elicit", command=sys.executable, args=["-u", str(server)])
    try:
        client.connect()
        content, is_error = client.call_tool("pay", {})
    finally:
        client.close()
        reset_current_approver(token)

    assert is_error is False
    response = json.loads(content)
    assert response["id"] == "elicit-1"
    assert response["result"] == {"action": "decline"}
    assert prompts
    assert "Approve payment?" in prompts[0]


def test_stdio_elicitation_url_mode_declines_without_prompt(tmp_path) -> None:
    from aegis.mcp.client import MCPClient

    server = tmp_path / "elicit_server.py"
    _write_elicitation_server(server, mode="url")
    client = MCPClient("elicit", command=sys.executable, args=["-u", str(server)])
    try:
        client.connect()
        content, is_error = client.call_tool("pay", {})
    finally:
        client.close()

    assert is_error is False
    response = json.loads(content)
    assert response["id"] == "elicit-1"
    assert response["result"] == {"action": "decline"}


def test_stdio_elicitation_request_id_collision_does_not_complete_active_request(tmp_path) -> None:
    from aegis.mcp.client import MCPClient

    server = tmp_path / "elicit_server.py"
    _write_elicitation_server(server, mode="url", reuse_request_id=True)
    client = MCPClient("elicit", command=sys.executable, args=["-u", str(server)])
    try:
        client.connect()
        content, is_error = client.call_tool("pay", {})
    finally:
        client.close()

    assert is_error is False
    response = json.loads(content)
    assert "method" not in response
    assert response["result"] == {"action": "decline"}


def test_stdio_elicitation_form_without_approval_path_declines(tmp_path) -> None:
    from aegis.mcp.client import MCPClient

    server = tmp_path / "elicit_server.py"
    _write_elicitation_server(server, mode="form")
    client = MCPClient("elicit", command=sys.executable, args=["-u", str(server)])
    try:
        client.connect()
        content, is_error = client.call_tool("pay", {})
    finally:
        client.close()

    assert is_error is False
    response = json.loads(content)
    assert response["id"] == "elicit-1"
    assert response["result"] == {"action": "decline"}


def test_stdio_elicitation_form_waits_for_pending_approval_queue_resolution(tmp_path) -> None:
    from aegis.mcp.client import MCPClient
    from aegis.tools.thread_context import (
        clear_pending_approvals,
        pending_approval_count,
        propagate_context_to_thread,
        register_approval_notifier,
        reset_current_session_key,
        resolve_pending_approval,
        set_current_session_key,
        unregister_approval_notifier,
    )

    server = tmp_path / "elicit_server.py"
    _write_elicitation_server(server, mode="form")
    session_key = "stage-z-mcp-elicit-pending"
    notifications: list[dict] = []
    result: dict[str, object] = {}
    clear_pending_approvals(session_key)
    register_approval_notifier(session_key, notifications.append)
    client = MCPClient("elicit", command=sys.executable, args=["-u", str(server)])

    def call_tool() -> None:
        try:
            result["value"] = client.call_tool("pay", {})
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    try:
        client.connect()
        token = set_current_session_key(session_key)
        try:
            thread = threading.Thread(target=propagate_context_to_thread(call_tool))
        finally:
            reset_current_session_key(token)
        thread.start()
        try:
            assert _wait_until(lambda: pending_approval_count(session_key) == 1)
            assert notifications
            assert notifications[0]["type"] == "mcp_elicitation"
            assert notifications[0]["session_key"] == session_key
            assert notifications[0]["message"] == "Approve payment?"
            assert "amount (number): USD amount" in notifications[0]["description"]

            assert resolve_pending_approval(session_key, "once") == 1
            thread.join(timeout=2)
        finally:
            clear_pending_approvals(session_key)
            thread.join(timeout=1)
    finally:
        client.close()
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)

    assert not thread.is_alive()
    assert "error" not in result
    content, is_error = result["value"]
    assert is_error is False
    response = json.loads(content)
    assert response["id"] == "elicit-1"
    assert response["result"] == {"action": "accept", "content": {}}
    assert pending_approval_count(session_key) == 0


def test_stdio_elicitation_form_pending_approval_timeout_cancels(tmp_path) -> None:
    from aegis.mcp.client import MCPClient
    from aegis.tools.thread_context import (
        clear_pending_approvals,
        pending_approval_count,
        propagate_context_to_thread,
        register_approval_notifier,
        reset_current_session_key,
        set_current_session_key,
        unregister_approval_notifier,
    )

    server = tmp_path / "elicit_server.py"
    _write_elicitation_server(server, mode="form")
    session_key = "stage-z-mcp-elicit-timeout"
    notifications: list[dict] = []
    result: dict[str, object] = {}
    clear_pending_approvals(session_key)
    register_approval_notifier(session_key, notifications.append)
    client = MCPClient(
        "elicit",
        command=sys.executable,
        args=["-u", str(server)],
        elicitation={"timeout": 0.05},
    )

    def call_tool() -> None:
        try:
            result["value"] = client.call_tool("pay", {})
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    try:
        client.connect()
        token = set_current_session_key(session_key)
        try:
            thread = threading.Thread(target=propagate_context_to_thread(call_tool))
        finally:
            reset_current_session_key(token)
        thread.start()
        try:
            assert _wait_until(lambda: pending_approval_count(session_key) == 1)
            thread.join(timeout=2)
        finally:
            clear_pending_approvals(session_key)
            thread.join(timeout=1)
    finally:
        client.close()
        unregister_approval_notifier(session_key)
        clear_pending_approvals(session_key)

    assert not thread.is_alive()
    assert "error" not in result
    assert notifications
    content, is_error = result["value"]
    assert is_error is False
    response = json.loads(content)
    assert response["id"] == "elicit-1"
    assert response["result"] == {"action": "cancel"}
    assert pending_approval_count(session_key) == 0


def test_stdio_elicitation_form_routes_through_pending_approval_queue(tmp_path) -> None:
    from aegis.mcp.client import MCPClient
    from aegis.tools.thread_context import (
        register_approval_notifier,
        reset_current_session_key,
        resolve_pending_approval,
        set_current_session_key,
        unregister_approval_notifier,
    )

    server = tmp_path / "elicit_server.py"
    _write_elicitation_server(server, mode="form")
    notifications: list[dict] = []

    def notify(payload: dict) -> None:
        notifications.append(dict(payload))
        assert resolve_pending_approval("mcp-session", "allow") == 1

    register_approval_notifier("mcp-session", notify)
    token = set_current_session_key("mcp-session")
    client = MCPClient("elicit", command=sys.executable, args=["-u", str(server)])
    try:
        client.connect()
        content, is_error = client.call_tool("pay", {})
    finally:
        client.close()
        reset_current_session_key(token)
        unregister_approval_notifier("mcp-session")

    assert is_error is False
    response = json.loads(content)
    assert response["id"] == "elicit-1"
    assert response["result"] == {"action": "accept", "content": {}}
    assert notifications
    assert notifications[0]["type"] == "mcp_elicitation"
    assert notifications[0]["server"] == "elicit"
