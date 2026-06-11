"""MCP server mode: expose AEGIS's built-in tools to other MCP clients over stdio.

`aegis mcp serve` turns AEGIS into an MCP server (JSON-RPC 2.0, newline-delimited),
so editors/agents that speak MCP can call its filesystem/shell/web/etc. tools.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROTOCOL_VERSION = "2025-06-18"


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def run_mcp_server(config) -> None:
    from ..tools.base import ToolContext
    from ..tools.permissions import PermissionEngine
    from ..tools.registry import default_registry
    from ..memory import MemoryManager
    from ..session import Session, SessionStore
    from ..skills import SkillsLoader

    registry = default_registry()
    permissions = PermissionEngine(config)
    cwd = Path.cwd()
    store = SessionStore()
    session = store.load("mcp:stdio") or Session(id="mcp:stdio", title="MCP stdio")
    skills = SkillsLoader(config, cwd=cwd)
    memory = MemoryManager(config) if config.get("memory.enabled", True) else None
    toolsets = list(config.get("tools.toolsets", []) or ["core"])
    visible_tools = {tool.name: tool for tool in registry.available(toolsets)}
    agent = SimpleNamespace(
        config=config,
        session=session,
        registry=registry,
        memory=memory,
        skills=skills,
        cwd=cwd,
        provider=None,
        tools_used=0,
        _trace_context={},
        deferred_tool_names=lambda available=None: set(),
        activated_tools=set(),
    )
    ctx = ToolContext(
        cwd=cwd,
        config=config,
        memory=memory,
        skills=skills,
        session=session,
        agent=agent,
    )

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
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "aegis", "version": "0.1.0"}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            tools = [{"name": t.name, "description": t.description.strip(),
                      "inputSchema": t.parameters} for t in visible_tools.values()]
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
        elif method == "resources/list":
            # expose skills + memory as readable resources
            from ..skills import SkillsLoader
            res = [{"uri": f"skill://{s.name}", "name": s.name, "description": s.description,
                    "mimeType": "text/markdown"} for s in SkillsLoader(config).available()]
            res.append({"uri": "memory://main", "name": "memory", "mimeType": "text/markdown"})
            _send({"jsonrpc": "2.0", "id": mid, "result": {"resources": res}})
        elif method == "resources/read":
            uri = msg.get("params", {}).get("uri", "")
            text = ""
            if uri.startswith("skill://"):
                from ..skills import SkillsLoader
                text = SkillsLoader(config).activate(uri[len("skill://"):]) or ""
            elif uri.startswith("memory://"):
                from ..memory import MemoryStore
                text = MemoryStore().raw("memory")
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}})
        elif method == "prompts/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"prompts": [
                {"name": "review", "description": "Review the current diff for bugs and cleanups."},
                {"name": "summarize", "description": "Summarize the given text faithfully."}]}})
        elif method == "prompts/get":
            name = msg.get("params", {}).get("name", "")
            body = {"review": "Review the current git diff for correctness bugs and simplifications.",
                    "summarize": "Summarize the following faithfully and concisely:"}.get(name, "")
            _send({"jsonrpc": "2.0", "id": mid, "result": {"messages": [
                {"role": "user", "content": {"type": "text", "text": body}}]}})
        elif method == "tools/call":
            params = msg.get("params", {})
            tool = visible_tools.get(params.get("name", ""))
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
                store.save(session)
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
