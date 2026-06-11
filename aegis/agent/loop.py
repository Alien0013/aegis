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
        self.emit({"type": "tool_start", "id": call.id, "name": call.name, "args": call.arguments})
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
        self.emit({"type": "tool_result", "id": call.id, "name": call.name,
                   "summary": res.summary, "is_error": res.is_error,
                   "classification": res.classification})
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
    s = getattr(agent, "_aux_provider", None)
    if s is None:
        try:
            from ..providers.registry import build_aux_provider
            s = build_aux_provider(agent.config)
        except Exception:  # noqa: BLE001 - never block compaction on aux setup
            s = agent.provider
        agent._aux_provider = s
    return s


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
    session.messages = governance.normalize(_engine(agent).compress(
        session.messages, _summarizer(agent),
        preserve_first=comp.get("preserve_first", 3),
        tail_tokens=_tail_token_budget(agent, comp, tight=True),
        max_tool_tokens=min(400, comp.get("max_tool_tokens", 600)),
    ))
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
        try:
            agent.store.save(parent)                    # preserve full parent history
        except Exception:  # noqa: BLE001
            pass
        child = Session.create(title=_next_in_lineage(parent.title), parent_id=parent.id)
        child.messages = compressed
        child.meta["forked_from"] = parent.id
        child.meta["summary"] = parent.meta.get("summary", "")
        child.meta.setdefault("compactions", []).append(rec)
        agent.session = child
        agent.tool_context.session = child
        session = child
        rec = {**rec, "split": True, "child_session": child.id, "parent_session": parent.id}
    else:
        session.messages = compressed
        session.meta.setdefault("compactions", []).append(rec)
    emit({"type": "compacted", **rec})
    agent.refresh_volatile()
    return session


def run_conversation(agent, on_event: OnEvent | None = None) -> Message:
    """Drive one user turn to completion. Returns the final assistant message."""
    emit = on_event or (lambda e: None)
    # Memory freshness policy (memory.refresh):
    #   "session" (default, Hermes-style) — the snapshot stays FROZEN for the whole
    #     session so the prompt prefix is byte-stable and the cache never thrashes,
    #     no matter how often memory is written. Saves are durable on disk at once
    #     and load on the next session — or earlier, for free, whenever something
    #     else already rebuilds the prompt (compaction, session split, /model).
    #   "message" — mtime check each turn; a changed file rebuilds the prompt so
    #     facts apply from the very next message (one cache miss per write).
    refresh_mode = (agent.config.get("memory.refresh", "session") or "session")
    if (refresh_mode == "message" and agent.memory is not None
            and agent.memory.is_stale()):
        agent.refresh_volatile()
    else:
        agent.ensure_system_prompt()
    session = agent.session
    budget = agent.budget
    budget.reset()

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
        from ..plugins import fire_hook
        rewritten = fire_hook("pre_llm_call", session.messages, agent)   # in-process Python hook
        if isinstance(rewritten, list):
            session.messages = rewritten

        def delta_cb(text: str) -> None:
            emit({"type": "assistant_delta", "text": text})

        reasoned_live = {"v": False}

        def reasoning_cb(text: str) -> None:
            reasoned_live["v"] = True
            emit({"type": "reasoning_delta", "text": text})

        # Thinking-signature recovery (one-shot, this turn): when set, send the wire a
        # COPY of the messages with thinking blocks removed. The canonical session is
        # never mutated — persisting a stripped message would permanently corrupt the
        # stored thinking signatures and 400 on every future turn (Hermes #24107).
        wire_messages = session.messages
        if getattr(agent, "_strip_thinking", False):
            wire_messages = [_without_thinking(m) for m in session.messages]
        try:
            resp = _provider_complete(
                agent.provider, wire_messages, tools=schemas, stream=agent.stream, on_delta=delta_cb,
                reasoning=getattr(agent, "reasoning", "off"),
                on_reasoning=reasoning_cb,
                tool_runner=executor.execute_one_raw,
                approver=getattr(agent.tool_context, "approver", None),
                cwd=agent.cwd,
            )
        except Exception as e:  # noqa: BLE001
            from .._log import log_exc
            from ..providers.fallback import classify_provider_error, recovery_action
            action = recovery_action(classify_provider_error(e))
            # Signed thinking blocks were invalidated upstream -> resend without them (once).
            if action == "strip_thinking" and not getattr(agent, "_strip_thinking", False):
                agent._strip_thinking = True
                emit({"type": "thinking_strip_retry"})
                continue
            # context_overflow -> compact the session and retry once, instead of failing the turn.
            if (action == "compress" and not getattr(agent, "_overflow_retried", False)):
                agent._overflow_retried = True
                emit({"type": "compacting", "reason": "context_overflow"})
                session = _force_compact(agent, session)
                continue
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
            return err

        budget.api_call_count += 1
        budget.usage.add(resp.usage)
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
