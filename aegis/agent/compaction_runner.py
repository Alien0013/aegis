"""Context compaction orchestration — extracted from the agent loop.

Owns the lifecycle around shrinking a session's history so an over-full window
never reaches the provider: the proactive in-loop check (:func:`_maybe_compact`),
the overflow-recovery path (:func:`_force_compact`), the user/gateway-facing
manual entry point (:func:`compact_now`), plus the cross-session compression lock,
aux-summarizer feasibility preflight, and split-session lineage bookkeeping.

The pure summarization algorithm lives in :mod:`aegis.agent.compaction`; this module
is the *runner* that wires it to the agent, session store, tracing, and event stream.

Import direction is one-way: the loop imports these names; this module never imports
``loop`` at module load (only lazily, inside the one function that needs a shared
hook helper) so there is no import cycle.
"""

from __future__ import annotations

from typing import Any, Callable

from ..types import new_id
from . import compaction, governance
from .events import EventType

OnEvent = Callable[[dict], None]


def _engine_should_compress(engine, messages, context_length: int, overhead_tokens: int, agent) -> bool:
    from .loop import _agent_output_reservation_tokens   # output-budget helper stays with the loop
    max_output_tokens = _agent_output_reservation_tokens(agent)
    try:
        return bool(engine.should_compress(
            messages,
            context_length,
            overhead_tokens,
            max_output_tokens=max_output_tokens,
        ))
    except TypeError:
        return bool(engine.should_compress(messages, context_length, overhead_tokens))


def _inherit_child_session_meta(parent, child) -> None:
    meta = getattr(parent, "meta", {}) or {}
    child_meta = getattr(child, "meta", None)
    if not isinstance(meta, dict) or not isinstance(child_meta, dict):
        return
    for key in ("runtime", "runtime_controls", "model", "provider"):
        if key not in meta:
            continue
        value = meta[key]
        child_meta[key] = dict(value) if isinstance(value, dict) else value


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


def _tail_token_budget(agent, comp: dict, *, tight: bool = False) -> int:
    """Tokens of recent conversation to protect during compaction — a fraction of the
    model's window so the tail scales with the model, not a fixed message count. The
    overflow-recovery path uses a tighter fraction so the request actually shrinks."""
    ctx = getattr(agent.provider, "context_length", 0) or 0
    frac = comp.get("tail_fraction", 0.25)
    if tight:
        frac = min(frac, 0.12)
    return int(ctx * frac) if ctx > 0 else (3000 if tight else 6000)


def _ensure_compression_feasibility(agent, engine, comp: dict, emit: OnEvent | None = None) -> None:
    """Aux compression preflight.

    If the configured compression summarizer has a smaller context window than
    the main model threshold, lower the live default-engine threshold for this
    session so compaction starts while the summarizer can still read the window.
    """
    # Memoize per provider identity, not once per session: a mid-session model
    # switch changes the context window, so the preflight must re-run for the new
    # model instead of staying pinned to the first model's feasibility decision.
    provider_id = (
        getattr(agent.provider, "model", None),
        int(getattr(agent.provider, "context_length", 0) or 0),
    )
    if getattr(agent, "_compression_feasibility_checked", None) == provider_id:
        return
    agent._compression_feasibility_checked = provider_id
    main_ctx = int(getattr(agent.provider, "context_length", 0) or 0)
    if main_ctx <= 0:
        return
    from ..constants import COMPACT_THRESHOLD
    try:
        current_fraction = (
            engine.threshold_fraction() if hasattr(engine, "threshold_fraction")
            else comp.get("threshold", COMPACT_THRESHOLD)
        )
    except Exception:  # noqa: BLE001
        current_fraction = comp.get("threshold", COMPACT_THRESHOLD)
    try:
        threshold_fraction = float(COMPACT_THRESHOLD if current_fraction is None else current_fraction)
    except (TypeError, ValueError):
        threshold_fraction = 0.5
    threshold_tokens = max(1, int(main_ctx * threshold_fraction))
    try:
        summarizer = _summarizer(agent)
        aux_ctx = int(getattr(summarizer, "context_length", 0) or 0)
    except Exception:  # noqa: BLE001
        return
    if aux_ctx <= 0 or aux_ctx >= threshold_tokens:
        return
    new_fraction = max(0.01, min(threshold_fraction, aux_ctx / main_ctx))
    adjusted = False
    try:
        if hasattr(engine, "set_threshold_fraction"):
            engine.set_threshold_fraction(new_fraction)
            adjusted = True
    except Exception:  # noqa: BLE001
        adjusted = False
    rec = {
        "main_context_tokens": main_ctx,
        "old_threshold": threshold_fraction,
        "old_threshold_tokens": threshold_tokens,
        "aux_context_tokens": aux_ctx,
        "new_threshold": new_fraction,
        "new_threshold_tokens": int(main_ctx * new_fraction),
        "adjusted": adjusted,
        "aux_model": str(getattr(summarizer, "model", "") or ""),
        "aux_provider": str(getattr(summarizer, "name", "") or ""),
    }
    try:
        agent.session.meta["compression_feasibility"] = rec
    except Exception:  # noqa: BLE001
        pass
    if emit:
        emit({"type": "compression_feasibility", **rec})


