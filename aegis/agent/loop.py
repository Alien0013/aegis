"""The bounded synchronous agent loop + concurrent tool executor."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..constants import MAX_PARALLEL_TOOLS
from ..tools.base import ToolContext, ToolResult
from ..types import Message, ToolCall, new_id
from . import compaction, governance

OnEvent = Callable[[dict], None]


def _without_thinking(m: Message) -> Message:
    """A shallow copy of ``m`` with thinking blocks/reasoning removed — for the
    thinking-signature 400 retry. Non-assistant or block-free messages pass through
    unchanged (identity) so unaffected turns aren't needlessly copied."""
    if m.role != "assistant" or not (getattr(m, "thinking_blocks", None) or m.reasoning):
        return m
    import dataclasses
    return dataclasses.replace(m, thinking_blocks=[], reasoning="")


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


def _preview(text: str, limit: int = 500) -> str:
    one_line = (text or "").replace("\r", "").strip()
    if len(one_line) <= limit:
        return one_line
    return one_line[:limit].rstrip() + " …"


def _artifact_ref(data) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("artifact_ref", "artifact", "path", "file", "url"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def _prompt_trace_meta(session) -> dict:
    meta = getattr(session, "meta", {}) or {}
    return {
        "system_prompt_hash": meta.get("system_prompt_hash", ""),
        "system_prompt_tokens": meta.get("system_prompt_tokens", 0),
        "system_prompt_chars": meta.get("system_prompt_chars", 0),
        "prompt_parts": list(meta.get("prompt_parts") or []),
    }


def _trace_scalar(value) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw)


def _provider_trace_data(agent, wire_messages, schemas, response_state: dict, prompt_meta: dict) -> dict:
    api_mode = getattr(agent.provider, "api_mode", "")
    tool_names = [str(t.get("name") or "") for t in (schemas or []) if isinstance(t, dict)]
    return {
        "iteration": agent.budget.api_call_count + 1,
        "system_prompt_hash": prompt_meta.get("system_prompt_hash", ""),
        "transport": type(agent.provider).__name__,
        "api_mode": _trace_scalar(api_mode),
        "context_length": int(getattr(agent.provider, "context_length", 0) or 0),
        "message_count": len(wire_messages or []),
        "tool_schema_count": len(schemas or []),
        "tool_schema_names": [name for name in tool_names if name],
        "stream": bool(getattr(agent, "stream", False)),
        "reasoning": getattr(agent, "reasoning", "off"),
        "responses_state": {
            "enabled": bool(response_state.get("enabled")),
            "store": bool(response_state.get("store")),
            "send_previous": bool(response_state.get("send_previous", True)),
            "preserve_items": bool(response_state.get("preserve_items", True)),
            "context_management": bool(response_state.get("context_management") or response_state.get("compaction")),
            "previous_response_id": str(response_state.get("previous_response_id") or ""),
            "provider": str(response_state.get("provider") or ""),
            "model": str(response_state.get("model") or ""),
            "previous_response_skipped": str(response_state.get("previous_response_skipped") or ""),
        },
    }


def _responses_state_matches(prev, *, provider: str = "", model: str = "") -> bool:
    prev_provider = str(getattr(prev, "provider", "") or "")
    prev_model = str(getattr(prev, "model", "") or "")
    provider = str(provider or "")
    model = str(model or "")
    if prev_provider and provider and prev_provider != provider:
        return False
    if prev_model and model and prev_model != model:
        return False
    return True


def _hydrate_previous_response_id(
    session_id: str,
    response_state: dict,
    *,
    provider: str = "",
    model: str = "",
) -> dict:
    state = dict(response_state or {})
    if not (state.get("enabled") and state.get("store") and state.get("send_previous", True) and session_id):
        return state
    if state.get("previous_response_id"):
        return state
    try:
        from ..responses_state import ResponsesStateStore

        prev = ResponsesStateStore().get(session_id)
        if prev and prev.response_id and _responses_state_matches(prev, provider=provider, model=model):
            state["previous_response_id"] = prev.response_id
        elif prev and prev.response_id:
            state["previous_response_skipped"] = "provider_or_model_changed"
    except Exception:  # noqa: BLE001
        pass
    return state


def _response_state_for_agent(agent, session_id: str) -> dict:
    response_state = dict(agent.config.get("responses.state", {}) or {})
    response_state["provider"] = str(getattr(agent.provider, "name", "") or "")
    response_state["model"] = str(getattr(agent.provider, "model", "") or "")
    native_compaction = dict(agent.config.get("responses.compaction", {}) or {})
    if native_compaction.get("enabled"):
        response_state["context_management"] = _responses_context_management(agent, native_compaction)
    return _hydrate_previous_response_id(
        session_id,
        response_state,
        provider=response_state.get("provider", ""),
        model=response_state.get("model", ""),
    )


def _responses_context_management(agent, native_compaction: dict) -> list[dict[str, int | str]]:
    raw = native_compaction.get("compact_threshold_tokens",
                                native_compaction.get("compact_threshold", 0.85))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.85
    if 0 < value < 1:
        context_length = int(getattr(getattr(agent, "provider", None), "context_length", 0) or 0)
        threshold = int(context_length * value) if context_length else 1000
    else:
        threshold = int(value)
    threshold = max(1000, threshold)
    return [{"type": "compaction", "compact_threshold": threshold}]


