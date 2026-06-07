"""Usage analytics over conversation history and stored sessions.

Aggregates activity from two local sources — the append-only message log
(``memories/history.jsonl``, via :class:`aegis.memory.History`) and the SQLite
:class:`aegis.session.SessionStore` — into a small dict of metrics: message and
session counts, per-day activity, the busiest days, and a rough token estimate
(~4 chars/token over message content). Everything is computed locally; no
network or external service is involved.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config as cfg
from .constants import CHARS_PER_TOKEN
from .memory import History
from .session import SessionStore

# Sources we know how to read. ``None`` means "all of them".
SOURCES = ("history", "sessions")


def _parse_ts(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; return None on anything unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _day(dt: datetime) -> str:
    return dt.date().isoformat()


def _iter_history(cutoff: datetime | None) -> list[tuple[datetime, str]]:
    """Yield (timestamp, content) for each history line within the window."""
    history = History()
    raw = history.path.read_text(encoding="utf-8") if history.path.exists() else ""
    out: list[tuple[datetime, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            import json

            rec = json.loads(line)
        except ValueError:
            continue
        dt = _parse_ts(rec.get("ts", ""))
        if dt is None or (cutoff is not None and dt < cutoff):
            continue
        out.append((dt, str(rec.get("content", "") or "")))
    return out


def _iter_sessions(cutoff: datetime | None) -> tuple[int, list[tuple[datetime, str]]]:
    """Return (session_count, [(timestamp, content), ...]) within the window.

    Loads each session's full message list; sessions are attributed to the day
    they were last updated, and every message's content contributes to tokens.
    """
    store = SessionStore()
    session_count = 0
    msgs: list[tuple[datetime, str]] = []
    for meta in store.list(limit=10_000):
        updated = _parse_ts(meta.get("updated_at", "")) or _parse_ts(meta.get("created_at", ""))
        if updated is None or (cutoff is not None and updated < cutoff):
            continue
        session_count += 1
        session = store.load(meta["id"])
        if session is None:
            continue
        for m in session.messages:
            content = m.content or ""
            if content:
                msgs.append((updated, content))
    return session_count, msgs


def insights(days: int = 30, source: str | None = None) -> dict[str, Any]:
    """Compute usage analytics over the last ``days`` (0/negative = all time).

    ``source`` selects the data source: ``"history"``, ``"sessions"``, or
    ``None`` for both. Returns a JSON-serializable dict.
    """
    if source is not None and source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES} or None, got {source!r}")

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days) if days and days > 0 else None
    )

    events: list[tuple[datetime, str]] = []
    total_sessions = 0
    if source in (None, "history"):
        events.extend(_iter_history(cutoff))
    if source in (None, "sessions"):
        total_sessions, session_msgs = _iter_sessions(cutoff)
        events.extend(session_msgs)

    per_day: Counter[str] = Counter()
    total_chars = 0
    timestamps: list[datetime] = []
    for dt, content in events:
        per_day[_day(dt)] += 1
        total_chars += len(content)
        timestamps.append(dt)

    top_active_days = [
        {"date": d, "count": c}
        for d, c in sorted(per_day.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]

    return {
        "days": days,
        "source": source or "all",
        "total_sessions": total_sessions,
        "total_messages": len(events),
        "messages_per_day": dict(sorted(per_day.items())),
        "top_active_days": top_active_days,
        "approx_tokens": total_chars // CHARS_PER_TOKEN,
        "first_activity": min(timestamps).isoformat() if timestamps else None,
        "last_activity": max(timestamps).isoformat() if timestamps else None,
    }


def render(d: dict[str, Any]) -> str:
    """Pretty multi-line text report from an :func:`insights` dict."""
    scope = "all time" if not d.get("days") or d["days"] <= 0 else f"last {d['days']} days"
    lines = [
        f"AEGIS usage insights — {scope} (source: {d.get('source', 'all')})",
        "─" * 48,
        f"  sessions      {d['total_sessions']:>8,}",
        f"  messages      {d['total_messages']:>8,}",
        f"  approx tokens {d['approx_tokens']:>8,}",
    ]

    first, last = d.get("first_activity"), d.get("last_activity")
    if first:
        lines.append(f"  first active  {first[:19].replace('T', ' '):>19}")
    if last:
        lines.append(f"  last active   {last[:19].replace('T', ' '):>19}")

    top = d.get("top_active_days") or []
    if top:
        peak = max(row["count"] for row in top) or 1
        lines.append("")
        lines.append("  Top active days:")
        for row in top:
            bar = "█" * max(1, round(20 * row["count"] / peak))
            lines.append(f"    {row['date']}  {row['count']:>5}  {bar}")
    else:
        lines.append("")
        lines.append("  No activity recorded in this window.")

    return "\n".join(lines)


def cmd_insights(args, config: cfg.Config) -> int:
    """CLI: ``aegis insights [--days N] [--source history|sessions]``."""
    days = getattr(args, "days", 30)
    source = getattr(args, "source", None)
    data = insights(days=days, source=source)
    if getattr(args, "json", False):
        import json

        print(json.dumps(data, indent=2))
    else:
        print(render(data))
    return 0