def _memory_pre_compress_context(agent, session) -> str:
    if not agent.memory:
        return ""
    try:
        agent.memory.refresh_snapshot()
        note = agent.memory.on_pre_compress(session.messages)
    except Exception:  # noqa: BLE001
        return ""
    return note.strip() if isinstance(note, str) else ""


def _compression_lock_holder(agent, session) -> str:
    return f"{getattr(session, 'id', 'unknown')}:{new_id('lock')}"


def _acquire_compression_lock(agent, session, emit: OnEvent | None = None) -> tuple[bool, str | None]:
    store = getattr(agent, "store", None)
    sid = getattr(session, "id", "") or ""
    if store is None or not sid:
        return True, None
    holder = _compression_lock_holder(agent, session)
    try:
        acquired = store.try_acquire_compression_lock(sid, holder)
    except Exception:  # noqa: BLE001
        return True, None
    if acquired:
        return True, holder
    existing = None
    try:
        existing = store.get_compression_lock_holder(sid)
    except Exception:  # noqa: BLE001
        pass
    if emit:
        emit({
            "type": "compaction_skipped",
            "reason": "compression_lock_held",
            "session_id": sid,
            "holder": existing or "",
        })
    return False, None


def _release_compression_lock(agent, session, holder: str | None) -> None:
    if not holder:
        return
    store = getattr(agent, "store", None)
    sid = getattr(session, "id", "") or ""
    if store is None or not sid:
        return
    try:
        store.release_compression_lock(sid, holder)
    except Exception:  # noqa: BLE001
        pass


def _emit_session_compress(agent, emit: OnEvent | None, payload: dict[str, Any]) -> None:
    event = {"type": EventType.SESSION_COMPRESS, **payload}
    if emit is not None:
        try:
            emit(event)
        except Exception:  # noqa: BLE001
            pass
    callback = getattr(agent, "event_callback", None)
    if callable(callback):
        try:
            callback(EventType.SESSION_COMPRESS, dict(payload))
        except Exception:  # noqa: BLE001
            pass
    try:
        from ..hooks import run_hooks
        from .loop import _shell_hook_context   # shell-hook context shaper stays with the loop
        run_hooks(agent.config, EventType.SESSION_COMPRESS, _shell_hook_context(payload))
    except Exception:  # noqa: BLE001
        pass


