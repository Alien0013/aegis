"""Local trace/span storage for agent runs.

Traces are provider-neutral and intentionally small: every row is a span that
dashboard, trajectory export, eval replay, and cost analytics can read without
having to reverse-engineer session messages.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, TypedDict

from . import config as cfg
from .types import new_id
from .util import now_iso


TRACE_FIELDS = (
    "trace_id", "session_id", "turn_id", "span_id", "parent_span_id", "kind", "status",
    "started_at", "ended_at", "provider", "model", "tool_name", "cost", "cache_read",
    "cache_write", "artifact_ref",
)


class TraceTimelineRow(TypedDict, total=False):
    index: int
    id: str
    row_id: str
    trace_id: str
    session_id: str
    turn_id: str
    span_id: str
    parent_span_id: str
    parent_id: str
    kind: str
    event: str
    span_kind: str
    status: str
    started_at: str
    ended_at: str
    offset_ms: int
    duration_ms: int
    latency_ms: int
    provider: str
    model: str
    api_mode: str
    tool_name: str
    label: str
    preview: str
    cost: float
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    artifact_ref: str
    attempt_index: int
    attempt_event: str
    data: dict[str, Any]
    prompt: dict[str, Any]
    attempt: dict[str, Any]
    text: str
    reason: str


def should_trace(config=None, trace_id: str | None = None) -> bool:
    getter = getattr(config, "get", None)
    if callable(getter) and not bool(getter("tracing.enabled", True)):
        return False
    try:
        raw = getter("tracing.sample_rate", 1.0) if callable(getter) else 1.0
        rate = float(raw)
    except (TypeError, ValueError):
        rate = 1.0
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    key = trace_id or new_id("trace")
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    return bucket < rate


class TraceStore:
    def __init__(self, path: str | Path | None = None):
        self.db = Path(path) if path else cfg.sub("traces.db")
        self._init()

    @classmethod
    def from_config(cls, config) -> "TraceStore":
        raw = config.get("tracing.path", "traces.db")
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = cfg.sub(str(raw))
        return cls(path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.OperationalError:
            pass
        return conn

    def _init(self) -> None:
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS spans (
                       span_id TEXT PRIMARY KEY,
                       trace_id TEXT NOT NULL,
                       session_id TEXT,
                       turn_id TEXT,
                       parent_span_id TEXT,
                       kind TEXT NOT NULL,
                       status TEXT,
                       started_at TEXT,
                       ended_at TEXT,
                       provider TEXT,
                       model TEXT,
                       tool_name TEXT,
                       cost REAL,
                       cache_read INTEGER,
                       cache_write INTEGER,
                       artifact_ref TEXT,
                       data TEXT
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, started_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_spans_session ON spans(session_id, started_at)")

    def start_span(
        self,
        *,
        trace_id: str | None = None,
        session_id: str = "",
        turn_id: str = "",
        parent_span_id: str = "",
        kind: str,
        provider: str = "",
        model: str = "",
        tool_name: str = "",
        artifact_ref: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        span = {
            "trace_id": trace_id or new_id("trace"),
            "session_id": session_id,
            "turn_id": turn_id,
            "span_id": new_id("span"),
            "parent_span_id": parent_span_id,
            "kind": kind,
            "status": "running",
            "started_at": now_iso(),
            "ended_at": "",
            "provider": provider,
            "model": model,
            "tool_name": tool_name,
            "cost": 0.0,
            "cache_read": 0,
            "cache_write": 0,
            "artifact_ref": artifact_ref,
            "data": data or {},
        }
        self.write_span(span)
        return span

    def write_span(self, span: dict[str, Any] | None = None, **kwargs: Any) -> None:
        span = {**(span or {}), **kwargs}
        if not span.get("trace_id"):
            span["trace_id"] = new_id("trace")
        if not span.get("span_id"):
            span["span_id"] = new_id("span")
        span.setdefault("kind", "span")
        span.setdefault("status", "ok")
        span.setdefault("started_at", now_iso())
        span.setdefault("ended_at", "")
        row = {k: span.get(k, "") for k in TRACE_FIELDS}
        row["cost"] = float(row.get("cost") or 0)
        row["cache_read"] = int(row.get("cache_read") or 0)
        row["cache_write"] = int(row.get("cache_write") or 0)
        row["data"] = json.dumps(span.get("data") or {}, default=str)
        with self._conn() as c:
            c.execute(
                """INSERT INTO spans
                   (span_id, trace_id, session_id, turn_id, parent_span_id, kind, status,
                    started_at, ended_at, provider, model, tool_name, cost, cache_read,
                    cache_write, artifact_ref, data)
                   VALUES
                   (:span_id, :trace_id, :session_id, :turn_id, :parent_span_id, :kind, :status,
                    :started_at, :ended_at, :provider, :model, :tool_name, :cost, :cache_read,
                    :cache_write, :artifact_ref, :data)
                   ON CONFLICT(span_id) DO UPDATE SET
                    status=excluded.status, ended_at=excluded.ended_at,
                    provider=excluded.provider, model=excluded.model, tool_name=excluded.tool_name,
                    cost=excluded.cost, cache_read=excluded.cache_read,
                    cache_write=excluded.cache_write, artifact_ref=excluded.artifact_ref,
                    data=excluded.data""",
                row,
            )

    def write_trace(
        self,
        spans: list[dict[str, Any]],
        *,
        trace_id: str | None = None,
        session_id: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        if not spans:
            raise ValueError("trace must contain at least one span")
        ids = {s.get("trace_id") for s in spans if s.get("trace_id")}
        if trace_id:
            ids.add(trace_id)
        if len(ids) > 1:
            raise ValueError("all spans in a trace must share one trace_id")
        tid = trace_id or next(iter(ids), None) or new_id("trace")
        for span in spans:
            row = {
                "trace_id": tid,
                "session_id": session_id or span.get("session_id", ""),
                "turn_id": turn_id or span.get("turn_id", ""),
                **span,
            }
            self.write_span(row)
        trace = self.get_trace(tid)
        if trace is None:
            raise ValueError("trace write failed")
        return trace

    def finish_span(self, span_id: str, *, status: str = "ok", **updates: Any) -> None:
        current = self.get_span(span_id)
        if not current:
            return
        if isinstance(current.get("data"), dict) and isinstance(updates.get("data"), dict):
            updates["data"] = {**current["data"], **updates["data"]}
        current.update(updates)
        current["status"] = status
        current["ended_at"] = updates.get("ended_at") or now_iso()
        self.write_span(current)

    @contextmanager
    def span(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        rec = self.start_span(**kwargs)
        try:
            yield rec
        except Exception as e:  # noqa: BLE001
            self.finish_span(rec["span_id"], status="error", data={**rec.get("data", {}), "error": str(e)})
            raise
        else:
            self.finish_span(rec["span_id"], status="ok")

    def get_span(self, trace_id: str, span_id: str | None = None) -> dict[str, Any] | None:
        if span_id is None:
            span_id = trace_id
            query = ("SELECT * FROM spans WHERE span_id=?", (span_id,))
        else:
            query = ("SELECT * FROM spans WHERE trace_id=? AND span_id=?", (trace_id, span_id))
        with self._conn() as c:
            row = c.execute(*query).fetchone()
        return _row(row) if row else None

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        spans = self.list_spans(trace_id=trace_id, limit=10_000)
        if not spans:
            return None
        statuses = {str(s.get("status", "")).lower() for s in spans}
        status = "error" if statuses & {"error", "failed", "failure"} else (
            "running" if "running" in statuses else "ok"
        )
        summary = _summarize_spans(spans)
        return {
            "trace_id": trace_id,
            "session_id": spans[0].get("session_id", ""),
            "turn_id": spans[0].get("turn_id", ""),
            "started_at": min((s.get("started_at") or "") for s in spans),
            "ended_at": max((s.get("ended_at") or "") for s in spans),
            "status": status,
            "span_count": len(spans),
            "spans": spans,
            "cost": sum(float(s.get("cost") or 0) for s in spans),
            "cache_read": sum(int(s.get("cache_read") or 0) for s in spans),
            "cache_write": sum(int(s.get("cache_write") or 0) for s in spans),
            "artifact_refs": [s["artifact_ref"] for s in spans if s.get("artifact_ref")],
            **summary,
        }

    def timeline(self, trace_id: str) -> list[TraceTimelineRow]:
        """Return a stable audit timeline derived from a trace's spans.

        Span storage stays provider-neutral; this export gives eval/audit callers
        normalized rows for turn, prompt, provider/model, provider attempts,
        tools, compactions, and terminal final/error events.
        """
        spans = self.list_spans(trace_id=trace_id, limit=10_000)
        return _timeline_rows(trace_id, spans)

    def list_spans(
        self,
        *,
        trace_id: str | None = None,
        session_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if trace_id:
            clauses.append("trace_id=?")
            args.append(trace_id)
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        if kind:
            clauses.append("kind=?")
            args.append(kind)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(limit)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM spans {where} ORDER BY started_at ASC LIMIT ?",
                args,
            ).fetchall()
        return [_row(r) for r in rows]

    def list_traces(self, *, session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where = "WHERE session_id=?" if session_id else ""
        args: list[Any] = [session_id] if session_id else []
        args.append(limit)
        with self._conn() as c:
            rows = c.execute(
                f"""SELECT trace_id, session_id, MIN(started_at) AS started_at,
                          MAX(ended_at) AS ended_at, COUNT(*) AS spans,
                          COUNT(*) AS span_count,
                          SUM(cost) AS cost, SUM(cache_read) AS cache_read,
                          SUM(cache_write) AS cache_write
                   FROM spans {where} GROUP BY trace_id, session_id
                   ORDER BY started_at DESC LIMIT ?""",
                args,
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            trace = self.get_trace(d["trace_id"])
            if trace:
                d["status"] = trace["status"]
                d["artifact_refs"] = trace["artifact_refs"]
                for key in (
                    "provider_calls", "tool_calls", "compactions", "error_spans",
                    "duration_ms", "latency_ms", "input_tokens", "output_tokens",
                    "providers", "models", "tools", "provider_counts",
                    "model_counts", "tool_counts", "kind_counts",
                ):
                    d[key] = trace.get(key)
            out.append(d)
        return out

    def retarget_session(self, trace_id: str, session_id: str) -> None:
        """Move all spans in a trace to the active session after a control action forks it."""
        if not trace_id or not session_id:
            return
        with self._conn() as c:
            c.execute("UPDATE spans SET session_id=? WHERE trace_id=?", (session_id, trace_id))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(started_at: str | None, ended_at: str | None) -> int:
    start = _parse_time(started_at)
    end = _parse_time(ended_at)
    if not start or not end:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _timeline_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _timeline_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _timeline_sort_key(started_at: str | None, sequence: int) -> tuple[float, str, int]:
    parsed = _parse_time(started_at)
    if parsed is None:
        return (float("inf"), str(started_at or ""), sequence)
    return (parsed.timestamp(), str(started_at or ""), sequence)


def _timeline_offset_ms(started_at: str | None, root_started_at: str | None) -> int:
    if not started_at or not root_started_at:
        return 0
    return _duration_ms(root_started_at, started_at)


def _timeline_row_kind(span_kind: str) -> str:
    if span_kind == "provider_call":
        return "provider"
    if span_kind in {"compaction", "compact"}:
        return "compaction"
    return span_kind or "span"


def _timeline_tool_target(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("command", "path", "query", "task", "url", "file"):
        value = args.get(key)
        if value:
            return str(value)[:280]
    if args:
        try:
            return json.dumps(args, sort_keys=True, default=str)[:280]
        except TypeError:
            return str(args)[:280]
    return ""


def _timeline_prompt_preview(data: dict[str, Any]) -> str:
    if "text" in data:
        return str(data.get("text") or "")[:280]
    parts = data.get("prompt_parts")
    if isinstance(parts, list):
        names = [str(part.get("name") or part.get("tier") or "")
                 for part in parts if isinstance(part, dict)]
        names = [name for name in names if name]
        if names:
            return ", ".join(names[:6])[:280]
    if data.get("system_prompt_hash"):
        return f"system_prompt_hash={data.get('system_prompt_hash')}"
    return ""


def _timeline_preview(kind: str, row: TraceTimelineRow, data: dict[str, Any]) -> str:
    if kind == "prompt":
        return _timeline_prompt_preview(data)
    if kind == "tool":
        target = _timeline_tool_target(data.get("args"))
        if target:
            return target
    if kind == "provider_attempt":
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        message = error.get("message") if isinstance(error, dict) else ""
        if message:
            return str(message)[:280]
    if kind == "provider":
        finish = data.get("finish_reason")
        if finish:
            return f"finish_reason={finish}"[:280]
    if kind == "compaction":
        before = data.get("messages_before")
        after = data.get("messages_after")
        if before is not None and after is not None:
            return f"{before} to {after} messages"
    for key in ("preview", "summary", "error", "reason", "message", "content", "text"):
        value = data.get(key)
        if value:
            return str(value)[:280]
    blocked = data.get("blocked")
    if isinstance(blocked, dict):
        return str(blocked.get("message") or blocked.get("reason") or "")[:280]
    if row.get("artifact_ref"):
        return str(row.get("artifact_ref") or "")[:280]
    return ""


def _timeline_label(kind: str, row: TraceTimelineRow, data: dict[str, Any]) -> str:
    if kind in {"provider", "model"}:
        bits = [str(row.get("provider") or "").strip(), str(row.get("model") or "").strip()]
        label = " / ".join(bit for bit in bits if bit)
        return label or ("Model call" if kind == "model" else "Provider call")
    if kind == "provider_attempt":
        bits = [str(row.get("provider") or "").strip(), str(row.get("model") or "").strip()]
        label = " / ".join(bit for bit in bits if bit) or "Provider attempt"
        event = str(row.get("attempt_event") or data.get("event") or "").strip()
        return f"{label} {event}".strip()
    if kind == "tool":
        return str(row.get("tool_name") or data.get("tool_name") or "Tool")
    if kind == "compaction":
        before = data.get("messages_before")
        after = data.get("messages_after")
        return f"Compaction {before} to {after}" if before is not None and after is not None else "Compaction"
    if kind == "prompt":
        return "Prompt audit"
    if kind == "turn":
        return "Agent turn"
    if kind == "final":
        return "Final response"
    if kind == "error":
        return "Error"
    return kind.replace("_", " ").title() or "Span"


def _timeline_base_row(
    trace_id: str,
    span: dict[str, Any],
    *,
    kind: str | None = None,
    row_suffix: str = "",
    started_at: str | None = None,
    ended_at: str | None = None,
    status: str | None = None,
    data: dict[str, Any] | None = None,
) -> TraceTimelineRow:
    raw_data = data if isinstance(data, dict) else (
        span.get("data") if isinstance(span.get("data"), dict) else {}
    )
    span_kind = str(span.get("kind") or "span")
    row_kind = kind or _timeline_row_kind(span_kind)
    span_id = str(span.get("span_id") or "")
    row_id = f"{span_id}:{row_suffix}" if row_suffix and span_id else (span_id or row_suffix)
    row_started_at = str(started_at if started_at is not None else span.get("started_at") or "")
    row_ended_at = str(ended_at if ended_at is not None else span.get("ended_at") or "")
    duration = raw_data.get("duration_ms")
    if duration is None:
        duration = span.get("duration_ms") if kind is None else _duration_ms(row_started_at, row_ended_at)
    artifact_ref = str(span.get("artifact_ref") or raw_data.get("artifact_ref") or "")
    row: TraceTimelineRow = {
        "id": row_id,
        "row_id": row_id,
        "trace_id": str(span.get("trace_id") or trace_id),
        "session_id": str(span.get("session_id") or ""),
        "turn_id": str(span.get("turn_id") or ""),
        "span_id": span_id,
        "parent_span_id": str(span.get("parent_span_id") or ""),
        "parent_id": str(span.get("parent_span_id") or ""),
        "kind": row_kind,
        "event": row_kind,
        "span_kind": span_kind,
        "status": str(status if status is not None else span.get("status") or "ok"),
        "started_at": row_started_at,
        "ended_at": row_ended_at,
        "duration_ms": _timeline_int(duration),
        "latency_ms": _timeline_int(duration),
        "provider": str(raw_data.get("provider") or span.get("provider") or ""),
        "model": str(raw_data.get("model") or span.get("model") or ""),
        "api_mode": str(raw_data.get("api_mode") or ""),
        "tool_name": str(raw_data.get("tool_name") or span.get("tool_name") or ""),
        "cost": _timeline_float(span.get("cost")),
        "input_tokens": _timeline_int(raw_data.get("input_tokens")),
        "output_tokens": _timeline_int(raw_data.get("output_tokens")),
        "cache_read": _timeline_int(span.get("cache_read") or raw_data.get("cache_read")),
        "cache_write": _timeline_int(span.get("cache_write") or raw_data.get("cache_write")),
        "artifact_ref": artifact_ref,
        "data": dict(raw_data),
    }
    row["label"] = _timeline_label(row_kind, row, raw_data)
    row["preview"] = _timeline_preview(row_kind, row, raw_data)
    return row


def _timeline_provider_attempts(span: dict[str, Any]) -> list[TraceTimelineRow]:
    data = span.get("data") if isinstance(span.get("data"), dict) else {}
    attempts = data.get("provider_attempts")
    if not isinstance(attempts, list):
        attempts = data.get("fallback_attempts")
    if not isinstance(attempts, list):
        return []
    rows: list[TraceTimelineRow] = []
    for pos, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            continue
        event = str(attempt.get("event") or "")
        status = str(attempt.get("status") or ("error" if event == "error" else "ok"))
        row = _timeline_base_row(
            str(span.get("trace_id") or ""),
            span,
            kind="provider_attempt",
            row_suffix=f"attempt:{pos}",
            status=status,
            data=dict(attempt),
        )
        row["attempt"] = dict(attempt)
        row["attempt_index"] = _timeline_int(attempt.get("index"))
        row["attempt_event"] = event
        row["duration_ms"] = _timeline_int(attempt.get("duration_ms"))
        row["latency_ms"] = row["duration_ms"]
        row["label"] = _timeline_label("provider_attempt", row, dict(attempt))
        row["preview"] = _timeline_preview("provider_attempt", row, dict(attempt))
        rows.append(row)
    return rows


def _timeline_prompt_row(trace_id: str, span: dict[str, Any]) -> TraceTimelineRow | None:
    data = span.get("data") if isinstance(span.get("data"), dict) else {}
    if "prompt" not in data:
        return None
    prompt = data.get("prompt")
    prompt_data = dict(prompt) if isinstance(prompt, dict) else {"text": str(prompt or "")}
    row = _timeline_base_row(trace_id, span, kind="prompt", row_suffix="prompt", data=prompt_data)
    row["prompt"] = prompt_data
    return row


def _timeline_terminal_row(trace_id: str, span: dict[str, Any]) -> TraceTimelineRow | None:
    if str(span.get("kind") or "") != "turn":
        return None
    data = span.get("data") if isinstance(span.get("data"), dict) else {}
    terminal_at = str(span.get("ended_at") or span.get("started_at") or "")
    if "text" in data:
        final_data = {
            "text": str(data.get("text") or ""),
            "reason": str(data.get("reason") or ""),
        }
        row = _timeline_base_row(
            trace_id,
            span,
            kind="final",
            row_suffix="final",
            started_at=terminal_at,
            ended_at=terminal_at,
            status=str(span.get("status") or "ok"),
            data=final_data,
        )
        row["text"] = final_data["text"]
        row["reason"] = final_data["reason"]
        return row
    if "error" in data or "blocked" in data:
        error_data: dict[str, Any] = {}
        if "error" in data:
            error_data["error"] = data.get("error")
        if "blocked" in data:
            error_data["blocked"] = data.get("blocked")
        if data.get("reason"):
            error_data["reason"] = data.get("reason")
        row = _timeline_base_row(
            trace_id,
            span,
            kind="error",
            row_suffix="error",
            started_at=terminal_at,
            ended_at=terminal_at,
            status=str(span.get("status") or "error"),
            data=error_data,
        )
        row["reason"] = str(error_data.get("reason") or "")
        return row
    return None


def _timeline_rows(trace_id: str, spans: list[dict[str, Any]]) -> list[TraceTimelineRow]:
    if not spans:
        return []
    ordered_spans = sorted(
        enumerate(spans),
        key=lambda item: (
            _timeline_sort_key(item[1].get("started_at"), 0),
            str(item[1].get("span_id") or ""),
            item[0],
        ),
    )
    starts = [str(span.get("started_at") or "") for _, span in ordered_spans if span.get("started_at")]
    root_started_at = min(starts) if starts else ""
    rows: list[tuple[tuple[float, str, int], TraceTimelineRow]] = []
    sequence = 0

    def add(row: TraceTimelineRow | None) -> None:
        nonlocal sequence
        if row is None:
            return
        row["offset_ms"] = _timeline_offset_ms(row.get("started_at"), root_started_at)
        rows.append((_timeline_sort_key(row.get("started_at"), sequence), row))
        sequence += 1

    for _, span in ordered_spans:
        span_kind = str(span.get("kind") or "span")
        add(_timeline_base_row(trace_id, span))
        if span_kind == "turn":
            add(_timeline_prompt_row(trace_id, span))
        if span_kind in {"provider_call", "model"}:
            for row in _timeline_provider_attempts(span):
                add(row)
        terminal = _timeline_terminal_row(trace_id, span)
        if terminal is not None:
            add(terminal)

    out = [row for _, row in sorted(rows, key=lambda item: item[0])]
    for index, row in enumerate(out):
        row["index"] = index
    return out


def _uniq(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _counts(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        if value:
            out[value] = out.get(value, 0) + 1
    return out


def _summarize_spans(spans: list[dict[str, Any]]) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for span in spans:
        kind = str(span.get("kind") or "span")
        kinds[kind] = kinds.get(kind, 0) + 1
    starts = [s.get("started_at") for s in spans if s.get("started_at")]
    ends = [s.get("ended_at") for s in spans if s.get("ended_at")]
    error_statuses = {"error", "failed", "failure"}
    providers = [str(s.get("provider") or "") for s in spans]
    models = [str(s.get("model") or "") for s in spans]
    tools = [str(s.get("tool_name") or "") for s in spans]
    duration = _duration_ms(min(starts) if starts else "", max(ends) if ends else "")
    return {
        "provider_calls": sum(1 for s in spans if s.get("kind") in {"provider_call", "model"}),
        "tool_calls": sum(1 for s in spans if s.get("kind") == "tool"),
        "compactions": sum(1 for s in spans if s.get("kind") in {"compaction", "compact"}),
        "error_spans": sum(1 for s in spans if str(s.get("status", "")).lower() in error_statuses),
        "duration_ms": duration,
        "latency_ms": duration,
        "input_tokens": sum(int((s.get("data") or {}).get("input_tokens") or 0) for s in spans),
        "output_tokens": sum(int((s.get("data") or {}).get("output_tokens") or 0) for s in spans),
        "providers": _uniq(providers),
        "models": _uniq(models),
        "tools": _uniq(tools),
        "provider_counts": _counts(providers),
        "model_counts": _counts(models),
        "tool_counts": _counts(tools),
        "kind_counts": kinds,
    }


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["data"] = json.loads(d.get("data") or "{}")
    except json.JSONDecodeError:
        d["data"] = {}
    duration = d["data"].get("duration_ms")
    if duration is None:
        duration = _duration_ms(d.get("started_at"), d.get("ended_at"))
    d["duration_ms"] = int(duration or 0)
    d["latency_ms"] = d["duration_ms"]
    return d


def enabled(config) -> bool:
    return bool(config.get("tracing.enabled", True))
