"""Stage Z MCP client capability surface regressions."""

from __future__ import annotations

import json
from types import MethodType


def test_mcp_roots_list_uses_configured_file_roots(tmp_path):
    from aegis.mcp.client import MCPClient

    root = tmp_path / "workspace"
    root.mkdir()
    client = MCPClient("roots", roots=[{"path": str(root), "name": "Workspace"}])

    response = client._server_request_response({
        "jsonrpc": "2.0",
        "id": "roots-1",
        "method": "roots/list",
    })

    assert client._client_capabilities()["roots"] == {"listChanged": False}
    assert response == {
        "jsonrpc": "2.0",
        "id": "roots-1",
        "result": {
            "roots": [{"uri": root.resolve().as_uri(), "name": "Workspace"}],
        },
    }


def test_mcp_client_uses_cwd_as_default_root(tmp_path):
    from aegis.mcp.client import MCPClient

    client = MCPClient("roots", cwd=str(tmp_path))

    assert client.roots == [{
        "uri": tmp_path.resolve().as_uri(),
        "name": tmp_path.name,
    }]


def test_mcp_progress_and_logging_notifications_are_retained_and_redacted():
    from aegis.mcp.client import MCPClient

    client = MCPClient("notify")
    client._handle_notification({
        "method": "notifications/progress",
        "params": {
            "progressToken": "turn-1",
            "progress": 3,
            "total": 10,
            "message": "Bearer secret-token",
        },
    })
    client._handle_notification({
        "method": "notifications/message",
        "params": {"level": "warning", "logger": "mcp", "data": "hello"},
    })

    notifications = client.recent_notifications
    assert [item["method"] for item in notifications] == [
        "notifications/progress",
        "notifications/message",
    ]
    assert notifications[0]["params"]["progressToken"] == "[REDACTED]"
    assert "secret-token" not in json.dumps(notifications)
    assert notifications[1]["params"]["level"] == "warning"


def test_mcp_completion_complete_helper_sends_reference_and_context():
    from aegis.mcp.client import MCPClient

    client = MCPClient("complete")
    seen: list[tuple[str, dict | None, bool]] = []

    def fake_request(self, method, params=None, notify=False):
        seen.append((method, params, notify))
        return {
            "result": {
                "completion": {
                    "values": ["alpha", "alphabet"],
                    "total": 2,
                    "hasMore": False,
                },
            },
        }

    client._request = MethodType(fake_request, client)

    result = client.complete(
        {"type": "ref/prompt", "name": "search"},
        {"name": "query", "value": "alp"},
        context_arguments={"mode": "fast"},
    )

    assert seen == [(
        "completion/complete",
        {
            "ref": {"type": "ref/prompt", "name": "search"},
            "argument": {"name": "query", "value": "alp"},
            "context": {"arguments": {"mode": "fast"}},
        },
        False,
    )]
    assert result["values"] == ["alpha", "alphabet"]
