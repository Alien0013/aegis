"""Agent state tools: sessions, traces, evals, and background work."""

from __future__ import annotations

import json
from typing import Any

from .base import Tool, ToolContext, ToolResult


class AgentStateTool(Tool):
    name = "agent_state"
    description = (
        "Inspect AEGIS runtime state that is shared across CLI, dashboard, API, ACP, "
        "gateway, and automation surfaces. Actions: current, list_sessions, session, "
        "branch, runs, run, traces, trace, evals, background."
    )
    toolset = "core"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "current", "list_sessions", "session", "branch",
                    "runs", "run", "traces", "trace", "evals", "background",
                ],
            },
            "id": {"type": "string", "description": "session, trace, eval, or background id"},
            "title": {"type": "string", "description": "title for branch"},
            "limit": {"type": "integer", "description": "max rows to return (default 10)"},
        },
        "required": ["action"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        action = str(args.get("action") or "current")
        limit = max(1, min(100, int(args.get("limit", 10) or 10)))
        if action == "current":
            return self._current(ctx)
        if action == "list_sessions":
            from ..session import SessionStore

            return _json(SessionStore().list(limit), f"{limit} session(s)")
        if action == "session":
            return self._session(str(args.get("id") or ""), limit)
        if action == "branch":
            return self._branch(str(args.get("id") or ""), str(args.get("title") or ""), ctx)
        if action == "runs":
            return self._runs(str(args.get("id") or ""), limit)
        if action == "run":
            return self._run_detail(str(args.get("id") or ""))
        if action == "traces":
            return self._traces(str(args.get("id") or ""), limit, ctx)
        if action == "trace":
            return self._trace(str(args.get("id") or ""), ctx)
        if action == "evals":
            return self._evals(str(args.get("id") or ""), limit, ctx)
        if action == "background":
            return self._background(str(args.get("id") or ""))
        return ToolResult.error(f"unknown action: {action}")

    def _current(self, ctx: ToolContext) -> ToolResult:
        session = ctx.session
        agent = ctx.agent
        trace_ctx = getattr(agent, "_trace_context", {}) or {}
        data = {
            "session_id": getattr(session, "id", ""),
            "title": getattr(session, "title", ""),
            "parent_id": getattr(session, "parent_id", None),
            "messages": len(getattr(session, "messages", []) or []),
            "todos": getattr(session, "todos", []) or [],
            "meta": getattr(session, "meta", {}) or {},
            "trace_id": trace_ctx.get("trace_id", ""),
            "turn_id": trace_ctx.get("turn_id", ""),
            "provider": getattr(getattr(agent, "provider", None), "name", ""),
            "model": getattr(getattr(agent, "provider", None), "model", ""),
            "tools_used": getattr(agent, "tools_used", 0),
        }
        return _json(data, "current state")

    def _session(self, session_id: str, limit: int) -> ToolResult:
        if not session_id:
            return ToolResult.error("session action requires id")
        from ..session import SessionStore

        session = SessionStore().load(session_id)
        if session is None:
            return ToolResult.error(f"session not found: {session_id}")
        messages = []
        for message in session.messages[-limit:]:
            messages.append({
                "role": message.role,
                "name": message.name,
                "content": _clip(message.content),
                "tool_calls": [tc.to_dict() for tc in message.tool_calls],
            })
        data = {
            "id": session.id,
            "title": session.title,
            "parent_id": session.parent_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "meta": session.meta,
            "todos": session.todos,
            "recent_messages": messages,
        }
        return _json(data, f"session {session.id[:12]}")

    def _branch(self, session_id: str, title: str, ctx: ToolContext) -> ToolResult:
        from ..session import SessionStore

        store = SessionStore()
        parent = store.load(session_id) if session_id else ctx.session
        if parent is None:
            return ToolResult.error(f"session not found: {session_id}")
        child = store.fork(parent)
        if title:
            child.title = title
            store.save(child)
        return _json(
            {"session_id": child.id, "title": child.title, "parent_id": child.parent_id},
            f"branched {child.id[:12]}",
        )

    def _runs(self, session_id: str, limit: int) -> ToolResult:
        from ..runs import RunStore

        rows = RunStore().list(session_id=session_id or None, limit=limit)
        return _json(rows, f"{len(rows)} run(s)")

    def _run_detail(self, run_id: str) -> ToolResult:
        if not run_id:
            return ToolResult.error("run action requires id")
        from ..runs import RunStore

        run = RunStore().get(run_id)
        if run is None:
            return ToolResult.error(f"run not found: {run_id}")
        return _json(run, f"run {run['id'][:12]}")

    def _traces(self, session_id: str, limit: int, ctx: ToolContext) -> ToolResult:
        from ..config import Config
        from ..tracing import TraceStore

        config = ctx.config or Config.load()
        rows = TraceStore.from_config(config).list_traces(session_id=session_id or None, limit=limit)
        return _json(rows, f"{len(rows)} trace(s)")

    def _trace(self, trace_id: str, ctx: ToolContext) -> ToolResult:
        if not trace_id:
            trace_id = str((getattr(ctx.agent, "_trace_context", {}) or {}).get("trace_id", ""))
        if not trace_id:
            return ToolResult.error("trace action requires id")
        from ..config import Config
        from ..tracing import TraceStore

        trace = TraceStore.from_config(ctx.config or Config.load()).get_trace(trace_id)
        if trace is None:
            return ToolResult.error(f"trace not found: {trace_id}")
        return _json(trace, f"trace {trace_id[:12]}")

    def _evals(self, run_id: str, limit: int, ctx: ToolContext) -> ToolResult:
        from ..config import Config
        from ..evals import EvalStore

        store = EvalStore.from_config(ctx.config or Config.load())
        if run_id:
            run = store.get_run(run_id)
            if run is None:
                return ToolResult.error(f"eval run not found: {run_id}")
            return _json(run, f"eval {run_id[:12]}")
        rows = store.list_runs(limit=limit)
        return _json(rows, f"{len(rows)} eval run(s)")

    def _background(self, task_id: str) -> ToolResult:
        from ..background import get_manager

        mgr = get_manager()
        if task_id:
            task = mgr.get(task_id)
            if task is None:
                return ToolResult.error(f"background task not found: {task_id}")
            return _json(task.__dict__, f"background {task.id[:12]}")
        rows = mgr.list()
        return _json(rows, f"{len(rows)} background task(s)")


def _clip(text: str, limit: int = 1200) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "\n...[truncated]"


def _json(data: Any, display: str) -> ToolResult:
    return ToolResult.ok(json.dumps(data, indent=2, sort_keys=True), display=display, data=data)


def state_tools() -> list[Tool]:
    return [AgentStateTool()]
