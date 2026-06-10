"""LSP tool: code intelligence from persistent language servers.

Backed by :mod:`aegis.lsp` — servers stay alive across calls (fast repeat
queries), auto-install when missing, and are scoped to the file's project
root. Actions: diagnostics, hover, definition, references, rename, symbols,
status, restart.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Tool, ToolContext, ToolResult


class LspTool(Tool):
    name = "lsp"
    description = (
        "Code intelligence from a persistent language server. action: diagnostics | hover | "
        "definition | references | rename | symbols | status | restart. Give path, plus "
        "line + character (0-based) for position queries and new_name for rename. "
        "Only works on files inside a git project."
    )
    groups = ["runtime"]
    toolset = "lsp"
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["diagnostics", "hover", "definition", "references",
                                "rename", "symbols", "status", "restart"]},
            "path": {"type": "string"},
            "line": {"type": "integer"},
            "character": {"type": "integer"},
            "new_name": {"type": "string"},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..lsp import get_service
        from ..lsp.service import format_diags

        service = get_service(ctx.config)
        action = args["action"]
        if action == "status":
            return ToolResult.ok(json.dumps(service.status(), indent=2), display="lsp status")
        if action == "restart":
            service.restart()
            return ToolResult.ok("language servers restarted.", display="lsp restart")

        raw = args.get("path")
        if not raw:
            return ToolResult.error("path is required for this action")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = ctx.cwd / path
        if not path.exists():
            return ToolResult.error(f"no such file: {path}")
        cwd = str(ctx.cwd)

        try:
            if action == "diagnostics":
                diags = service.diagnostics(str(path), cwd)
                if diags is None:
                    return ToolResult.error(
                        "LSP unavailable for this file (no server for the language, the server "
                        "failed to start, or the file is outside a git project).")
                if not diags:
                    return ToolResult.ok("(no diagnostics)", display="lsp diagnostics")
                return ToolResult.ok(format_diags(diags, limit=50),
                                     display=f"lsp {len(diags)} diagnostic(s)")
            result = service.query(action, str(path), int(args.get("line", 0)),
                                   int(args.get("character", 0)), cwd,
                                   new_name=args.get("new_name"))
            if result is None:
                return ToolResult.error(
                    "LSP unavailable for this file (no server for the language, the server "
                    "failed to start, or the file is outside a git project).")
            if action == "hover":
                content = result.get("contents") if isinstance(result, dict) else result
                text = content.get("value") if isinstance(content, dict) else str(content)
                return ToolResult.ok(text or "(no hover info)", display="lsp hover")
            return ToolResult.ok(json.dumps(result, indent=2)[:8000] or f"(no {action})",
                                 display=f"lsp {action}")
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"lsp error: {e}")


def lsp_tools() -> list[Tool]:
    return [LspTool()]
