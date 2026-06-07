"""The bounded synchronous agent loop + concurrent tool executor."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..constants import MAX_PARALLEL_TOOLS
from ..tools.base import ToolContext, ToolResult
from ..types import Message, ToolCall
from . import compaction, governance

OnEvent = Callable[[dict], None]


class ToolExecutor:
    """Runs requested tool calls (concurrently), enforcing permissions per call."""

    def __init__(self, registry, permissions, ctx: ToolContext, on_event: OnEvent):
        self.registry = registry
        self.permissions = permissions
        self.ctx = ctx
        self.emit = on_event

    def _run_one(self, call: ToolCall) -> Message:
        self.emit({"type": "tool_start", "id": call.id, "name": call.name, "args": call.arguments})
        tool = self.registry.get(call.name)
        if tool is None:
            res = ToolResult.error(f"unknown tool '{call.name}'")
        else:
            allowed, reason = self.permissions.authorize(tool, call.arguments, self.ctx)
            if not allowed:
                res = ToolResult.error(f"permission denied: {reason}")
            else:
                try:
                    res = tool.run(call.arguments, self.ctx)
                except Exception as e:  # noqa: BLE001
                    res = ToolResult.error(f"tool raised {type(e).__name__}: {e}")
        self.emit({"type": "tool_result", "id": call.id, "name": call.name,
                   "summary": res.summary, "is_error": res.is_error})
        return Message.tool(call.id, call.name, res.content)

    def execute(self, calls: list[ToolCall]) -> list[Message]:
        if len(calls) == 1:
            return [self._run_one(calls[0])]
        # Preserve order in results while running concurrently.
        results: list[Message | None] = [None] * len(calls)
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_TOOLS, len(calls))) as pool:
            futures = {pool.submit(self._run_one, c): i for i, c in enumerate(calls)}
            for fut in futures:
                results[futures[fut]] = fut.result()
        return [r for r in results if r is not None]


def run_conversation(agent, on_event: OnEvent | None = None) -> Message:
    """Drive one user turn to completion. Returns the final assistant message."""
    emit = on_event or (lambda e: None)
    agent.ensure_system_prompt()
    session = agent.session
    budget = agent.budget
    budget.reset()

    available = agent.registry.available(agent.config.get("tools.toolsets", ["core"]))
    schemas = agent.registry.schemas(available)
    executor = ToolExecutor(agent.registry, agent.permissions, agent.tool_context, emit)

    while budget.should_continue():
        emit({"type": "iteration", "n": budget.api_call_count + 1, "max": budget.max_iterations})
        session.messages = governance.normalize(session.messages)

        def delta_cb(text: str) -> None:
            emit({"type": "assistant_delta", "text": text})

        try:
            resp = agent.provider.complete(
                session.messages, tools=schemas, stream=agent.stream, on_delta=delta_cb
            )
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            err = Message.assistant(f"[provider error] {e}")
            session.messages.append(err)
            return err

        budget.api_call_count += 1
        budget.usage.add(resp.usage)
        assistant_msg = resp.to_message()
        session.messages.append(assistant_msg)
        emit({"type": "assistant_message", "text": resp.text,
              "tool_calls": [tc.to_dict() for tc in resp.tool_calls]})

        if not resp.tool_calls:
            emit({"type": "final", "text": resp.text})
            return assistant_msg

        results = executor.execute(resp.tool_calls)
        session.messages.extend(results)
        agent.tools_used += len(resp.tool_calls)

        if compaction.should_compress(session.messages, agent.provider.context_length):
            emit({"type": "compacting"})
            comp = agent.config.get("agent.compression", {}) or {}
            session.messages = compaction.compress(
                session.messages, agent.provider,
                preserve_first=comp.get("preserve_first", 3),
                preserve_last=comp.get("preserve_last", 20),
            )
            agent.refresh_volatile()

    # Budget exhausted -> one grace call without tools for a final summary.
    emit({"type": "budget_exhausted"})
    session.messages.append(
        Message.user("You've reached the step limit. Stop and summarize what you accomplished "
                     "and what remains.")
    )
    try:
        grace = agent.provider.complete(session.messages, tools=None, stream=agent.stream,
                                        on_delta=lambda t: emit({"type": "assistant_delta", "text": t}))
        gm = grace.to_message()
    except Exception as e:  # noqa: BLE001
        gm = Message.assistant(f"[step limit reached; summary failed: {e}]")
    session.messages.append(gm)
    emit({"type": "final", "text": gm.content})
    return gm
