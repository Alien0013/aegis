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

_NO_PROGRESS_LIMIT = 2
_NO_PROGRESS_MIN_SAVINGS_PCT = 10.0
_PROGRESS_MIN_SAVINGS_PCT = 5.0
_MAX_COMPACTION_PASSES = 5
_DEFAULT_COMPACTION_PASSES = 3
_MIN_TAIL_TOKENS = 256
_MIN_TOOL_TOKENS = 80


def _no_progress_state(session) -> dict[str, Any]:
    meta = getattr(session, "meta", None)
    if not isinstance(meta, dict):
        return {}
    state = meta.get("compression_no_progress")
    return state if isinstance(state, dict) else {}


def _no_progress_count(session) -> int:
    try:
        return max(0, int(_no_progress_state(session).get("count", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _skip_for_no_progress_guard(agent, session, emit: OnEvent | None) -> bool:
    count = _no_progress_count(session)
    state = _no_progress_state(session)
    if count < _NO_PROGRESS_LIMIT and not state.get("blocked"):
        return False
    agent._compact_stuck = True
    if emit:
        emit({
            "type": "compaction_skipped",
            "reason": "compression_no_progress",
            "session_id": getattr(session, "id", "") or "",
            "ineffective_count": count,
            "blocked": bool(state.get("blocked")),
            "last_savings_pct": state.get("last_savings_pct"),
            "last_plan_id": state.get("last_plan_id", ""),
            "last_stop_reason": state.get("last_stop_reason", ""),
        })
    return True


def _coerce_int(value: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    return max(min_value, min(max_value, out))


def _coerce_float(value: Any, default: float, *, min_value: float, max_value: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = default
    return max(min_value, min(max_value, out))


def _compression_made_progress(
    before_n: int,
    after_n: int,
    before_tok: int,
    after_tok: int,
) -> bool:
    """Reference-style progress: fewer rows OR a material token reduction."""
    if int(after_n or 0) < int(before_n or 0):
        return True
    before = max(0, int(before_tok or 0))
    after = max(0, int(after_tok or 0))
    return before > 0 and after < before * (1.0 - (_PROGRESS_MIN_SAVINGS_PCT / 100.0))


def _compaction_fallback_used(messages: list) -> bool:
    for message in messages:
        meta = getattr(message, "meta", None)
        if isinstance(meta, dict) and meta.get("fallback_used"):
            return True
    return False


def _compaction_plan(agent, comp: dict, *, reason: str, recovery: bool) -> dict[str, Any]:
    """Build a bounded multi-pass compaction plan.

    Each pass tightens the protected tail and tool-output budgets. The first pass
    matches the current AEGIS behavior; later passes are only used when the
    re-measured transcript still exceeds the window and the previous pass made
    material progress.
    """
    mode = "overflow_recovery" if recovery else "proactive"
    max_pass_key = "recovery_max_passes" if recovery else "max_passes"
    max_passes = _coerce_int(
        comp.get(max_pass_key, comp.get("max_passes", _DEFAULT_COMPACTION_PASSES)),
        _DEFAULT_COMPACTION_PASSES,
        min_value=1,
        max_value=_MAX_COMPACTION_PASSES,
    )
    base_tool_tokens = comp.get("max_tool_tokens", 600)
    if recovery:
        base_tool_tokens = min(400, base_tool_tokens)
    base_tool_tokens = _coerce_int(
        base_tool_tokens,
        400 if recovery else 600,
        min_value=_MIN_TOOL_TOKENS,
        max_value=20_000,
    )
    return {
        "id": new_id("compact_plan"),
        "mode": mode,
        "reason": reason,
        "recovery": recovery,
        "max_passes": max_passes,
        "base_tail_tokens": max(_MIN_TAIL_TOKENS, _tail_token_budget(agent, comp, tight=recovery)),
        "tail_decay": _coerce_float(
            comp.get("recovery_tail_decay" if recovery else "tail_decay"),
            0.65 if recovery else 0.75,
            min_value=0.25,
            max_value=0.95,
        ),
        "base_max_tool_tokens": base_tool_tokens,
        "tool_decay": _coerce_float(comp.get("tool_decay"), 0.75, min_value=0.25, max_value=1.0),
        "context_length": int(getattr(agent.provider, "context_length", 0) or 0),
    }


def _pass_budget(plan: dict[str, Any], pass_index: int) -> tuple[int, int]:
    tail_tokens = int(plan["base_tail_tokens"] * (plan["tail_decay"] ** pass_index))
    max_tool_tokens = int(plan["base_max_tool_tokens"] * (plan["tool_decay"] ** pass_index))
    return max(_MIN_TAIL_TOKENS, tail_tokens), max(_MIN_TOOL_TOKENS, max_tool_tokens)


def _pass_record(
    plan: dict[str, Any],
    *,
    pass_index: int,
    tail_tokens: int,
    max_tool_tokens: int,
    before_n: int,
    after_n: int,
    before_tok: int,
    after_tok: int,
    still_over: bool,
    fallback_used: bool,
) -> dict[str, Any]:
    saved = max(0, int(before_tok or 0)) - max(0, int(after_tok or 0))
    savings_pct = (saved / before_tok * 100.0) if before_tok else 0.0
    progress = _compression_made_progress(before_n, after_n, before_tok, after_tok)
    if after_n < before_n:
        progress_kind = "messages"
    elif progress:
        progress_kind = "tokens"
    else:
        progress_kind = "none"
    return {
        "plan_id": plan["id"],
        "pass": pass_index + 1,
        "max_passes": plan["max_passes"],
        "tail_tokens": tail_tokens,
        "max_tool_tokens": max_tool_tokens,
        "messages_before": int(before_n or 0),
        "messages_after": int(after_n or 0),
        "tokens_before": int(before_tok or 0),
        "tokens_after": int(after_tok or 0),
        "tokens_saved": saved,
        "savings_pct": round(savings_pct, 2),
        "progress": progress,
        "progress_kind": progress_kind,
        "still_over_threshold": bool(still_over),
        "fallback_used": bool(fallback_used),
    }


def _recovery_state(session) -> dict[str, Any]:
    meta = getattr(session, "meta", None)
    if not isinstance(meta, dict):
        return {}
    state = meta.get("compression_recovery")
    return state if isinstance(state, dict) else {}


def _record_plan_metadata(
    session,
    rec: dict,
    plan: dict[str, Any],
    pass_records: list[dict[str, Any]],
) -> None:
    meta = getattr(session, "meta", None)
    if not isinstance(meta, dict):
        return
    previous = _recovery_state(session)
    try:
        previous_depth = max(0, int(previous.get("depth", 0) or 0))
    except (TypeError, ValueError):
        previous_depth = 0
    depth_delta = len(pass_records) if plan.get("recovery") else 0
    latest = pass_records[-1] if pass_records else {}
    meta["compression_recovery"] = {
        "depth": previous_depth + depth_delta,
        "last_depth_delta": depth_delta,
        "last_plan_id": plan["id"],
        "last_mode": plan["mode"],
        "last_reason": rec.get("reason", ""),
        "last_at": rec.get("at", ""),
        "last_pass_count": len(pass_records),
        "last_max_passes": plan["max_passes"],
        "last_stop_reason": rec.get("stop_reason", ""),
        "last_still_over_threshold": bool(rec.get("stuck")),
        "last_fallback_used": bool(rec.get("fallback_used")),
        "last_tokens_before": int(rec.get("tokens_before", 0) or 0),
        "last_tokens_after": int(rec.get("tokens_after", 0) or 0),
        "last_messages_before": int(rec.get("messages_before", 0) or 0),
        "last_messages_after": int(rec.get("messages_after", 0) or 0),
        "last_tail_tokens": latest.get("tail_tokens"),
        "passes": pass_records[-_MAX_COMPACTION_PASSES:],
    }


def _carry_recovery_state(parent, child) -> None:
    child_meta = getattr(child, "meta", None)
    if not isinstance(child_meta, dict):
        return
    state = _recovery_state(parent)
    if state:
        child_meta["compression_recovery"] = dict(state)
    else:
        child_meta.pop("compression_recovery", None)


def _record_compression_progress(
    session,
    rec: dict,
    *,
    before_n: int,
    after_n: int,
    before_tok: int,
    after_tok: int,
) -> None:
    before = max(0, int(before_tok or 0))
    after = max(0, int(after_tok or 0))
    saved = before - after
    if before:
        savings_pct = (saved / before) * 100.0
    else:
        savings_pct = 100.0 if after_n < before_n else 0.0
    rec["tokens_saved"] = saved
    rec["savings_pct"] = round(savings_pct, 2)

    meta = getattr(session, "meta", None)
    if not isinstance(meta, dict):
        return
    if savings_pct >= _NO_PROGRESS_MIN_SAVINGS_PCT:
        meta.pop("compression_no_progress", None)
        return

    from ..util import now_iso
    count = _no_progress_count(session) + 1
    blocked = count >= _NO_PROGRESS_LIMIT
    meta["compression_no_progress"] = {
        "count": count,
        "blocked": blocked,
        "last_at": rec.get("at") or now_iso(),
        "last_reason": rec.get("reason", ""),
        "last_savings_pct": round(savings_pct, 2),
        "last_tokens_before": before,
        "last_tokens_after": after,
        "last_messages_before": int(before_n or 0),
        "last_messages_after": int(after_n or 0),
        "last_progress_made": bool(rec.get("progress_made")),
        "last_progress_kind": rec.get("progress_kind", "none"),
        "last_stop_reason": rec.get("stop_reason", ""),
        "last_plan_id": rec.get("plan_id", ""),
        "last_pass_count": int(rec.get("pass_count", 0) or 0),
    }
    rec["no_progress"] = True
    rec["no_progress_count"] = count
    if blocked:
        rec["no_progress_blocked"] = True


def _carry_no_progress_state(parent, child) -> None:
    child_meta = getattr(child, "meta", None)
    if not isinstance(child_meta, dict):
        return
    state = _no_progress_state(parent)
    if state:
        child_meta["compression_no_progress"] = dict(state)
    else:
        child_meta.pop("compression_no_progress", None)


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


def _save_compacted_session(agent, session, *, archive: bool = False) -> None:
    store = getattr(agent, "store", None)
    if store is None:
        return
    if archive and hasattr(store, "archive_and_compact"):
        try:
            store.archive_and_compact(session)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        store.save(session)
    except Exception:  # noqa: BLE001
        pass


def _record_compaction(agent, session, rec: dict, *, save: bool = False,
                       archive: bool = False) -> None:
    session.meta.setdefault("compactions", []).append(rec)
    if save:
        _save_compacted_session(agent, session, archive=archive)


def _apply_plan_result(
    rec: dict,
    plan: dict[str, Any],
    pass_records: list[dict[str, Any]],
    *,
    stop_reason: str,
    still_over: bool,
) -> None:
    latest = pass_records[-1] if pass_records else {}
    rec.update({
        "plan_id": plan["id"],
        "plan_mode": plan["mode"],
        "pass_count": len(pass_records),
        "max_passes": plan["max_passes"],
        "passes": pass_records,
        "stop_reason": stop_reason,
        "progress_made": any(p.get("progress") for p in pass_records),
        "progress_kind": latest.get("progress_kind", "none"),
        "fallback_used": any(p.get("fallback_used") for p in pass_records),
        "tail_tokens": latest.get("tail_tokens"),
        "max_tool_tokens": latest.get("max_tool_tokens"),
    })
    if still_over:
        rec["stuck"] = True


def _run_compaction_plan(
    agent,
    engine,
    comp: dict,
    messages: list,
    *,
    plan: dict[str, Any],
    schema_tokens: int,
    pre_compress_context: str,
    focus: str = "",
) -> tuple[list, list[dict[str, Any]], str, bool]:
    current = list(messages)
    pass_records: list[dict[str, Any]] = []
    stop_reason = "max_passes"
    still_over = True
    summarizer = _summarizer(agent)

    for pass_index in range(plan["max_passes"]):
        tail_tokens, max_tool_tokens = _pass_budget(plan, pass_index)
        before_n = len(current)
        before_tok = compaction.estimated_tokens(current)
        kwargs = {
            "preserve_first": comp.get("preserve_first", 3),
            "tail_tokens": tail_tokens,
            "max_tool_tokens": max_tool_tokens,
            "pre_compress_context": pre_compress_context,
            "abort_on_summary_failure": bool(comp.get("abort_on_summary_failure", False)),
        }
        if focus:
            kwargs["focus"] = focus
        compressed = governance.normalize(engine.compress(current, summarizer, **kwargs))
        after_tok = compaction.estimated_tokens(compressed)
        still_over = _engine_should_compress(
            engine,
            compressed,
            agent.provider.context_length,
            schema_tokens,
            agent,
        )
        pass_rec = _pass_record(
            plan,
            pass_index=pass_index,
            tail_tokens=tail_tokens,
            max_tool_tokens=max_tool_tokens,
            before_n=before_n,
            after_n=len(compressed),
            before_tok=before_tok,
            after_tok=after_tok,
            still_over=still_over,
            fallback_used=_compaction_fallback_used(compressed),
        )
        pass_records.append(pass_rec)
        current = compressed
        if not pass_rec["progress"]:
            stop_reason = "no_progress"
            break
        if not still_over:
            stop_reason = "below_threshold"
            break

    return current, pass_records, stop_reason, still_over


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
        plan = _compaction_plan(agent, comp, reason="context_overflow", recovery=True)
        session.messages, pass_records, stop_reason, still_over = _run_compaction_plan(
            agent,
            engine,
            comp,
            session.messages,
            plan=plan,
            schema_tokens=0,
            pre_compress_context=pre_compress_context,
        )
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
        _apply_plan_result(
            rec,
            plan,
            pass_records,
            stop_reason=stop_reason,
            still_over=still_over,
        )
        _record_compression_progress(
            session,
            rec,
            before_n=before_n,
            after_n=len(session.messages),
            before_tok=before_tok,
            after_tok=after_tok,
        )
        if still_over:
            agent._compact_stuck = True
        _record_plan_metadata(session, rec, plan, pass_records)
        _record_compaction(agent, session, rec, save=True, archive=True)
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
    except compaction.CompressionAborted as exc:
        from ..util import now_iso
        rec = {
            "at": now_iso(),
            "iteration": getattr(agent.budget, "api_call_count", 0),
            "messages_before": len(session.messages),
            "messages_after": len(session.messages),
            "tokens_before": compaction.estimated_tokens(session.messages),
            "tokens_after": compaction.estimated_tokens(session.messages),
            "reason": "context_overflow",
            "recovery": True,
            "aborted": True,
            "error": str(exc),
            "stop_reason": "aborted",
        }
        _record_compaction(agent, session, rec, save=True)
        _trace_compaction(agent, session, rec)
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
    if _skip_for_no_progress_guard(agent, session, emit):
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
    from ..constants import COMPACT_THRESHOLD
    from ..util import now_iso
    pct = int((COMPACT_THRESHOLD if threshold is None else threshold) * 100)
    reason = f"context exceeded {pct}% of the window"
    plan = _compaction_plan(agent, comp, reason=reason, recovery=False)
    try:
        compressed, pass_records, stop_reason, still_over = _run_compaction_plan(
            agent,
            engine,
            comp,
            session.messages,
            plan=plan,
            schema_tokens=schema_tokens,
            pre_compress_context=pre_compress_context,
        )
    except compaction.CompressionAborted as exc:
        rec = {"at": now_iso(), "iteration": budget.api_call_count,
               "messages_before": before_n, "messages_after": before_n,
               "tokens_before": before_tok, "tokens_after": before_tok,
               "reason": "compression_aborted", "error": str(exc), "aborted": True,
               "plan_id": plan["id"], "plan_mode": plan["mode"], "stop_reason": "aborted",
               "pass_count": 0, "max_passes": plan["max_passes"], "passes": []}
        _record_plan_metadata(session, rec, plan, [])
        _record_compaction(agent, session, rec, save=True)
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
    rec = {"at": now_iso(), "iteration": budget.api_call_count,
           "messages_before": before_n, "messages_after": len(compressed),
           "tokens_before": before_tok, "tokens_after": after_tok,
           "reason": reason}
    _apply_plan_result(
        rec,
        plan,
        pass_records,
        stop_reason=stop_reason,
        still_over=still_over,
    )
    _record_compression_progress(
        session,
        rec,
        before_n=before_n,
        after_n=len(compressed),
        before_tok=before_tok,
        after_tok=after_tok,
    )
    if still_over:
        agent._compact_stuck = True
    _record_plan_metadata(session, rec, plan, pass_records)

    if not still_over and comp.get("split_sessions", True) and agent.store is not None \
            and len(compressed) < before_n:
        from ..session import Session
        parent = session
        child = Session.create(title=_next_in_lineage(parent.title), parent_id=parent.id)
        child.messages = compressed
        _inherit_child_session_meta(parent, child)
        _carry_no_progress_state(parent, child)
        _carry_recovery_state(parent, child)
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
        _record_compaction(agent, session, rec, save=True, archive=True)
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
    plan = _compaction_plan(agent, comp, reason=reason, recovery=False)
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
    tail_tokens, max_tool_tokens = _pass_budget(plan, 0)
    pass_record = _pass_record(
        plan,
        pass_index=0,
        tail_tokens=tail_tokens,
        max_tool_tokens=max_tool_tokens,
        before_n=before_n,
        after_n=len(compressed),
        before_tok=before_tok,
        after_tok=after_tok,
        still_over=False,
        fallback_used=_compaction_fallback_used(compressed),
    )
    if preserve_last is not None:
        pass_record["preserve_last"] = preserve_last
    _apply_plan_result(
        rec,
        plan,
        [pass_record],
        stop_reason="manual",
        still_over=False,
    )
    _record_compression_progress(
        session,
        rec,
        before_n=before_n,
        after_n=len(compressed),
        before_tok=before_tok,
        after_tok=after_tok,
    )
    _record_plan_metadata(session, rec, plan, [pass_record])

    if comp.get("split_sessions", True) and agent.store is not None and len(compressed) < before_n:
        from ..session import Session
        parent = session
        child = Session.create(title=_next_in_lineage(parent.title), parent_id=parent.id)
        child.messages = compressed
        _inherit_child_session_meta(parent, child)
        _carry_no_progress_state(parent, child)
        _carry_recovery_state(parent, child)
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
        _record_compaction(agent, session, rec, save=True, archive=True)
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
