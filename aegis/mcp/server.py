"""MCP server mode: expose AEGIS's built-in tools to other MCP clients over stdio.

`aegis mcp serve` turns AEGIS into an MCP server (JSON-RPC 2.0, newline-delimited),
so editors/agents that speak MCP can call its filesystem/shell/web/etc. tools.
"""

from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2025-06-18"


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def run_mcp_server(config) -> None:
    from ..tools.base import ToolContext
    from ..tools.permissions import PermissionEngine
    from ..tools.registry import default_registry

    registry = default_registry()
    permissions = PermissionEngine(config)
    ctx = ToolContext(config=config)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, mid = msg.get("method"), msg.get("id")

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aegis", "version": "0.1.0"}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            tools = [{"name": t.name, "description": t.description.strip(),
                      "inputSchema": t.parameters} for t in registry.all()]
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
        elif method == "tools/call":
            params = msg.get("params", {})
            tool = registry.get(params.get("name", ""))
            if tool is None:
                _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}})
                continue
            allowed, reason = permissions.authorize(tool, params.get("arguments", {}), ctx)
            if not allowed:
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"permission denied: {reason}"}],
                    "isError": True}})
                continue
            try:
                res = tool.run(params.get("arguments", {}), ctx)
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": res.content}], "isError": res.is_error}})
            except Exception as e:  # noqa: BLE001
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})


def cmd_mcp_serve(args, config) -> int:
    run_mcp_server(config)
    return 0