def _provider_metadata(agent) -> dict[str, str]:
    session = getattr(agent, "session", None)
    trace_ctx = getattr(agent, "_trace_context", None) or {}
    data = {
        "session_id": getattr(session, "id", ""),
        "trace_id": trace_ctx.get("trace_id", ""),
        "turn_id": trace_ctx.get("turn_id", ""),
        "run_id": getattr(agent, "_surface_run_id", ""),
    }
    return {key: str(value) for key, value in data.items() if value}


def _record_response_request_meta(session, response_state: dict) -> None:
    if session is None:
        return
    meta = {
        "enabled": bool(response_state.get("enabled")),
        "store": bool(response_state.get("store")),
        "send_previous": bool(response_state.get("send_previous", True)),
        "previous_response_id": str(response_state.get("previous_response_id") or ""),
    }
    try:
        session.meta["response_state_request"] = meta
    except Exception:  # noqa: BLE001
        pass


def _response_trace_data(resp, duration_ms: int) -> dict:
    raw = resp.raw if isinstance(resp.raw, dict) else {}
    output = raw.get("output") if isinstance(raw.get("output"), list) else []
    data: dict[str, Any] = {
        "finish_reason": resp.finish_reason,
        "input_tokens": getattr(resp.usage, "input_tokens", 0),
        "output_tokens": getattr(resp.usage, "output_tokens", 0),
        "tool_calls": len(resp.tool_calls),
        "duration_ms": duration_ms,
    }
    if raw:
        data.update({
            "response_id": raw.get("id", ""),
            "response_status": raw.get("status", ""),
            "output_item_count": len(output),
            "output_item_types": [str(item.get("type") or "") for item in output if isinstance(item, dict)],
        })
    return data


def _usage_cost_usd(model: str, usage, config) -> float:
    try:
        from ..usage_log import _price
        pin, pout = _price(model, config)
        cache_read = int(getattr(usage, "cache_read", 0) or 0)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        fresh_in = max(0, input_tokens - cache_read)
        return round((fresh_in * pin + cache_read * pin * 0.1 + output_tokens * pout) / 1_000_000, 6)
    except Exception:  # noqa: BLE001
        return 0.0


def _trace_compaction(agent, session, rec: dict) -> None:
    trace_store = getattr(agent, "_trace_store", None)
    trace_ctx = getattr(agent, "_trace_context", None) or {}
    if not (trace_store and trace_ctx):
        return
    try:
        summarizer = _summarizer(agent)
        span = trace_store.start_span(
            trace_id=trace_ctx.get("trace_id"),
            session_id=trace_ctx.get("session_id", getattr(session, "id", "")),
            turn_id=trace_ctx.get("turn_id", ""),
            parent_span_id=trace_ctx.get("turn_span_id", ""),
            kind="compaction",
            provider=getattr(summarizer, "name", ""),
            model=getattr(summarizer, "model", ""),
            data=dict(rec),
        )
        trace_store.finish_span(span["span_id"], status="ok", data=dict(rec))
    except Exception:  # noqa: BLE001
        pass


