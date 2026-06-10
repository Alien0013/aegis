"""Rate-limit telemetry: capture x-ratelimit-* response headers for /usage.

Providers call ``record(headers)`` after a response; the latest snapshot per
provider is kept in memory (and the most recent overall). ``summary()`` renders
it for the /usage command and the dashboard. Best-effort — absent headers just
mean no snapshot."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_latest: dict = {}

_FIELDS = (
    ("x-ratelimit-remaining-requests", "requests left (min)"),
    ("x-ratelimit-remaining-requests-1h", "requests left (hr)"),
    ("x-ratelimit-remaining-tokens", "tokens left (min)"),
    ("x-ratelimit-remaining-tokens-1h", "tokens left (hr)"),
    ("x-ratelimit-limit-requests", "request cap (min)"),
    ("x-ratelimit-limit-tokens", "token cap (min)"),
    ("anthropic-ratelimit-requests-remaining", "requests left"),
    ("anthropic-ratelimit-tokens-remaining", "tokens left"),
    ("anthropic-ratelimit-tokens-reset", "tokens reset"),
)


def record(headers, provider: str = "") -> None:
    try:
        h = {str(k).lower(): v for k, v in dict(headers).items()}
    except Exception:  # noqa: BLE001
        return
    found = {label: h[key] for key, label in _FIELDS if key in h}
    if not found:
        return
    from .util import now_iso
    with _lock:
        _latest[provider or "?"] = {"at": now_iso(), **found}
        _latest["_recent"] = {"provider": provider or "?", "at": now_iso(), **found}


def latest() -> dict:
    with _lock:
        return dict(_latest)


def summary() -> str:
    snap = latest().get("_recent")
    if not snap:
        return ""
    parts = [f"{k}: {v}" for k, v in snap.items() if k not in ("at", "provider")]
    return f"rate limits ({snap['provider']}): " + " · ".join(parts) if parts else ""
