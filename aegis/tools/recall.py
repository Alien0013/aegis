"""Cross-session recall tool: browse and search past conversations."""

from __future__ import annotations

import json

from .base import Tool, ToolContext, ToolResult


class SessionSearchTool(Tool):
    name = "session_search"
    description = (
        "Browse, search, read, or scroll PAST conversations/sessions. Use whenever the "
        "user references previous chats, last session, memory, or earlier work instead "
        "of asking them to repeat context."
    )
    toolset = "core"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language search query. Omit to browse recent sessions.",
            },
            "session_id": {
                "type": "string",
                "description": "Session id, id prefix, or exact title to read/scroll.",
            },
            "around_message_id": {
                "type": "integer",
                "description": "Message id to center when scrolling within a session.",
            },
            "window": {
                "type": "integer",
                "description": "Messages before/after around_message_id (default 5, max 20).",
            },
            "head": {
                "type": "integer",
                "description": "Initial messages to include when reading a session (default 20).",
            },
            "tail": {
                "type": "integer",
                "description": "Final messages to include when reading a long session (default 10).",
            },
            "limit": {
                "type": "integer",
                "description": "Max sessions/results (browse default 10, search default 3).",
            },
            "sort": {
                "type": "string",
                "enum": ["rank", "newest", "oldest"],
                "description": "Sort search results by rank or timestamp.",
            },
            "role_filter": {
                "type": "array",
                "items": {"type": "string", "enum": ["user", "assistant", "tool"]},
                "description": "Restrict search matches by message role.",
            },
            "profile": {
                "type": "string",
                "description": "Optional AEGIS config profile to search (default searches the active profile).",
            },
        },
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..session import SessionStore

        requested_profile = str(args.get("profile") or "").strip()
        try:
            store = SessionStore(profile=requested_profile) if requested_profile else SessionStore()
        except ValueError as e:
            data = {"success": False, "mode": "recall", "error": str(e), "profile": requested_profile}
            content = json.dumps(data, ensure_ascii=False, indent=2)
            return ToolResult(content=content, is_error=True, display="recall: error", data=data)
        current_session_id = getattr(getattr(ctx, "session", None), "id", None)
        if requested_profile:
            current_session_id = None
        query = str(args.get("query") or "").strip()
        session_id = str(args.get("session_id") or "").strip()

        if session_id and args.get("around_message_id") is not None:
            data = store.messages_around(
                session_id,
                args.get("around_message_id"),
                window=int(args.get("window", 5)),
                current_session_id=current_session_id,
            )
        elif session_id:
            data = store.read_session(
                session_id,
                head=int(args.get("head", 20)),
                tail=int(args.get("tail", 10)),
            )
        elif query:
            role_filter = args.get("role_filter")
            if isinstance(role_filter, str):
                role_filter = [r.strip() for r in role_filter.split(",") if r.strip()]
            data = store.discover_sessions(
                query,
                limit=int(args.get("limit", 3)),
                role_filter=role_filter,
                sort=args.get("sort"),
                current_session_id=current_session_id,
            )
        else:
            data = store.browse_sessions(
                limit=int(args.get("limit", 10)),
                current_session_id=current_session_id,
            )

        data.setdefault("profile", requested_profile or store.profile or "")
        content = json.dumps(data, ensure_ascii=False, indent=2)
        mode = data.get("mode", "recall")
        count = data.get("count", len(data.get("messages", [])))
        display = f"recall {mode}: {count}"
        if not data.get("success", True):
            return ToolResult(content=content, is_error=True, display=f"recall {mode}: error", data=data)
        return ToolResult.ok(content, display=display, data=data)


def recall_tools() -> list[Tool]:
    return [SessionSearchTool()]
