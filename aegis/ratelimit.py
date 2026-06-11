"""Rate-limit + credit telemetry: capture x-ratelimit-* / balance response headers.

Providers call ``record(headers)`` after a response; the latest snapshot per
provider is kept in memory (and the most recent overall). ``summary()`` renders
it for the /usage command and the dashboard. Best-effort — absent headers just
mean no snapshot. ``balance()`` separately exposes any credit/balance fields so
``/usage`` and ``aegis doctor`` can show remaining account credit when the
provider returns it (OpenRouter and several OpenAI-compatible gateways do)."""

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

# Credit/balance headers some providers return (OpenRouter, OpenAI-compatible gateways).
_CREDIT_FIELDS = (
    ("x-ratelimit-remaining-credits", "credits left"),
    ("x-credits-remaining", "credits left"),
    ("x-account-balance", "balance"),
    ("x-balance", "balance"),
    ("openai-organization-credit-remaining", "credits left"),
)


def record(headers, provider: str = "") -> None:
    try:
        h = {str(k).lower(): v for k, v in dict(headers).items()}
    except Exception:  # noqa: BLE001
        return
    found = {label: h[key] for key, label in _FIELDS if key in h}
    credit = {label: h[key] for key, label in _CREDIT_FIELDS if key in h}
    if not found and not credit:
        return
    from .util import now_iso
    with _lock:
        snap = {"at": now_iso(), **found, **credit}
        _latest[provider or "?"] = snap
        _latest["_recent"] = {"provider": provider or "?", **snap}
        if credit:
            _latest["_credit"] = {"provider": provider or "?", "at": now_iso(), **credit}


def latest() -> dict:
    with _lock:
        return dict(_latest)


def balance() -> dict:
    """Most recent credit/balance snapshot ({} when no provider has reported one)."""
    return latest().get("_credit", {})


def summary() -> str:
    snap = latest().get("_recent")
    if not snap:
        return ""
    parts = [f"{k}: {v}" for k, v in snap.items() if k not in ("at", "provider")]
    return f"rate limits ({snap['provider']}): " + " · ".join(parts) if parts else ""
