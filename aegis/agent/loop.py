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


def _provider_complete(provider, messages, *, tools=None, **kwargs):
    """Call provider.complete without breaking older Provider-compatible fakes/plugins."""
    import inspect

    try:
        params = inspect.signature(provider.complete).parameters
    except (TypeError, ValueError):
        return provider.complete(messages, tools=tools, **kwargs)
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if accepts_kwargs:
        return provider.complete(messages, tools=tools, **kwargs)
    filtered = {k: v for k, v in kwargs.items() if k in params}
    return provider.complete(messages, tools=tools, **filtered)


class ToolExecutor:
    """Runs requested tool calls (concurrently), enforcing permissions per call."""

    def __init__(self, registry, permissions, ctx: ToolContext, on_event: OnEvent):
        self.registry = registry
        self.permissions = permissions
        self.ctx = ctx
        self.emit = on_event

    def _run_hooks(self, event: str, context: dict) -> None:
        cfg = getattr(self.ctx, "config", None)
        if cfg is None:
            return
        try:
            from ..hooks import run_hooks
            run_hooks(cfg, event, context)
        except Exception:  # noqa: BLE001
            pass

    def _maybe_checkpoint(self, call: ToolCall) -> None:
        cfg = getattr(self.ctx, "config", None)
        if cfg is None or not cfg.get("checkpoints.enabled", False):
            return
        if call.name not in ("write_file", "edit_file"):
            return
        path = call.arguments.get("path")
        if not path:
            return
        try:
            from ..checkpoints import CheckpointStore
            CheckpointStore(self.ctx.cwd).snapshot([path], label=call.name)
        except Exception:  # noqa: BLE001
            pass

    def execute_one_raw(self, call: ToolCall) -> ToolResult:
        self.emit({"type": "tool_start", "id": call.id, "name": call.name, "args": call.arguments})
        self._run_hooks("pre_tool", {"tool": call.name, "args": str(call.arguments)[:300]})
        self._maybe_checkpoint(call)
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
                   "summary": res.summary, "is_error": res.is_error,
                   "classification": res.classification})
        self._run_hooks("post_tool", {"tool": call.name, "is_error": str(res.is_error)})
        return res

    def _run_one(self, call: ToolCall) -> Message:
        res = self.execute_one_raw(call)
        content = res.content
        # Wrap results from external/untrusted sources so the model treats them as DATA,
        # not instructions (prompt-injection defense).
        tool = self.registry.get(call.name)
        is_untrusted = call.name.startswith("mcp__") or (tool and "network" in tool.groups)
        if content and not res.is_error and is_untrusted:
            content = (f'<untrusted_tool_result source="{call.name}">\n{content}\n'
                       f"</untrusted_tool_result>")
        return Message.tool(call.id, call.name, content)

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
    continuations = 0
    from ..util import estimate_tokens
    schema_tokens = estimate_tokens(json.dumps(schemas))   # tools count toward the window

    while budget.should_continue():
        emit({"type": "iteration", "n": budget.api_call_count + 1, "max": budget.max_iterations})
        session.messages = governance.normalize(session.messages)

        def delta_cb(text: str) -> None:
            emit({"type": "assistant_delta", "text": text})

        try:
            resp = _provider_complete(
                agent.provider, session.messages, tools=schemas, stream=agent.stream, on_delta=delta_cb,
                reasoning=getattr(agent, "reasoning", "off"),
                tool_runner=executor.execute_one_raw,
                approver=getattr(agent.tool_context, "approver", None),
                cwd=agent.cwd,
            )
        except Exception as e:  # noqa: BLE001
            from .._log import log_exc
            log_exc("provider.complete failed")
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
            # Auto-continue a response truncated by the output token limit (up to 3x).
            if resp.finish_reason in ("length", "max_tokens") and continuations < 3:
                continuations += 1
                emit({"type": "continuation", "n": continuations})
                session.messages.append(
                    Message.user("Continue exactly where you left off. Do not repeat or re-introduce."))
                continue
            from ..constants import SKILL_AUTOGEN_THRESHOLD
            if (agent.tools_used >= SKILL_AUTOGEN_THRESHOLD
                    and agent.config.get("skills.autogen", True)
                    and not agent.session.meta.get("nudged")):
                agent.session.meta["nudged"] = True
                emit({"type": "skill_nudge"})
            emit({"type": "final", "text": resp.text})
            return assistant_msg

        results = executor.execute(resp.tool_calls)
        session.messages.extend(results)
        agent.tools_used += len(resp.tool_calls)

        # Incremental persist so a crash mid-turn doesn't lose progress.
        if agent.store is not None:
            try:
                agent.store.save(session)
            except Exception:  # noqa: BLE001
                pass

        if compaction.should_compress(session.messages, agent.provider.context_length, schema_tokens):
            emit({"type": "compacting"})
            comp = agent.config.get("agent.compression", {}) or {}
            session.messages = compaction.compress(
                session.messages, agent.provider,
                preserve_first=comp.get("preserve_first", 3),
                preserve_last=comp.get("preserve_last", 20),
                max_tool_tokens=comp.get("max_tool_tokens", 600),
            )
            agent.refresh_volatile()

    # Budget exhausted -> one grace call without tools for a final summary.
    emit({"type": "budget_exhausted"})
    session.messages.append(
        Message.user("You've reached the step limit. Stop and summarize what you accomplished "
                     "and what remains.")
    )
    try:
        grace = _provider_complete(
            agent.provider,
            session.messages,
            tools=None,
            stream=agent.stream,
            on_delta=lambda t: emit({"type": "assistant_delta", "text": t}),
        )
        gm = grace.to_message()
    except Exception as e:  # noqa: BLE001
        gm = Message.assistant(f"[step limit reached; summary failed: {e}]")
    session.messages.append(gm)
    emit({"type": "final", "text": gm.content})
    return gm