def _force_compact(agent, session):
    """Compress unconditionally — recovery from a provider context_overflow. Tighter tail than
    the proactive path so the request actually shrinks below the window."""
    acquired, holder = _acquire_compression_lock(agent, session)
    if not acquired:
        agent._compact_stuck = True
        return session
    comp = agent.config.get("agent.compression", {}) or {}
    engine = _engine(agent)
    _ensure_compression_feasibility(agent, engine, comp)
    try:
        pre_compress_context = _memory_pre_compress_context(agent, session)
        try:
            from .context_engine import call_hook
            call_hook(engine, "on_pre_compress", agent, session)
        except Exception:  # noqa: BLE001
            pass
        before_n = len(session.messages)
        before_tok = compaction.estimated_tokens(session.messages)
        session.messages = governance.normalize(engine.compress(
            session.messages, _summarizer(agent),
            preserve_first=comp.get("preserve_first", 3),
            tail_tokens=_tail_token_budget(agent, comp, tight=True),
            max_tool_tokens=min(400, comp.get("max_tool_tokens", 600)),
            pre_compress_context=pre_compress_context,
            abort_on_summary_failure=bool(comp.get("abort_on_summary_failure", False)),
        ))
        after_tok = compaction.estimated_tokens(session.messages)
        from ..util import now_iso
        rec = {
            "at": now_iso(),
            "iteration": getattr(agent.budget, "api_call_count", 0),
            "messages_before": before_n,
            "messages_after": len(session.messages),
            "tokens_before": before_tok,
            "tokens_after": after_tok,
            "reason": "context_overflow",
            "recovery": True,
        }
        _trace_compaction(agent, session, rec)
        _emit_session_compress(agent, None, {
            "platform": getattr(agent, "platform", "") or "",
            "session_id": getattr(session, "id", "") or "",
            "old_session_id": getattr(session, "id", "") or "",
            "compression_count": len(session.meta.get("compactions", []) or []),
            **rec,
        })
        agent.refresh_volatile()
        return session
    except compaction.CompressionAborted:
        agent._compact_stuck = True
        return session
    finally:
        _release_compression_lock(agent, session, holder)


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
    comp = agent.config.get("agent.compression", {}) or {}
    _ensure_compression_feasibility(agent, engine, comp, emit)
    threshold = (
        engine.threshold_fraction() if hasattr(engine, "threshold_fraction")
        else comp.get("threshold")
    )   # for the record's reason text; the engine applies it
    if not _engine_should_compress(engine, session.messages, agent.provider.context_length, schema_tokens, agent):
        return session
    lock_session = session
    acquired, holder = _acquire_compression_lock(agent, lock_session, emit)
    if not acquired:
        agent._compact_stuck = True
        return session
    emit({"type": "compacting"})
    pre_compress_context = _memory_pre_compress_context(agent, session)
    try:
        from .context_engine import call_hook
        call_hook(engine, "on_pre_compress", agent, session)
    except Exception:  # noqa: BLE001
        pass
    before_n = len(session.messages)
    before_tok = compaction.estimated_tokens(session.messages)
    try:
        compressed = engine.compress(
            session.messages, _summarizer(agent),    # summarize on the cheap aux model, not the main one
            preserve_first=comp.get("preserve_first", 3),
            tail_tokens=_tail_token_budget(agent, comp),   # token-budgeted tail, scales with the window
            max_tool_tokens=comp.get("max_tool_tokens", 600),
            pre_compress_context=pre_compress_context,
            abort_on_summary_failure=bool(comp.get("abort_on_summary_failure", False)),
        )
    except compaction.CompressionAborted as exc:
        from ..util import now_iso
        rec = {"at": now_iso(), "iteration": budget.api_call_count,
               "messages_before": before_n, "messages_after": before_n,
               "tokens_before": before_tok, "tokens_after": before_tok,
               "reason": "compression_aborted", "error": str(exc), "aborted": True}
        session.meta.setdefault("compactions", []).append(rec)
        _trace_compaction(agent, session, rec)
        agent._compact_stuck = True
        _release_compression_lock(agent, lock_session, holder)
        emit({"type": "compaction_aborted", **rec})
        return session
    except Exception:
        _release_compression_lock(agent, lock_session, holder)
        raise
    after_tok = compaction.estimated_tokens(compressed)
    # If we're STILL over the threshold after compacting, the preserved tail is the floor —
    # further compaction can't help, so don't retry this turn (avoids per-iteration thrash).
    still_over = _engine_should_compress(engine, compressed, agent.provider.context_length, schema_tokens, agent)
    from ..constants import COMPACT_THRESHOLD
    from ..util import now_iso
    pct = int((COMPACT_THRESHOLD if threshold is None else threshold) * 100)
    rec = {"at": now_iso(), "iteration": budget.api_call_count,
           "messages_before": before_n, "messages_after": len(compressed),
           "tokens_before": before_tok, "tokens_after": after_tok,
           "reason": f"context exceeded {pct}% of the window"}
    if still_over:
        agent._compact_stuck = True
        rec["stuck"] = True

    if not still_over and comp.get("split_sessions", True) and agent.store is not None \
            and len(compressed) < before_n:
        from ..session import Session
        parent = session
        child = Session.create(title=_next_in_lineage(parent.title), parent_id=parent.id)
        child.messages = compressed
        _inherit_child_session_meta(parent, child)
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
        rec = {**rec, "split": True, "child_session": child.id, "parent_session": parent.id}
        child.meta.setdefault("compactions", []).append(rec)
        try:
            agent.store.save(parent)                    # preserve full parent history + end reason
            agent.store.save(child)                     # make continuation visible immediately
        except Exception:  # noqa: BLE001
            pass
        agent.switch_session(child, reason="compression")
        session = child
        try:
            from .context_engine import call_hook
            call_hook(engine, "on_session_switch", agent, parent, child, reason="compression")
        except Exception:  # noqa: BLE001
            pass
    else:
        session.messages = compressed
        session.meta.setdefault("compactions", []).append(rec)
    _trace_compaction(agent, session, rec)
    _emit_session_compress(agent, emit, {
        "platform": getattr(agent, "platform", "") or "",
        "session_id": getattr(session, "id", "") or "",
        "old_session_id": getattr(lock_session, "id", "") or "",
        "compression_count": len(session.meta.get("compactions", []) or []),
        **rec,
    })
    _release_compression_lock(agent, lock_session, holder)
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
    comp = agent.config.get("agent.compression", {}) or {}
    _ensure_compression_feasibility(agent, engine, comp, emit)
    lock_session = session
    acquired, holder = _acquire_compression_lock(agent, lock_session, emit)
    if not acquired:
        return session
    emit({"type": "compacting", "reason": reason})
    pre_compress_context = _memory_pre_compress_context(agent, session)
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
    if pre_compress_context:
        kwargs["pre_compress_context"] = pre_compress_context
    kwargs["abort_on_summary_failure"] = bool(comp.get("abort_on_summary_failure", False))
    try:
        compressed = governance.normalize(engine.compress(session.messages, _summarizer(agent), **kwargs))
    except compaction.CompressionAborted as exc:
        from ..util import now_iso
        rec = {"at": now_iso(), "iteration": getattr(agent.budget, "api_call_count", 0),
               "messages_before": before_n, "messages_after": before_n,
               "tokens_before": before_tok, "tokens_after": before_tok,
               "reason": reason, "manual": True, "aborted": True, "error": str(exc)}
        session.meta.setdefault("compactions", []).append(rec)
        _trace_compaction(agent, session, rec)
        _release_compression_lock(agent, lock_session, holder)
        emit({"type": "compaction_aborted", **rec})
        return session
    except Exception:
        _release_compression_lock(agent, lock_session, holder)
        raise
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
        _inherit_child_session_meta(parent, child)
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
        rec = {**rec, "split": True, "child_session": child.id, "parent_session": parent.id}
        child.meta.setdefault("compactions", []).append(rec)
        try:
            agent.store.save(parent)
            agent.store.save(child)
        except Exception:  # noqa: BLE001
            pass
        agent.switch_session(child, reason="manual_compression")
        session = child
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
    _emit_session_compress(agent, emit, {
        "platform": getattr(agent, "platform", "") or "",
        "session_id": getattr(session, "id", "") or "",
        "old_session_id": getattr(lock_session, "id", "") or "",
        "compression_count": len(session.meta.get("compactions", []) or []),
        **rec,
    })
    _release_compression_lock(agent, lock_session, holder)
    emit({"type": "compacted", **rec})
    agent.refresh_volatile()
    return session
