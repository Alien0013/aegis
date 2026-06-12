"""Kanban tool: let the agent manage its own task board (the same board behind
``aegis kanban`` and the dashboard). One tool, action-dispatched, so the model can
plan and track multi-step work that outlives a single turn."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolResult

_STATUSES = ("ready", "in_progress", "done", "blocked")


class KanbanTool(Tool):
    name = "kanban"
    description = (
        "Manage your task board (persists across turns/sessions). action: "
        "list | create | move | complete | block | comment | show. "
        "Use for multi-step work you want to track: create cards, move them through "
        "ready→in_progress→done, block with a reason, or comment progress. "
        "move needs id + status (ready|in_progress|done|blocked)."
    )
    groups = ["automation"]
    toolset = "core"
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "enum": ["list", "create", "move", "complete", "block", "comment", "show"]},
            "id": {"type": "string", "description": "Card id (move/complete/block/comment/show)."},
            "title": {"type": "string", "description": "Card title (create)."},
            "body": {"type": "string", "description": "Card details (create)."},
            "status": {"type": "string", "enum": list(_STATUSES),
                       "description": "Target column (move)."},
            "text": {"type": "string", "description": "Comment text (comment) / block reason (block)."},
            "priority": {"type": "integer", "description": "0-3, higher = sooner (create)."},
            "filter_status": {"type": "string", "enum": list(_STATUSES),
                              "description": "Only list this column (list)."},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        from ..kanban import KanbanStore, _fmt_task as format_task
        store = KanbanStore()
        action = args.get("action")
        try:
            if action == "list":
                tasks = store.list(status=args.get("filter_status"))
                if not tasks:
                    return ToolResult.ok("(board empty)", display="kanban: empty")
                body = "\n".join(format_task(t) for t in tasks)
                return ToolResult.ok(body, display=f"kanban: {len(tasks)} card(s)")
            if action == "create":
                if not args.get("title"):
                    return ToolResult.error("create needs a title")
                t = store.create(args["title"].strip(), args.get("body", ""),
                                 int(args.get("priority", 0) or 0))
                return ToolResult.ok(f"created {t.id}: {t.title}", display=f"kanban + {t.title[:40]}")
            if action == "show":
                t = store.show(args.get("id", ""))
                if t is None:
                    return ToolResult.error(f"no card {args.get('id')!r}")
                return ToolResult.ok(
                    f"{t.id} [{t.status}] P{t.priority}\n{t.title}\n\n{t.body or '(no details)'}",
                    display=f"kanban: {t.title[:40]}")
            if action == "move":
                if args.get("status") not in _STATUSES:
                    return ToolResult.error(f"status must be one of {', '.join(_STATUSES)}")
                ok = store._set_status(args.get("id", ""), args["status"])
                return (ToolResult.ok(f"moved {args.get('id')} → {args['status']}")
                        if ok else ToolResult.error(f"no card {args.get('id')!r}"))
            if action == "complete":
                ok = store.complete(args.get("id", ""))
                return ToolResult.ok(f"completed {args.get('id')}") if ok else \
                    ToolResult.error(f"no card {args.get('id')!r}")
            if action == "block":
                ok = store.block(args.get("id", ""), args.get("text", ""))
                return ToolResult.ok(f"blocked {args.get('id')}") if ok else \
                    ToolResult.error(f"no card {args.get('id')!r}")
            if action == "comment":
                if not args.get("text"):
                    return ToolResult.error("comment needs text")
                store.comment(args.get("id", ""), args["text"])
                return ToolResult.ok(f"commented on {args.get('id')}")
        except Exception as e:  # noqa: BLE001
            return ToolResult.error(f"kanban error: {e}")
        return ToolResult.error(f"unknown action {action!r}")


def kanban_tools() -> list[Tool]:
    return [KanbanTool()]
