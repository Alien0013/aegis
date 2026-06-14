"""repo_map tool: a ranked structural outline of the codebase.

Lets the agent orient on an unfamiliar repo (which files matter, what they define,
where a symbol lives) without reading everything — AEGIS's repo-map answer to
Aider/Cursor. Backed by :mod:`aegis.repomap`.
"""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolContext, ToolResult


class RepoMapTool(Tool):
    name = "repo_map"
    description = (
        "Structural map of the codebase: the most important files (ranked by how often "
        "their symbols are referenced) and the classes/functions each defines. "
        "action: map (whole-repo outline) | find (locate where a symbol is defined). "
        "Optional path scopes to a subdirectory; optional query filters by file path or "
        "symbol name. Use this first to orient on an unfamiliar repo instead of reading files blindly."
    )
    groups = ["fs"]
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["map", "find"], "default": "map"},
            "path": {"type": "string", "description": "Subdirectory to scope to (default: cwd)."},
            "query": {"type": "string", "description": "Filter map by path/symbol substring."},
            "name": {"type": "string", "description": "Symbol name to locate (action=find)."},
        },
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        from .. import repomap

        root = Path(args.get("path") or ".").expanduser()
        if not root.is_absolute():
            root = (ctx.cwd or Path.cwd()) / root
        if not root.exists():
            return ToolResult.error(f"no such path: {root}")
        root = root if root.is_dir() else root.parent

        action = args.get("action") or "map"
        if action == "find":
            name = (args.get("name") or args.get("query") or "").strip()
            if not name:
                return ToolResult.error("action=find requires a symbol name")
            hits = repomap.find_symbol(root, name)
            if not hits:
                return ToolResult.ok(f"no definition of '{name}' found under {root.name}/",
                                     display=f"repo_map: '{name}' not found")
            body = "\n".join(f"{rel}:{line}  ({kind})" for rel, line, kind in hits[:50])
            return ToolResult.ok(f"Definitions of '{name}':\n{body}",
                                 display=f"repo_map: {len(hits)} def(s) of '{name}'")

        text = repomap.render_map(root, query=str(args.get("query") or ""))
        return ToolResult.ok(text, display=f"repo map of {root.name}/")


def repomap_tools() -> list[Tool]:
    return [RepoMapTool()]
