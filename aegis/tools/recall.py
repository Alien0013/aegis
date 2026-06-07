"""Cross-session recall tool: search past conversations ("remember what happened before")."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult


class SessionSearchTool(Tool):
    name = "session_search"
    description = ("Search your PAST conversations/sessions for something discussed before. "
                   "Use when the user references earlier work ('like we did last time', "
                   "'what did we decide about X'). Returns matching snippets with session ids.")
    toolset = "core"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "max results (default 8)"},
        },
        "required": ["query"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..session import SessionStore
        hits = SessionStore().search_messages(args["query"], int(args.get("limit", 8)))
        if not hits:
            return ToolResult.ok("(nothing found in past sessions)", display="recall: no matches")
        lines = [f"[{h['when']}] {h['title']} ({h['session'][:14]})\n  {h['role']}: {h['snippet']}"
                 for h in hits]
        return ToolResult.ok("\n".join(lines), display=f"recall: {len(hits)} match(es)")


def recall_tools() -> list[Tool]:
    return [SessionSearchTool()]
