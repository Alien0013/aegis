"""Process-local live activity state for all AEGIS surfaces.

Durable runs/traces answer "what happened". This module answers "what is
running right now?" without making each UI infer it from logs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


LIVE_ACTIVITY_VERSION = 1
TERMINAL_STATES = {"ok", "error", "cancelled", "done"}


@dataclass
class ActivityRecord:
    id: str
    surface: str = "agent"
    session_id: str = ""
    run_id: str = ""
    trace_id: str = ""
    turn_id: str = ""
    title: str = ""
    prompt_preview: str = ""
    provider: str = ""
    model: str = ""
    phase: str = "starting"
    status: str = "running"
    iteration: int = 0
    max_iterations: int = 0
    active_provider: str = ""
    active_tool: str = ""
    active_tool_id: str = ""
    active_tool_started_at: float = 0.0
    active_provider_started_at: float = 0.0
    provider_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    subagents_active: int = 0
    subagents_done: int = 0
    compactions: int = 0
    last_event: str = ""
    last_tool: str = ""
    last_text_preview: str = ""
    last_error: str = ""
    note: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    ended_at: float = 0.0

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        active_started = self.active_tool_started_at or self.active_provider_started_at
        return {
            "version": LIVE_ACTIVITY_VERSION,
            "id": self.id,
            "surface": self.surface,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "turn_id": self.turn_id,
            "title": self.title,
            "prompt_preview": self.prompt_preview,
            "provider": self.provider,
            "model": self.model,
            "phase": self.phase,
            "status": self.status,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "active_provider": self.active_provider,
            "active_tool": self.active_tool,
            "active_tool_id": self.active_tool_id,
            "provider_calls": self.provider_calls,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "subagents_active": self.subagents_active,
            "subagents_done": self.subagents_done,
            "compactions": self.compactions,
            "last_event": self.last_event,
            "last_tool": self.last_tool,
            "last_text_preview": self.last_text_preview,
            "last_error": self.last_error,
            "note": self.note,
            "started_at": _iso(self.started_at),
            "updated_at": _iso(self.updated_at),
            "ended_at": _iso(self.ended_at) if self.ended_at else "",
            "elapsed_ms": int(max(0.0, (self.ended_at or now) - self.started_at) * 1000),
            "active_elapsed_ms": int(max(0.0, now - active_started) * 1000) if active_started else 0,
        }


class ActivityStore:
    def __init__(self, retain_completed: int = 30) -> None:
        self.retain_completed = retain_completed
        self._lock = threading.Lock()
        self._records: dict[str, ActivityRecord] = {}
        self._completed: list[ActivityRecord] = []

    def start(
        self,
        activity_id: str,
        *,
        surface: str = "agent",
        session_id: str = "",
        run_id: str = "",
        trace_id: str = "",
        turn_id: str = "",
        title: str = "",
        prompt_preview: str = "",
        provider: str = "",
        model: str = "",
    ) -> None:
        with self._lock:
            self._records[activity_id] = ActivityRecord(
                id=activity_id,
                surface=surface or "agent",
                session_id=session_id,
                run_id=run_id,
                trace_id=trace_id,
                turn_id=turn_id,
                title=title,
                prompt_preview=_clip(prompt_preview, 240),
                provider=provider,
                model=model,
            )

    def update(self, activity_id: str, event: dict[str, Any]) -> None:
        etype = str(event.get("type") or "")
        if not etype:
            return
        with self._lock:
            rec = self._records.get(activity_id)
            if rec is None:
                return
            rec.last_event = etype
            rec.updated_at = time.time()
            if event.get("session_id") and not rec.session_id:
                rec.session_id = str(event.get("session_id") or "")
            if event.get("run_id") and not rec.run_id:
                rec.run_id = str(event.get("run_id") or "")
            if etype not in {"subagent_start", "subagent_done", "subagent_text", "subagent_reasoning"}:
                if event.get("trace_id"):
                    rec.trace_id = str(event.get("trace_id") or "")
                if event.get("turn_id"):
                    rec.turn_id = str(event.get("turn_id") or "")
            if etype == "iteration":
                rec.phase = "thinking"
                rec.iteration = _int(event.get("n"))
                rec.max_iterations = _int(event.get("max"))
            elif etype == "provider_start":
                rec.phase = "model"
                rec.provider = str(event.get("provider") or rec.provider)
                rec.model = str(event.get("model") or rec.model)
                rec.active_provider = _provider_label(rec.provider, rec.model)
                rec.provider_calls += 1
                rec.active_provider_started_at = time.time()
            elif etype == "provider_end":
                rec.active_provider = ""
                rec.active_provider_started_at = 0.0
                if event.get("status") == "error":
                    rec.phase = "provider error"
                    rec.last_error = str(event.get("error") or event.get("message") or "")
            elif etype == "assistant_delta":
                rec.phase = "streaming"
                rec.last_text_preview = _clip((rec.last_text_preview + str(event.get("text") or ""))[-240:], 240)
            elif etype == "assistant_message":
                rec.phase = "responding"
                rec.last_text_preview = _clip(str(event.get("text") or ""), 240)
            elif etype == "tool_start":
                rec.phase = "tool"
                rec.active_tool = str(event.get("name") or "")
                rec.active_tool_id = str(event.get("id") or "")
                rec.active_tool_started_at = time.time()
                rec.tool_calls += 1
            elif etype == "tool_result":
                rec.phase = "tool result"
                rec.last_tool = str(event.get("name") or rec.active_tool or rec.last_tool)
                if event.get("is_error"):
                    rec.tool_errors += 1
                rec.active_tool = ""
                rec.active_tool_id = ""
                rec.active_tool_started_at = 0.0
            elif etype == "subagent_start":
                rec.phase = "subagent"
                rec.subagents_active += 1
                rec.active_tool = f"subagent:{event.get('agent_type') or 'worker'}"
                rec.last_tool = rec.active_tool
                rec.active_tool_started_at = time.time()
            elif etype == "subagent_done":
                rec.phase = "subagent done"
                rec.subagents_active = max(0, rec.subagents_active - 1)
                rec.subagents_done += 1
                rec.active_tool = ""
                rec.active_tool_started_at = 0.0
            elif etype == "compacting":
                rec.phase = "compacting"
            elif etype == "compacted":
                rec.phase = "compacted"
                rec.compactions += 1
            elif etype == "review_started":
                rec.phase = f"review:{event.get('kind') or ''}".rstrip(":")
            elif etype == "review_done":
                rec.phase = "review done"
            elif etype == "budget_exhausted":
                rec.phase = "budget exhausted"
                rec.note = "iteration budget exhausted"
            elif etype == "cancelled":
                rec.phase = "cancelled"
                rec.status = "cancelled"
            elif etype == "error":
                rec.phase = "error"
                rec.status = "error"
                rec.last_error = str(event.get("message") or "")
            elif etype == "final":
                rec.phase = "final"
                rec.last_text_preview = _clip(str(event.get("text") or ""), 240)

    def finish(self, activity_id: str, *, status: str = "ok", error: str = "") -> dict[str, Any] | None:
        with self._lock:
            rec = self._records.pop(activity_id, None)
            if rec is None:
                return None
            rec.status = status or rec.status or "ok"
            rec.phase = "done" if rec.status == "ok" else rec.status
            rec.last_error = error or rec.last_error
            rec.updated_at = time.time()
            rec.ended_at = rec.updated_at
            rec.active_tool = ""
            rec.active_tool_id = ""
            rec.active_provider = ""
            rec.active_tool_started_at = 0.0
            rec.active_provider_started_at = 0.0
            out = rec.to_dict(rec.updated_at)
            self._completed.insert(0, rec)
            del self._completed[self.retain_completed:]
            return out

    def current(self, activity_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._records.get(activity_id)
            if rec is None:
                return None
            return rec.to_dict(time.time())

    def snapshot(self, *, include_recent: bool = True) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            active = [rec.to_dict(now) for rec in self._records.values()]
            active.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
            recent = [rec.to_dict(now) for rec in self._completed[: self.retain_completed]] if include_recent else []
        return {
            "active": active,
            "recent": recent,
            "active_count": len(active),
            "recent_count": len(recent),
        }


_STORE = ActivityStore()


def start(activity_id: str, **kwargs: Any) -> None:
    _STORE.start(activity_id, **kwargs)


def update(activity_id: str, event: dict[str, Any]) -> None:
    _STORE.update(activity_id, event)


def finish(activity_id: str, *, status: str = "ok", error: str = "") -> dict[str, Any] | None:
    return _STORE.finish(activity_id, status=status, error=error)


def current(activity_id: str) -> dict[str, Any] | None:
    return _STORE.current(activity_id)


def snapshot(*, include_recent: bool = True) -> dict[str, Any]:
    return _STORE.snapshot(include_recent=include_recent)


def _clip(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(ts: float) -> str:
    if not ts:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _provider_label(provider: str, model: str) -> str:
    provider = str(provider or "")
    model = str(model or "")
    return f"{provider}/{model}" if provider and model else provider or model
