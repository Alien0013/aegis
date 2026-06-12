"""Long-running background process management."""

from __future__ import annotations

import json

from .base import Tool, ToolContext, ToolResult
from .process_registry import process_registry


class ProcessTool(Tool):
    name = "process"
    description = ("Manage long-running background processes (dev servers, watchers). "
                  "actions: start(command) | list | poll(id) | log/logs(id) | "
                  "wait(id, timeout) | kill/stop(id) | write/submit/close(id).")
    groups = ["runtime"]
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "start", "list", "poll", "log", "logs", "wait", "kill", "stop",
                    "write", "submit", "close",
                ],
            },
            "command": {"type": "string"},
            "id": {"type": "string"},
            "session_id": {"type": "string"},
            "data": {"type": "string"},
            "timeout": {"type": "integer"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext) -> ToolResult:
        action = args["action"]
        if action == "start":
            if not args.get("command"):
                return ToolResult.error("start needs a command")
            agent = getattr(ctx, "agent", None)
            proc = process_registry.spawn_local(
                args["command"],
                cwd=ctx.cwd,
                task_id=getattr(ctx, "task_id", "") or "",
                notify_on_complete=True,
                watcher_platform=getattr(agent, "platform", "") or "",
                watcher_chat_id=getattr(agent, "chat_id", "") or "",
            )
            return ToolResult.ok(
                f"started {proc.id} (pid {proc.pid}): {args['command']} — "
                "you'll be notified on your next turn when it exits.",
                display=f"started {proc.id}",
                data={"session_id": proc.id, "pid": proc.pid},
            )
        if action == "list":
            rows = process_registry.list_sessions(task_id=getattr(ctx, "task_id", "") or None)
            if not rows:
                return ToolResult.ok("(no background processes)")
            lines = [
                (
                    f"{row['session_id']}  pid={row.get('pid')}  {row['status']}  "
                    f"{str(row.get('command', ''))[:50]}"
                )
                for row in rows
            ]
            return ToolResult.ok("\n".join(lines), display=f"{len(rows)} process(es)", data=rows)
        if action == "poll":
            result = process_registry.poll(_session_id(args))
            return _json_result(result, display=f"process {result.get('status', 'poll')}")
        if action in {"log", "logs"}:
            result = process_registry.read_log(
                _session_id(args),
                offset=int(args.get("offset", 0) or 0),
                limit=int(args.get("limit", 200) or 200),
            )
            if result.get("status") == "not_found":
                return ToolResult.error("unknown process id")
            return ToolResult.ok(result.get("output", "") or "(no output)",
                                 display="process logs", data=result)
        if action == "wait":
            result = process_registry.wait(_session_id(args), timeout=args.get("timeout"))
            return _json_result(result, display=f"process {result.get('status', 'wait')}")
        if action in {"kill", "stop"}:
            result = process_registry.kill_process(_session_id(args))
            if result.get("status") == "not_found":
                return ToolResult.error("unknown process id")
            return _json_result(result, display=str(result.get("status", "stopped")))
        if action == "write":
            result = process_registry.write_stdin(_session_id(args), str(args.get("data", "")))
            return _json_result(result, display=f"process {result.get('status', 'write')}")
        if action == "submit":
            result = process_registry.submit_stdin(_session_id(args), str(args.get("data", "")))
            return _json_result(result, display=f"process {result.get('status', 'submit')}")
        if action == "close":
            result = process_registry.close_stdin(_session_id(args))
            return _json_result(result, display=f"process {result.get('status', 'close')}")
        return ToolResult.error(f"unknown action {action}")


def _session_id(args: dict) -> str:
    raw = args.get("session_id", args.get("id", ""))
    return str(raw or "")


def _json_result(result: dict, *, display: str) -> ToolResult:
    is_error = result.get("status") in {"not_found", "error"}
    return ToolResult(
        content=json.dumps(result, indent=2),
        is_error=is_error,
        display=display,
        data=result,
    )


def process_tools() -> list[Tool]:
    return [ProcessTool()]