class ToolExecutor:
    """Runs requested tool calls (concurrently), enforcing permissions per call."""

    def __init__(self, registry, permissions, ctx: ToolContext, on_event: OnEvent,
                 guard=None):
        self.registry = registry
        self.permissions = permissions
        self.ctx = ctx
        self.emit = on_event
        self.guard = guard          # per-turn ToolLoopGuard (None in bare/test usage)
        self._turn_checkpoint: str | None = None   # one checkpoint per turn's edit batch

    def _run_hooks(self, event: str, context: dict) -> None:
        cfg = getattr(self.ctx, "config", None)
        if cfg is None:
            return
        try:
            from ..hooks import run_hooks
            run_hooks(cfg, event, context)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _edit_paths(call: ToolCall) -> list[str]:
        """File paths a mutating tool call is about to touch ([] for non-edits)."""
        if call.name in ("write_file", "edit_file"):
            p = call.arguments.get("path")
            return [p] if p else []
        if call.name == "apply_patch":
            import re
            return re.findall(r"^\+\+\+ (?:b/)?(\S+)", call.arguments.get("patch", "") or "",
                              re.MULTILINE)
        return []

    def _maybe_checkpoint(self, call: ToolCall) -> None:
        """Auto-checkpoint each turn's edit batch: the first edit of the turn opens a
        checkpoint (pre-turn state); later edits join it. /rollback undoes the batch,
        `aegis checkpoints diff` previews it."""
        cfg = getattr(self.ctx, "config", None)
        if cfg is None or not cfg.get("checkpoints.enabled", True):
            return
        paths = self._edit_paths(call)
        if not paths:
            return
        try:
            from ..checkpoints import CheckpointStore
            store = CheckpointStore(self.ctx.cwd)
            if self._turn_checkpoint:
                store.add_to(self._turn_checkpoint, paths)
            else:
                self._turn_checkpoint = store.snapshot(paths, label=f"turn edits ({call.name})")
        except Exception:  # noqa: BLE001
            pass

    def execute_one_raw(self, call: ToolCall) -> ToolResult:
        import time
        started = time.perf_counter()
        self.emit({"type": "tool_start", "id": call.id, "name": call.name, "args": call.arguments})
        trace_span = None
        trace_store = getattr(getattr(self.ctx, "agent", None), "_trace_store", None)
        trace_ctx = getattr(getattr(self.ctx, "agent", None), "_trace_context", None) or {}
        if trace_store and trace_ctx:
            try:
                trace_span = trace_store.start_span(
                    trace_id=trace_ctx.get("trace_id"),
                    session_id=trace_ctx.get("session_id", ""),
                    turn_id=trace_ctx.get("turn_id", ""),
                    parent_span_id=trace_ctx.get("turn_span_id", ""),
                    kind="tool",
                    tool_name=call.name,
                    data={"args": call.arguments},
                )
            except Exception:  # noqa: BLE001
                trace_span = None
        self._run_hooks("pre_tool", {"tool": call.name, "args": str(call.arguments)[:300]})
        self._maybe_checkpoint(call)
        blocked = self.guard.check(call.name, call.arguments) if self.guard else None
        tool = self.registry.get(call.name)
        if blocked:
            res = ToolResult.error(blocked)        # loop guard: don't run it again
        elif tool is None:
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
        if self.guard and not blocked:
            warn = self.guard.record(call.name, call.arguments, res.content, res.is_error)
            if warn:
                res.content = (res.content or "") + "\n\n" + warn
        duration_ms = int((time.perf_counter() - started) * 1000)
        artifact_ref = _artifact_ref(res.data)
        self.emit({"type": "tool_result", "id": call.id, "name": call.name,
                   "summary": res.summary, "is_error": res.is_error,
                   "classification": res.classification,
                   "preview": _preview(res.content),
                   "duration_ms": duration_ms,
                   "artifact_ref": artifact_ref,
                   "data": res.data if isinstance(res.data, dict) else None})
        if trace_store and trace_span:
            try:
                span_data = {
                    "summary": res.summary,
                    "classification": res.classification,
                    "preview": _preview(res.content),
                    "is_error": bool(res.is_error),
                    "duration_ms": duration_ms,
                    "artifact_ref": artifact_ref,
                }
                if isinstance(res.data, dict):
                    span_data["result"] = res.data
                trace_store.finish_span(
                    trace_span["span_id"],
                    status="error" if res.is_error else "ok",
                    data=span_data,
                    artifact_ref=artifact_ref,
                )
            except Exception:  # noqa: BLE001
                pass
        self._run_hooks("post_tool", {"tool": call.name, "is_error": str(res.is_error)})
        return res

    def _maybe_spill(self, call: ToolCall, content: str, is_error: bool) -> str:
        """Spill an oversized tool output to disk; return a preview + reference path so
        a single huge result can't blow the context window (the agent can read_file it)."""
        cfg_obj = getattr(self.ctx, "config", None)
        if is_error or not content or cfg_obj is None:
            return content
        from ..util import estimate_tokens
        limit = int(cfg_obj.get("tools.max_result_tokens", 4000) or 0)
        if limit <= 0 or estimate_tokens(content) <= limit:
            return content
        import os
        import time
        from .. import config as cfg
        d = cfg.sub("tool_outputs")
        try:
            os.makedirs(d, exist_ok=True)
            cutoff = time.time() - 7 * 86400          # spills are scratch — prune old ones
            for old in os.listdir(d):
                p = os.path.join(d, old)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.unlink(p)
                except OSError:
                    continue
            path = os.path.join(d, f"{call.name}_{call.id}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:  # noqa: BLE001
            return content
        head = content[: limit * 4].rstrip()
        return (f"{head}\n\n…[output truncated to protect context; full {len(content):,} "
                f"chars saved to {path} — use read_file to inspect specific parts]")

    def _run_one(self, call: ToolCall) -> Message:
        res = self.execute_one_raw(call)
        content = self._maybe_spill(call, res.content, res.is_error)
        # Wrap results from external/untrusted sources so the model treats them as DATA,
        # not instructions (prompt-injection defense).
        tool = self.registry.get(call.name)
        is_untrusted = call.name.startswith("mcp__") or (tool and "network" in tool.groups)
        if content and not res.is_error and is_untrusted:
            content = (f'<untrusted_tool_result source="{call.name}">\n{content}\n'
                       f"</untrusted_tool_result>")
        # Subdirectory hints: local rule files for any new directory this call entered.
        if not res.is_error:
            try:
                from .subdir_hints import hints_for_call
                hint = hints_for_call(getattr(self.ctx, "agent", None), call.name,
                                      call.arguments, self.ctx.cwd)
                if hint:
                    content = (content or "") + hint
            except Exception:  # noqa: BLE001
                pass
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


def _next_in_lineage(title: str) -> str:
    """Auto-number a continuation session title: 'Task' -> 'Task (2)' -> 'Task (3)'."""
    import re
    m = re.match(r"^(.*?) \((\d+)\)$", title or "")
    if m:
        return f"{m.group(1)} ({int(m.group(2)) + 1})"
    return f"{(title or 'session').strip()} (2)"


def _summarizer(agent):
    """Provider used for compaction summaries — the cheap auxiliary model when configured,
    else the main provider. Built once and cached on the agent."""
    try:
        from ..auxiliary import router_for
        return router_for(agent).provider_for("compaction")
    except Exception:  # noqa: BLE001 - never block compaction on aux setup
        return agent.provider


def _drain_steering(agent, session) -> None:
    """Fold any guidance queued via ``agent.steer()`` into the conversation before the next
    model call — appended to the last tool message to preserve role alternation."""
    q = getattr(agent, "steer_queue", None)
    if q is None:
        return
    notes = []
    while not q.empty():
        try:
            notes.append(q.get_nowait())
        except Exception:  # noqa: BLE001
            break
    if not notes:
        return
    text = "\n".join(f"[user steering]: {n}" for n in notes)
    for m in reversed(session.messages):
        if m.role == "tool":
            m.content = (m.content or "") + "\n\n" + text
            return
    session.messages.append(Message.user(text))


def _tail_token_budget(agent, comp: dict, *, tight: bool = False) -> int:
    """Tokens of recent conversation to protect during compaction — a fraction of the
    model's window so the tail scales with the model, not a fixed message count. The
    overflow-recovery path uses a tighter fraction so the request actually shrinks."""
    ctx = getattr(agent.provider, "context_length", 0) or 0
    frac = comp.get("tail_fraction", 0.25)
    if tight:
        frac = min(frac, 0.12)
    return int(ctx * frac) if ctx > 0 else (3000 if tight else 6000)


def _force_compact(agent, session):
    """Compress unconditionally — recovery from a provider context_overflow. Tighter tail than
    the proactive path so the request actually shrinks below the window."""
    comp = agent.config.get("agent.compression", {}) or {}
    before_n = len(session.messages)
    before_tok = compaction.estimated_tokens(session.messages)
    session.messages = governance.normalize(_engine(agent).compress(
        session.messages, _summarizer(agent),
        preserve_first=comp.get("preserve_first", 3),
        tail_tokens=_tail_token_budget(agent, comp, tight=True),
        max_tool_tokens=min(400, comp.get("max_tool_tokens", 600)),
    ))
    after_tok = compaction.estimated_tokens(session.messages)
    from ..util import now_iso
    _trace_compaction(agent, session, {
        "at": now_iso(),
        "iteration": getattr(agent.budget, "api_call_count", 0),
        "messages_before": before_n,
        "messages_after": len(session.messages),
        "tokens_before": before_tok,
        "tokens_after": after_tok,
        "reason": "context_overflow",
        "recovery": True,
    })
    agent.refresh_volatile()
    return session


def _engine(agent):
    """The active context engine for this agent (cached)."""
    e = getattr(agent, "_context_engine", None)
    if e is None:
        from .context_engine import get_engine
        e = get_engine(agent.config)
        agent._context_engine = e
    return e


def _maybe_compact(agent, session, schema_tokens: int, budget, emit):
    """Proactively compact BEFORE the next model call if the window is near full. Returns the
    (possibly new child) session. Splits into a child session when a store + split are enabled."""
    # Once a compaction can't meaningfully shrink the window (e.g. the preserved tail itself
    # exceeds the threshold), stop retrying this turn — otherwise we'd burn a model call on a
    # no-op summary every iteration until the budget is exhausted.
    if getattr(agent, "_compact_stuck", False):
        return session
    engine = _engine(agent)
    if not engine.should_compress(session.messages, agent.provider.context_length, schema_tokens):
        return session
    emit({"type": "compacting"})
    comp = agent.config.get("agent.compression", {}) or {}
    if agent.memory:                       # flush memory so the summary reflects latest facts
        try:
            agent.memory.refresh_snapshot()
            agent.memory.on_pre_compress(session.messages)   # provider pre-compression hook
        except Exception:  # noqa: BLE001
            pass
    try:
        from .context_engine import call_hook
        call_hook(engine, "on_pre_compress", agent, session)
    except Exception:  # noqa: BLE001
        pass
    before_n = len(session.messages)
    before_tok = compaction.estimated_tokens(session.messages)
    compressed = engine.compress(
        session.messages, _summarizer(agent),    # summarize on the cheap aux model, not the main one
        preserve_first=comp.get("preserve_first", 3),
        tail_tokens=_tail_token_budget(agent, comp),   # token-budgeted tail, scales with the window
        max_tool_tokens=comp.get("max_tool_tokens", 600),
    )
    after_tok = compaction.estimated_tokens(compressed)
    # If we're STILL over the threshold after compacting, the preserved tail is the floor —
    # further compaction can't help, so don't retry this turn (avoids per-iteration thrash).
    still_over = engine.should_compress(compressed, agent.provider.context_length, schema_tokens)
    from ..constants import COMPACT_THRESHOLD
    from ..util import now_iso
    rec = {"at": now_iso(), "iteration": budget.api_call_count,
           "messages_before": before_n, "messages_after": len(compressed),
           "tokens_before": before_tok, "tokens_after": after_tok,
           "reason": f"context exceeded {int(COMPACT_THRESHOLD * 100)}% of the window"}
    if still_over:
        agent._compact_stuck = True
        rec["stuck"] = True

    if not still_over and comp.get("split_sessions", True) and agent.store is not None \
            and len(compressed) < before_n:
        from ..session import Session
        parent = session
        child = Session.create(title=_next_in_lineage(parent.title), parent_id=parent.id)
        child.messages = compressed
        depth = int(parent.meta.get("lineage_depth", 0) or 0) + 1
        root = parent.meta.get("lineage_root") or parent.parent_id or parent.id
        parent.meta["end_reason"] = "compression"
        parent.meta.setdefault("child_sessions", []).append(child.id)
        child.meta["forked_from"] = parent.id
        child.meta["lineage_root"] = root
        child.meta["lineage_depth"] = depth
        child.meta["creator_kind"] = "compression"
        child.meta["reason"] = "context_compaction"
        child.meta["compression_depth"] = depth
        child.meta["parent_end_reason"] = "compression"
        child.meta["summary"] = parent.meta.get("summary", "")
        child.meta.setdefault("compactions", []).append(rec)
        try:
            agent.store.save(parent)                    # preserve full parent history + end reason
            agent.store.save(child)                     # make continuation visible immediately
        except Exception:  # noqa: BLE001
            pass
        agent.switch_session(child)            # fires the memory session-switch hook
        session = child
        rec = {**rec, "split": True, "child_session": child.id, "parent_session": parent.id}
        try:
            from .context_engine import call_hook
            call_hook(engine, "on_session_switch", agent, parent, child, reason="compression")
        except Exception:  # noqa: BLE001
            pass
    else:
        session.messages = compressed
        session.meta.setdefault("compactions", []).append(rec)
    _trace_compaction(agent, session, rec)
    emit({"type": "compacted", **rec})
    agent.refresh_volatile()
    return session


def compact_now(agent, session=None, emit: OnEvent | None = None, *,
                reason: str = "manual", focus: str = "", preserve_last: int | None = None):
    """Force a user-requested compaction through the same context-engine lifecycle
    used by automatic compaction. Returns the active session, which may be a child
    session when split compaction is enabled."""
    emit = emit or (lambda _e: None)
    session = session or agent.session
    engine = _engine(agent)
    emit({"type": "compacting", "reason": reason})
    comp = agent.config.get("agent.compression", {}) or {}
    if agent.memory:
        try:
            agent.memory.refresh_snapshot()
        except Exception:  # noqa: BLE001
            pass
    try:
        from .context_engine import call_hook
        call_hook(engine, "on_pre_compress", agent, session)
    except Exception:  # noqa: BLE001
        pass

    before_n = len(session.messages)
    before_tok = compaction.estimated_tokens(session.messages)
    kwargs = {
        "preserve_first": comp.get("preserve_first", 3),
        "max_tool_tokens": comp.get("max_tool_tokens", 600),
    }
    if preserve_last is not None:
        kwargs["preserve_last"] = preserve_last
    else:
        kwargs["tail_tokens"] = _tail_token_budget(agent, comp)
    if focus:
        kwargs["focus"] = focus
    compressed = governance.normalize(engine.compress(session.messages, _summarizer(agent), **kwargs))
    after_tok = compaction.estimated_tokens(compressed)

    from ..util import now_iso
    rec = {"at": now_iso(), "iteration": getattr(agent.budget, "api_call_count", 0),
           "messages_before": before_n, "messages_after": len(compressed),
           "tokens_before": before_tok, "tokens_after": after_tok,
           "reason": reason, "manual": True}
    if focus:
        rec["focus"] = focus

    if comp.get("split_sessions", True) and agent.store is not None and len(compressed) < before_n:
        from ..session import Session
        parent = session
        child = Session.create(title=_next_in_lineage(parent.title), parent_id=parent.id)
        child.messages = compressed
        depth = int(parent.meta.get("lineage_depth", 0) or 0) + 1
        root = parent.meta.get("lineage_root") or parent.parent_id or parent.id
        parent.meta["end_reason"] = "manual_compression"
        parent.meta.setdefault("child_sessions", []).append(child.id)
        child.meta["forked_from"] = parent.id
        child.meta["lineage_root"] = root
        child.meta["lineage_depth"] = depth
        child.meta["creator_kind"] = "manual_compression"
        child.meta["reason"] = reason
        child.meta["compression_depth"] = depth
        child.meta["parent_end_reason"] = "manual_compression"
        child.meta["summary"] = parent.meta.get("summary", "")
        child.meta.setdefault("compactions", []).append(rec)
        try:
            agent.store.save(parent)
            agent.store.save(child)
        except Exception:  # noqa: BLE001
            pass
        agent.switch_session(child)            # fires the memory session-switch hook
        session = child
        rec = {**rec, "split": True, "child_session": child.id, "parent_session": parent.id}
        try:
            from .context_engine import call_hook
            call_hook(engine, "on_session_switch", agent, parent, child, reason="manual_compression")
        except Exception:  # noqa: BLE001
            pass
    else:
        session.messages = compressed
        session.meta.setdefault("compactions", []).append(rec)
        if agent.store is not None:
            try:
                agent.store.save(session)
            except Exception:  # noqa: BLE001
                pass
    _trace_compaction(agent, session, rec)
    emit({"type": "compacted", **rec})
    agent.refresh_volatile()
    return session


def run_conversation(agent, on_event: OnEvent | None = None) -> Message:
    """Drive one user turn to completion. Returns the final assistant message."""
    emit = on_event or (lambda e: None)
    # Memory freshness policy (memory.refresh):
    #   "session" (default) / "message" — if memory files changed since the last
    #     prompt snapshot, rebuild at the next turn so durable facts are visible.
    #   "frozen" / "never" — keep the prompt prefix fixed until an explicit
    #     refresh/rebuild path such as /new, compaction, or a new process.
    refresh_mode = (agent.config.get("memory.refresh", "session") or "session")
    memory_stale = (
        refresh_mode not in {"frozen", "never"}
        and agent.memory is not None
        and agent.memory.is_stale()
    )
    skills_stale = bool(
        getattr(agent, "skills", None) is not None
        and getattr(agent.skills, "is_stale", lambda: False)()
    )
    if memory_stale or skills_stale:
        agent.refresh_volatile()
    else:
        agent.ensure_system_prompt()
    session = agent.session
    budget = agent.budget
    budget.reset()
    trace_store = None
    turn_span = None
    trace_id = new_id("trace")
    turn_id = new_id("turn")
    prompt_meta = _prompt_trace_meta(session)
    from ..tracing import should_trace
    if should_trace(agent.config, trace_id):
        try:
            from ..tracing import TraceStore
            trace_store = TraceStore.from_config(agent.config)
            turn_span = trace_store.start_span(
                trace_id=trace_id,
                session_id=session.id,
                turn_id=turn_id,
                kind="turn",
                provider=getattr(agent.provider, "name", ""),
                model=getattr(agent.provider, "model", ""),
                data={"prompt": prompt_meta},
            )
            agent._trace_store = trace_store
            agent._trace_context = {
                "trace_id": trace_id,
                "turn_id": turn_id,
                "session_id": session.id,
                "turn_span_id": turn_span["span_id"],
            }
        except Exception:  # noqa: BLE001
            trace_store = None
    else:
        trace_id = ""
        agent._trace_store = None
        agent._trace_context = {}

    def _finish_turn(status: str = "ok", **updates) -> None:
        if trace_store and turn_span:
            try:
                trace_store.finish_span(turn_span["span_id"], status=status, **updates)
            except Exception:  # noqa: BLE001
                pass

    available = agent.registry.available(agent.config.get("tools.toolsets", ["core"]))

    def _live_schemas():
        """Schemas for this iteration — deferred tools ship name-only (system-prompt
        index) until tool_search activates them, then their schemas join the wire."""
        deferred = agent.deferred_tool_names(available) if hasattr(agent, "deferred_tool_names") else set()
        return agent.registry.schemas([t for t in available if t.name not in deferred])

    schemas = _live_schemas()
    from .guardrails import ToolLoopGuard
    guard = ToolLoopGuard(
        warn_after=int(agent.config.get("tools.loop_warn_after", 3)),
        block_after=int(agent.config.get("tools.loop_block_after", 5)),
    )
    executor = ToolExecutor(agent.registry, agent.permissions, agent.tool_context, emit, guard)
    continuations = 0
    empty_nudges = 0
    from ..util import estimate_tokens
    schema_tokens = estimate_tokens(json.dumps(schemas))   # tools count toward the window

    cancel = getattr(agent, "cancel_event", None)

    def _cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    while budget.should_continue():
        if _cancelled():
            emit({"type": "cancelled"})
            stop = Message.assistant("[interrupted by user]")
            session.messages.append(stop)
            _finish_turn("cancelled")
            return stop
        emit({"type": "iteration", "n": budget.api_call_count + 1, "max": budget.max_iterations})
        _drain_steering(agent, session)        # fold in any mid-run /steer guidance
        if len(fresh := _live_schemas()) != len(schemas):   # tool_search activated a deferred tool
            schemas = fresh
            schema_tokens = estimate_tokens(json.dumps(schemas))
        # Compact BEFORE the model call so an over-full window never reaches the provider,
        # then normalize AFTER so a compaction boundary can never ship a broken tool pair.
        session = _maybe_compact(agent, session, schema_tokens, budget, emit)
        session.messages = governance.normalize(session.messages)
        prompt_meta = _prompt_trace_meta(session)
        from ..plugins import fire_hook
        rewritten = fire_hook("pre_llm_call", session.messages, agent)   # in-process Python hook
        if isinstance(rewritten, list):
            session.messages = rewritten

        def delta_cb(text: str) -> None:
            emit({"type": "assistant_delta", "text": text})

        reasoned_live = {"v": False}

        def reasoning_cb(text: str) -> None:
            reasoned_live["v"] = True       # noqa: B023 — consumed within this iteration only
            emit({"type": "reasoning_delta", "text": text})

        # Thinking-signature recovery (one-shot, this turn): when set, send the wire a
        # COPY of the messages with thinking blocks removed. The canonical session is
        # never mutated — persisting a stripped message would permanently corrupt the
        # stored thinking signatures and 400 on every future turn (Hermes #24107).
        wire_messages = session.messages
        if getattr(agent, "_strip_thinking", False):
            wire_messages = [_without_thinking(m) for m in session.messages]
        provider_span = None
        response_state = _response_state_for_agent(agent, getattr(agent.session, "id", ""))
        _record_response_request_meta(session, response_state)
        if trace_store and turn_span:
            try:
                provider_span = trace_store.start_span(
                    trace_id=trace_id,
                    session_id=session.id,
                    turn_id=turn_id,
                    parent_span_id=turn_span["span_id"],
                    kind="provider_call",
                    provider=getattr(agent.provider, "name", ""),
                    model=getattr(agent.provider, "model", ""),
                    data=_provider_trace_data(agent, wire_messages, schemas, response_state, prompt_meta),
                )
            except Exception:  # noqa: BLE001
                provider_span = None
        provider_started = time.perf_counter()
        try:
            agent._active_response_id = ""
            agent._active_response_cancelled = ""
            resp = _provider_complete(
                agent.provider, wire_messages, tools=schemas, stream=agent.stream, on_delta=delta_cb,
                reasoning=getattr(agent, "reasoning", "off"),
                on_reasoning=reasoning_cb,
                tool_runner=executor.execute_one_raw,
                approver=getattr(agent.tool_context, "approver", None),
                cwd=agent.cwd,
                session_id=getattr(agent.session, "id", None),
                response_state=response_state,
                metadata=_provider_metadata(agent),
                on_response_id=lambda rid: setattr(agent, "_active_response_id", str(rid or "")),
            )
            agent._active_response_id = ""
        except Exception as e:  # noqa: BLE001
            agent._active_response_id = ""
            from .._log import log_exc
            from ..providers.fallback import classify_provider_error, recovery_action
            action = recovery_action(classify_provider_error(e))
            # Signed thinking blocks were invalidated upstream -> resend without them (once).
            if action == "strip_thinking" and not getattr(agent, "_strip_thinking", False):
                if trace_store and provider_span:
                    try:
                        trace_store.finish_span(
                            provider_span["span_id"],
                            status="retrying",
                            data={
                                "error": f"{type(e).__name__}: {e}",
                                "error_type": type(e).__name__,
                                "recovery": "strip_thinking",
                                "duration_ms": int((time.perf_counter() - provider_started) * 1000),
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
                agent._strip_thinking = True
                emit({"type": "thinking_strip_retry"})
                continue
            # context_overflow -> compact the session and retry once, instead of failing the turn.
            if (action == "compress" and not getattr(agent, "_overflow_retried", False)):
                if trace_store and provider_span:
                    try:
                        trace_store.finish_span(
                            provider_span["span_id"],
                            status="retrying",
                            data={
                                "error": f"{type(e).__name__}: {e}",
                                "error_type": type(e).__name__,
                                "recovery": "compress",
                                "duration_ms": int((time.perf_counter() - provider_started) * 1000),
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
                agent._overflow_retried = True
                emit({"type": "compacting", "reason": "context_overflow"})
                session = _force_compact(agent, session)
                continue
            if trace_store and provider_span:
                try:
                    trace_store.finish_span(
                        provider_span["span_id"],
                        status="error",
                        data={
                            "error": f"{type(e).__name__}: {e}",
                            "error_type": type(e).__name__,
                            "duration_ms": int((time.perf_counter() - provider_started) * 1000),
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
            log_exc("provider.complete failed")
            msg = f"{type(e).__name__}: {e}"
            low = str(e).lower()
            if "not a chat model" in low or ("model" in low and ("404" in low or "does not exist" in low)):
                msg += ("\n  → That model isn't available on this endpoint/auth. Pick another with "
                        "`aegis model set <provider> <model>` (e.g. gpt-5.2-chat-latest for an API "
                        "key), or use the `codex` provider + `codex login` for ChatGPT-subscription "
                        "models like gpt-5.5.")
            emit({"type": "error", "message": msg})
            err = Message.assistant(f"[provider error] {msg}")
            session.messages.append(err)
            _finish_turn("error", data={"error": msg})
            return err

        budget.api_call_count += 1
        budget.usage.add(resp.usage)
        if trace_store and provider_span:
            try:
                provider_duration_ms = int((time.perf_counter() - provider_started) * 1000)
                trace_store.finish_span(
                    provider_span["span_id"],
                    status="ok",
                    cost=_usage_cost_usd(getattr(agent.provider, "model", ""), resp.usage, agent.config),
                    cache_read=getattr(resp.usage, "cache_read", 0),
                    cache_write=getattr(resp.usage, "cache_write", 0),
                    data=_response_trace_data(resp, provider_duration_ms),
                )
            except Exception:  # noqa: BLE001
                pass
        from .governance import strip_reasoning
        resp.text = strip_reasoning(resp.text)   # drop any inlined <think>…</think> blocks
        assistant_msg = resp.to_message()
        session.messages.append(assistant_msg)
        if resp.reasoning and not reasoned_live["v"]:    # blocking path: emit once at the end
            emit({"type": "reasoning_delta", "text": resp.reasoning})
        reasoned_live["v"] = False
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
            # Empty reply after using tools = a dead-end turn; nudge it to continue (twice max)
            # rather than handing back nothing.
            if not (resp.text or "").strip() and agent.tools_used > 0 and empty_nudges < 2:
                empty_nudges += 1
                emit({"type": "empty_nudge", "n": empty_nudges})
                session.messages.append(Message.user(
                    "You returned an empty reply after using tools. Continue: take the next "
                    "action, or give the final answer."))
                continue
            # Periodic skill-save nudge: every N tool-uses across a long session (not once-ever).
            from ..constants import SKILL_AUTOGEN_THRESHOLD
            interval = int(agent.config.get("learn.skill_nudge_interval", SKILL_AUTOGEN_THRESHOLD) or 0)
            last = int(agent.session.meta.get("_last_skill_nudge", 0))
            if (interval > 0 and agent.config.get("skills.autogen", True)
                    and agent.tools_used - last >= interval):
                agent.session.meta["_last_skill_nudge"] = agent.tools_used
                emit({"type": "skill_nudge"})
            emit({"type": "final", "text": resp.text})
            _finish_turn("ok", data={"text": resp.text})
            return assistant_msg

        results = executor.execute(resp.tool_calls)
        session.messages.extend(results)
        agent.tools_used += len(resp.tool_calls)
        # Todo staleness nudge: once the model starts a todo list, keep it honest.
        if any(tc.name == "todo_write" for tc in resp.tool_calls):
            session.meta["_last_todo_use"] = agent.tools_used
        elif (session.meta.get("_last_todo_use") is not None and results
                and agent.tools_used - session.meta["_last_todo_use"]
                >= int(agent.config.get("tools.todo_nudge_after", 15))):
            session.meta["_last_todo_use"] = agent.tools_used
            results[-1].content = (results[-1].content or "") + (
                "\n\n<system-reminder>The todo list hasn't been updated in a while. If the "
                "plan changed or items are done, update it with todo_write; if it's stale, "
                "clear it.</system-reminder>")
        # Refund the step for a pure "zero-context-cost" compute turn (only execute_code) so
        # code-heavy runs aren't penalized against the iteration budget.
        if resp.tool_calls and all(tc.name == "execute_code" for tc in resp.tool_calls):
            budget.refund()

        # Incremental persist so a crash mid-turn doesn't lose progress.
        if agent.store is not None:
            try:
                agent.store.save(session)
            except Exception:  # noqa: BLE001
                pass

    # Budget exhausted -> one grace call without tools for a final summary.
    emit({"type": "budget_exhausted"})
    session.messages.append(
        Message.user("You've reached the step limit. Stop and summarize what you accomplished "
                     "and what remains.")
    )
    grace_span = None
    grace_response_state = _response_state_for_agent(agent, getattr(agent.session, "id", ""))
    _record_response_request_meta(session, grace_response_state)
    if trace_store and turn_span:
        try:
            grace_data = _provider_trace_data(
                agent,
                session.messages,
                [],
                grace_response_state,
                _prompt_trace_meta(session),
            )
            grace_data.update({
                "grace": True,
                "reason": "budget_exhausted",
                "tools_enabled": False,
                "budget_calls": budget.api_call_count,
            })
            grace_span = trace_store.start_span(
                trace_id=trace_id,
                session_id=session.id,
                turn_id=turn_id,
                parent_span_id=turn_span["span_id"],
                kind="provider_call",
                provider=getattr(agent.provider, "name", ""),
                model=getattr(agent.provider, "model", ""),
                data=grace_data,
            )
        except Exception:  # noqa: BLE001
            grace_span = None
    grace_started = time.perf_counter()
    try:
        grace = _provider_complete(
            agent.provider,
            session.messages,
            tools=None,
            stream=agent.stream,
            on_delta=lambda t: emit({"type": "assistant_delta", "text": t}),
            reasoning=getattr(agent, "reasoning", "off"),
            session_id=getattr(agent.session, "id", None),
            response_state=grace_response_state,
            metadata=_provider_metadata(agent),
            on_response_id=lambda rid: setattr(agent, "_active_response_id", str(rid or "")),
        )
        agent._active_response_id = ""
        budget.usage.add(grace.usage)
        if trace_store and grace_span:
            try:
                grace_duration_ms = int((time.perf_counter() - grace_started) * 1000)
                trace_store.finish_span(
                    grace_span["span_id"],
                    status="ok",
                    cost=_usage_cost_usd(getattr(agent.provider, "model", ""), grace.usage, agent.config),
                    cache_read=getattr(grace.usage, "cache_read", 0),
                    cache_write=getattr(grace.usage, "cache_write", 0),
                    data=_response_trace_data(grace, grace_duration_ms),
                )
            except Exception:  # noqa: BLE001
                pass
        gm = grace.to_message()
    except Exception as e:  # noqa: BLE001
        agent._active_response_id = ""
        if trace_store and grace_span:
            try:
                trace_store.finish_span(
                    grace_span["span_id"],
                    status="error",
                    data={
                        "error": f"{type(e).__name__}: {e}",
                        "error_type": type(e).__name__,
                        "duration_ms": int((time.perf_counter() - grace_started) * 1000),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        gm = Message.assistant(f"[step limit reached; summary failed: {e}]")
    session.messages.append(gm)
    emit({"type": "final", "text": gm.content})
    _finish_turn("budget_exhausted", data={"text": gm.content})
    return gm
