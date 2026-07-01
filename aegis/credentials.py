"""Credential pools: multiple API keys per provider with rotation strategies,
credential health, and persisted state shared across the whole process (so
subagents share too).

Keys merge two sources: ``credential_pools.<provider>.keys`` in config and the comma-split
value of the provider's API-key env var. Source suppression is sticky in the
shared state file, so removing an env/config-backed source is not undone on the
next reload. Failure policy (driven by the retry layer's error classification):
``billing`` / ``rate_limit`` exhausts the current key for ``cooldown_hours`` or
an explicit reset timestamp, terminal ``auth`` errors mark the key dead, and
recoverable ``auth`` rotates to the next key. Account-scoped rate limits can
also trip a provider-wide breaker shared across sessions.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config as cfg
from .util import atomic_write, read_text

_LOCK = threading.Lock()
_POOLS: dict[str, "CredentialPool"] = {}
_STATUS_OK = "ok"
_STATUS_EXHAUSTED = "exhausted"
_STATUS_DEAD = "dead"
_EXHAUSTED_KINDS = {"billing", "rate_limit", "429"}
_TERMINAL_AUTH_REASONS = (
    "token_invalidated",
    "token_revoked",
    "invalid_token",
    "invalid_grant",
    "unauthorized_client",
    "refresh_token_reused",
    "account_disabled",
    "account_suspended",
    "account_deactivated",
    "key_revoked",
    "api_key_revoked",
    "project_disabled",
)
_AUTH_KINDS = {"auth", "401", "unauthorized", *_TERMINAL_AUTH_REASONS}
_REASON_KEYS = ("reason", "code", "type", "error", "error_code")
_MESSAGE_KEYS = ("message", "error_description", "description", "body")
_ACCOUNT_LIMIT_KINDS = {"account_rate_limit", "account_limit", "provider_rate_limit"}
_ACCOUNT_LIMIT_SCOPES = {"account", "account_bucket", "provider_account"}


def _state_path():
    return cfg.sub("credential_state.json")


def _load_state() -> dict:
    raw = read_text(_state_path())
    try:
        d = json.loads(raw) if raw.strip() else {}
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save_state(d: dict) -> None:
    atomic_write(_state_path(), json.dumps(d, indent=2, sort_keys=True))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mask(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "key"


def _parse_reset_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        ts = float(text)
    except ValueError:
        pass
    else:
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _coerce_error_context(error_context: Any) -> dict[str, Any]:
    if error_context is None:
        return {}
    if isinstance(error_context, str):
        return {"message": error_context}
    if isinstance(error_context, dict):
        out: dict[str, Any] = {}

        def merge(node: dict[str, Any]) -> None:
            for k, v in node.items():
                if k == "error" and isinstance(v, dict):
                    merge(v)
                    continue
                out.setdefault(str(k), v)

        merge(error_context)
        return out

    out = {}
    for attr in (
        "status",
        "status_code",
        "code",
        "reason",
        "message",
        "error",
        "error_description",
        "body",
        "reset_at",
    ):
        try:
            value = getattr(error_context, attr)
        except Exception:  # noqa: BLE001
            continue
        if value is not None:
            out[attr] = value
    response = getattr(error_context, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        out.setdefault("status_code", status_code)
    if "message" not in out:
        text = str(error_context).strip()
        if text:
            out["message"] = text
    return out


def _context_text(ctx: dict[str, Any], keys: tuple[str, ...], pool_keys: list[str]) -> str:
    for key in keys:
        value = ctx.get(key)
        if value is None or isinstance(value, (dict, list, tuple)):
            continue
        text = re.sub(r"\s+", " ", str(value)).strip()
        if not text:
            continue
        for secret in pool_keys:
            if secret:
                text = text.replace(secret, "[credential]")
        return text[:500]
    return ""


def _context_status_code(kind: str, ctx: dict[str, Any]) -> int | None:
    for key in ("status_code", "status"):
        value = ctx.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    if kind == "429" or kind == "rate_limit":
        return 429
    if kind in {"401", "auth"}:
        return 401
    if kind == "billing":
        return 402
    return None


def _terminal_auth_context(kind: str, ctx: dict[str, Any], pool_keys: list[str]) -> bool:
    values = [kind]
    values.extend(_context_text(ctx, keys, pool_keys) for keys in (_REASON_KEYS, _MESSAGE_KEYS))
    haystack = " ".join(v for v in values if v).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", haystack)
    if any(reason in normalized for reason in _TERMINAL_AUTH_REASONS):
        return True
    return "token" in normalized and ("invalidated" in normalized or "revoked" in normalized)


def _config_source(provider: str) -> str:
    return f"config:credential_pools.{provider}.keys"


def _split_env_keys(value: str | None) -> list[str]:
    if not value:
        return []
    return [k.strip() for k in value.split(",") if k.strip()]


def _source_payload(reason: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"suppressed_at": _now().isoformat()}
    if reason:
        payload["reason"] = str(reason)
    return payload


def suppressed_sources(provider: str) -> dict[str, dict[str, Any]]:
    """Return sticky source-suppression state for a provider."""
    section = _load_state().get(provider, {})
    raw = section.get("suppressed_sources", {}) if isinstance(section, dict) else {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for source, payload in raw.items():
        out[str(source)] = dict(payload) if isinstance(payload, dict) else {}
    return out


def is_source_suppressed(provider: str, source: str) -> bool:
    return str(source or "") in suppressed_sources(provider)


def _account_breaker_requested(kind: str, ctx: dict[str, Any]) -> bool:
    kind = str(kind or "").lower()
    if kind in _ACCOUNT_LIMIT_KINDS:
        return True
    if ctx.get("account_breaker") is True:
        return True
    scope = str(ctx.get("scope") or ctx.get("bucket_scope") or "").lower()
    if scope in _ACCOUNT_LIMIT_SCOPES:
        return True
    return False


class CredentialPool:
    def __init__(self, provider: str, keys: list[str], strategy: str = "fill_first",
                 cooldown_hours: float = 24.0, *,
                 sources: dict[str, str] | None = None,
                 max_concurrent_per_key: int = 1):
        self.provider = provider
        self.keys = list(dict.fromkeys(k for k in keys if k))   # dedup, preserve order
        self.sources = {
            key: str((sources or {}).get(key) or "manual")
            for key in self.keys
        }
        self.strategy = strategy
        self.cooldown_hours = cooldown_hours
        self.max_concurrent_per_key = max(1, int(max_concurrent_per_key or 1))
        self._idx = 0
        self._lease_lock = threading.Lock()
        self._active_leases: dict[str, int] = {}

    # -- persisted per-provider state ---------------------------------------
    def _section(self, st: dict) -> dict:
        return st.setdefault(self.provider, {})

    def _benched(self) -> dict:
        return _load_state().get(self.provider, {}).get("cooldown", {})

    def _usage(self) -> dict:
        return _load_state().get(self.provider, {}).get("used", {})

    def _account_breaker(self, section: dict, now: datetime, *, clear_expired: bool = False) -> dict | None:
        breaker = section.get("account_breaker")
        if not isinstance(breaker, dict):
            return None
        reset_at = _parse_reset_at(breaker.get("reset_at"))
        if reset_at is not None and reset_at > now:
            return breaker
        if clear_expired:
            section.pop("account_breaker", None)
        return None

    def _entry_for(self, section: dict, key: str) -> dict:
        ident = _mask(key)
        entries = section.get("entries", {})
        entry = entries.get(ident, {}) if isinstance(entries, dict) else {}
        if isinstance(entry, dict) and entry:
            return dict(entry)
        cooldown = section.get("cooldown", {})
        reset_at = cooldown.get(ident) if isinstance(cooldown, dict) else None
        if reset_at:
            return {"status": _STATUS_EXHAUSTED, "reason": "billing", "reset_at": reset_at}
        return {"status": _STATUS_OK}

    def _refresh_expired(self, section: dict, now: datetime) -> bool:
        changed = False
        had_account_breaker = "account_breaker" in section
        self._account_breaker(section, now, clear_expired=True)
        if had_account_breaker and "account_breaker" not in section:
            changed = True
        entries = section.get("entries")
        if isinstance(entries, dict):
            for ident, entry in list(entries.items()):
                if not isinstance(entry, dict) or entry.get("status") != _STATUS_EXHAUSTED:
                    continue
                reset_at = _parse_reset_at(entry.get("reset_at"))
                if reset_at is None or reset_at <= now:
                    entries[ident] = {"status": _STATUS_OK, "updated_at": now.isoformat()}
                    changed = True
        cooldown = section.get("cooldown")
        if isinstance(cooldown, dict):
            for ident, reset_at in list(cooldown.items()):
                parsed = _parse_reset_at(reset_at)
                if parsed is None or parsed <= now:
                    cooldown.pop(ident, None)
                    changed = True
        return changed

    def _is_available(self, section: dict, key: str, now: datetime) -> bool:
        if self._account_breaker(section, now) is not None:
            return False
        entry = self._entry_for(section, key)
        status = str(entry.get("status") or _STATUS_OK)
        if status == _STATUS_DEAD:
            return False
        if status == _STATUS_EXHAUSTED:
            reset_at = _parse_reset_at(entry.get("reset_at"))
            return reset_at is None or reset_at <= now
        return True

    def _state_for_read(self) -> dict:
        with _LOCK:
            st = _load_state()
            section = self._section(st)
            if self._refresh_expired(section, _now()):
                _save_state(st)
            return section

    def available_keys(self) -> list[str]:
        """Keys that are not exhausted or dead. Returns [] when every key is unavailable."""
        section, now = self._state_for_read(), _now()
        return [k for k in self.keys if self._is_available(section, k, now)]

    def has_available(self) -> bool:
        return bool(self.available_keys())

    def suppress_source(self, source: str, reason: str | None = None) -> int:
        """Suppress a backing source and remove its current in-memory entries.

        This mirrors the upstream sticky removal contract: once a source such as
        ``env:XAI_API_KEY`` is suppressed, pool reloads skip it even when the
        variable remains exported by the parent shell.
        """
        source = str(source or "").strip()
        if not source:
            return 0
        removed_keys = [key for key in self.keys if self.sources.get(key) == source]
        removed_ids = {_mask(key) for key in removed_keys}
        with _LOCK:
            st = _load_state()
            section = self._section(st)
            section.setdefault("suppressed_sources", {})[source] = _source_payload(reason)
            for field in ("entries", "cooldown", "used"):
                bucket = section.get(field)
                if isinstance(bucket, dict):
                    for ident in removed_ids:
                        bucket.pop(ident, None)
            _save_state(st)
        if removed_keys:
            self.keys = [key for key in self.keys if key not in removed_keys]
            for key in removed_keys:
                self.sources.pop(key, None)
            with self._lease_lock:
                for ident in removed_ids:
                    self._active_leases.pop(ident, None)
        return len(removed_keys)

    def unsuppress_source(self, source: str) -> bool:
        source = str(source or "").strip()
        if not source:
            return False
        with _LOCK:
            st = _load_state()
            section = self._section(st)
            suppressed = section.get("suppressed_sources")
            if not isinstance(suppressed, dict) or source not in suppressed:
                return False
            suppressed.pop(source, None)
            _save_state(st)
            _POOLS.pop(self.provider, None)
            return True

    def acquire_lease(self, key: str | None = None) -> str | None:
        """Acquire a soft, non-blocking lease on an available key."""
        section, now, used = self._state_for_read(), _now(), self._usage()
        if key is not None:
            if key not in self.keys or not self._is_available(section, key, now):
                return None
            ident = _mask(key)
            with self._lease_lock:
                self._active_leases[ident] = self._active_leases.get(ident, 0) + 1
            self._idx = self.keys.index(key)
            return key

        available = [candidate for candidate in self.keys if self._is_available(section, candidate, now)]
        if not available:
            return None
        with self._lease_lock:
            below_cap = [
                candidate for candidate in available
                if self._active_leases.get(_mask(candidate), 0) < self.max_concurrent_per_key
            ]
            candidates = below_cap if below_cap else available

            def sort_key(candidate: str) -> tuple[int, int, int]:
                ident = _mask(candidate)
                return (
                    self._active_leases.get(ident, 0),
                    int(used.get(ident, 0)) if self.strategy == "least_used" else 0,
                    self.keys.index(candidate),
                )

            chosen = min(candidates, key=sort_key)
            ident = _mask(chosen)
            self._active_leases[ident] = self._active_leases.get(ident, 0) + 1
        self._idx = self.keys.index(chosen)
        return chosen

    def release_lease(self, key: str | None) -> None:
        if not key:
            return
        ident = _mask(key)
        with self._lease_lock:
            count = self._active_leases.get(ident, 0)
            if count <= 1:
                self._active_leases.pop(ident, None)
            else:
                self._active_leases[ident] = count - 1

    def record_account_limit(self, error_context: Any = None, *, default_seconds: float | None = None) -> None:
        ctx = _coerce_error_context(error_context)
        now = _now()
        reset_at = _parse_reset_at(ctx.get("reset_at"))
        if reset_at is None:
            reset_after = ctx.get("retry_after") or ctx.get("retry-after") or ctx.get("reset_after")
            try:
                reset_at = now + timedelta(seconds=float(reset_after))
            except (TypeError, ValueError):
                reset_at = now + timedelta(seconds=float(default_seconds or self.cooldown_hours * 3600))
        with _LOCK:
            st = _load_state()
            section = self._section(st)
            entry = {
                "status": _STATUS_EXHAUSTED,
                "updated_at": now.isoformat(),
                "reset_at": reset_at.isoformat(),
            }
            reason = _context_text(ctx, _REASON_KEYS, self.keys) or "account_rate_limit"
            message = _context_text(ctx, _MESSAGE_KEYS, self.keys)
            status_code = _context_status_code("rate_limit", ctx)
            if status_code is not None:
                entry["status_code"] = status_code
            if reason:
                entry["reason"] = reason
            if message:
                entry["message"] = message
            section["account_breaker"] = entry
            _save_state(st)

    def account_limit_remaining(self) -> float | None:
        with _LOCK:
            st = _load_state()
            section = self._section(st)
            now = _now()
            had_breaker = "account_breaker" in section
            breaker = self._account_breaker(section, now, clear_expired=True)
            if breaker is None:
                if had_breaker and "account_breaker" not in section:
                    _save_state(st)
                return None
            reset_at = _parse_reset_at(breaker.get("reset_at"))
            if reset_at is None:
                return None
            return max(0.0, (reset_at - now).total_seconds())

    def clear_account_limit(self) -> bool:
        with _LOCK:
            st = _load_state()
            section = self._section(st)
            existed = "account_breaker" in section
            section.pop("account_breaker", None)
            if existed:
                _save_state(st)
            return existed

    def lease_counts(self) -> dict[str, int]:
        with self._lease_lock:
            return dict(self._active_leases)

    def current(self) -> str | None:
        avail = self.available_keys()
        if not avail:
            return None
        if self.strategy == "random":
            return random.choice(avail)
        if self.strategy == "least_used":
            used = self._usage()
            return min(avail, key=lambda k: used.get(_mask(k), 0))
        if not self.keys:
            return None
        start = self._idx % len(self.keys)
        for offset in range(len(self.keys)):
            candidate = self.keys[(start + offset) % len(self.keys)]
            if candidate in avail:
                return candidate
        return None

    def rotate(self, key: str | None = None) -> bool:
        if len(self.keys) <= 1:
            return False
        avail = self.available_keys()
        if not avail:
            return False
        if self.strategy == "random":
            return len(avail) > 1 or (key is not None and avail[0] != key)
        if self.strategy == "least_used":
            current = self.current()
            return current is not None and current != key
        current = key if key in self.keys else self.current()
        if current not in self.keys:
            return False
        start = self.keys.index(current)
        for offset in range(1, len(self.keys) + 1):
            candidate = self.keys[(start + offset) % len(self.keys)]
            if candidate in avail:
                if candidate == current:
                    return False
                self._idx = self.keys.index(candidate)
                return True
        return False

    def report(self, kind: str, error_context=None, *, key: str | None = None) -> bool:
        """Apply pool policy for a classified failure.

        ``billing`` / ``rate_limit`` exhaust the active key and rotate; terminal
        auth reasons mark it dead. Returns True when a different usable key can
        be tried immediately. The second positional argument remains compatible
        with the old explicit-key form when it matches a configured key.
        """
        if key is None and isinstance(error_context, str) and error_context in self.keys:
            key = error_context
            error_context = None
        ctx = _coerce_error_context(error_context)
        kind = str(kind or "").lower()
        if _account_breaker_requested(kind, ctx):
            self.record_account_limit(ctx)
            return False
        key = key or self.current()
        if not key:
            return False
        status_code = _context_status_code(kind, ctx)
        is_auth = kind in _AUTH_KINDS or status_code == 401
        if is_auth and _terminal_auth_context(kind, ctx, self.keys):
            with _LOCK:
                st = _load_state()
                now = _now()
                entry = {
                    "status": _STATUS_DEAD,
                    "updated_at": now.isoformat(),
                }
                if status_code is not None:
                    entry["status_code"] = status_code
                reason = _context_text(ctx, _REASON_KEYS, self.keys)
                message = _context_text(ctx, _MESSAGE_KEYS, self.keys)
                if reason:
                    entry["reason"] = reason
                if message:
                    entry["message"] = message
                section = self._section(st)
                section.setdefault("entries", {})[_mask(key)] = entry
                cooldown = section.get("cooldown")
                if isinstance(cooldown, dict):
                    cooldown.pop(_mask(key), None)
                _save_state(st)
            return self.rotate(key)
        if kind in _EXHAUSTED_KINDS:
            with _LOCK:
                st = _load_state()
                section = self._section(st)
                now = _now()
                reset_at = _parse_reset_at(ctx.get("reset_at")) or (
                    now + timedelta(hours=self.cooldown_hours)
                )
                entry = {
                    "status": _STATUS_EXHAUSTED,
                    "updated_at": now.isoformat(),
                    "reset_at": reset_at.isoformat(),
                }
                if status_code is not None:
                    entry["status_code"] = status_code
                reason = _context_text(ctx, _REASON_KEYS, self.keys) or kind
                message = _context_text(ctx, _MESSAGE_KEYS, self.keys)
                if reason:
                    entry["reason"] = reason
                if message:
                    entry["message"] = message
                ident = _mask(key)
                section.setdefault("entries", {})[ident] = entry
                section.setdefault("cooldown", {})[ident] = entry["reset_at"]
                _save_state(st)
            return self.rotate(key)
        if is_auth:
            return self.rotate(key)
        return False

    def record_use(self, key: str | None = None) -> None:
        key = key or self.current()
        if not key:
            return
        with _LOCK:
            st = _load_state()
            used = self._section(st).setdefault("used", {})
            used[_mask(key)] = int(used.get(_mask(key), 0)) + 1
            _save_state(st)

    def entries(self) -> list[dict]:
        section, now, used = self._state_for_read(), _now(), self._usage()
        out = []
        for key in self.keys:
            ident = _mask(key)
            entry = self._entry_for(section, key)
            status = str(entry.get("status") or _STATUS_OK)
            available = self._is_available(section, key, now)
            if available and status == _STATUS_EXHAUSTED:
                status = _STATUS_OK
            row = {
                "id": ident,
                "source": self.sources.get(key, "manual"),
                "status": status,
                "available": available,
                "used": int(used.get(ident, 0)),
            }
            for field in ("status_code", "reason", "message", "reset_at", "updated_at"):
                if entry.get(field) is not None:
                    row[field] = entry[field]
            out.append(row)
        return out

    def status(self) -> dict:
        entries = self.entries()
        benched = self._benched()
        section = self._state_for_read()
        breaker = section.get("account_breaker") if isinstance(section, dict) else None
        dead = [row["id"] for row in entries if row["status"] == _STATUS_DEAD]
        exhausted = [
            row["id"] for row in entries
            if row["status"] == _STATUS_EXHAUSTED and not row["available"]
        ]
        return {
            "provider": self.provider, "strategy": self.strategy, "keys": len(self.keys),
            "available": len(self.available_keys()), "cooldown_hours": self.cooldown_hours,
            "has_available": self.has_available(), "benched": {k: v for k, v in benched.items()},
            "dead": dead, "exhausted": exhausted, "entries": entries,
            "suppressed_sources": suppressed_sources(self.provider),
            "leases": self.lease_counts(),
            "account_breaker": dict(breaker) if isinstance(breaker, dict) else None,
        }


def pool_for(provider: str, env_vars: list[str] | None, config) -> CredentialPool | None:
    """Build (once) and return the shared pool for ``provider``, or None if no keys exist.
    Cached process-wide so every agent and subagent shares rotation state."""
    with _LOCK:
        if provider in _POOLS:
            return _POOLS[provider]
    cfg_pool = ((config.get("credential_pools", {}) or {}).get(provider, {}) if config else {}) or {}
    records: list[tuple[str, str]] = []

    def add_records(values: list[str], source: str) -> None:
        if is_source_suppressed(provider, source):
            return
        for value in values:
            key = str(value or "").strip()
            if key:
                records.append((key, source))

    raw_config_keys = [str(k).strip() for k in list(cfg_pool.get("keys", []) or []) if str(k).strip()]
    raw_env_records: list[tuple[str, list[str]]] = []
    for var in (env_vars or []):
        keys_for_var = _split_env_keys(os.environ.get(var))
        if keys_for_var:
            raw_env_records.append((var, keys_for_var))

    if raw_config_keys:
        add_records(raw_config_keys, _config_source(provider))
    for var, keys_for_var in raw_env_records:
        add_records(keys_for_var, f"env:{var}")

    seen: dict[str, str] = {}
    for key, source in records:
        seen.setdefault(key, source)
    keys = list(seen)
    if not keys and not raw_config_keys and not raw_env_records:
        return None
    max_concurrent = int(cfg_pool.get("max_concurrent_per_key", 1) or 1)
    pool = CredentialPool(provider, keys, str(cfg_pool.get("strategy", "fill_first")),
                          float(cfg_pool.get("cooldown_hours", 24)),
                          sources=seen,
                          max_concurrent_per_key=max_concurrent)
    with _LOCK:
        _POOLS.setdefault(provider, pool)
        return _POOLS[provider]


def reset() -> None:
    """Drop the cached pools (tests / config reloads)."""
    with _LOCK:
        _POOLS.clear()


def reset_provider_state(provider: str | None = None) -> int:
    """Clear persisted credential-pool cooldown/usage state.

    Configured keys remain in config.yaml; this only forgets runtime state such
    as billing cooldowns and least-used counters. Sticky source suppressions are
    preserved so removed env/config sources do not reappear after a reset.
    Returns the number of provider sections touched so CLIs can report whether
    anything was cleared.
    """
    def retained_section(section: dict) -> dict | None:
        suppressed = section.get("suppressed_sources") if isinstance(section, dict) else None
        if isinstance(suppressed, dict) and suppressed:
            return {"suppressed_sources": suppressed}
        return None

    with _LOCK:
        state = _load_state()
        if provider:
            removed = 1 if provider in state else 0
            if provider in state:
                retained = retained_section(state.get(provider, {}))
                if retained is None:
                    state.pop(provider, None)
                else:
                    state[provider] = retained
        else:
            removed = len(state)
            state = {
                name: retained
                for name, section in state.items()
                if (retained := retained_section(section)) is not None
            }
        _save_state(state)
        _POOLS.clear()
        return removed


def cmd_auth_pool(args, config) -> int:
    """`aegis auth pool [provider]` — show configured credential pools and their state."""
    from .providers.registry import _specs_for
    specs = _specs_for(config)
    only = getattr(args, "name", None)
    shown = 0
    for name, spec in sorted(specs.items()):
        if only and name != only:
            continue
        pool = pool_for(name, list(getattr(spec, "env_vars", []) or []), config)
        if not pool:
            continue
        s = pool.status()
        shown += 1
        print(f"  {name:<14} {s['keys']} key(s) · {s['available']} available · "
              f"strategy={s['strategy']} · cooldown={s['cooldown_hours']}h"
              + (f" · benched={list(s['benched'])}" if s["benched"] else ""))
    if not shown:
        print("  no credential pools configured (single keys are used directly).")
    return 0
