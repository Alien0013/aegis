"""Credential pools: multiple API keys per provider with rotation strategies, billing
cooldowns, and persisted state shared across the whole process (so subagents share too).

Keys merge two sources: ``credential_pools.<provider>.keys`` in config and the comma-split
value of the provider's API-key env var. Failure policy (driven by the retry layer's error
classification): ``billing`` (402 / quota) benches the current key for ``cooldown_hours`` then
rotates; ``rate_limit`` (429) and ``auth`` (401) rotate to the next key.
"""

from __future__ import annotations

import json
import os
import random
import threading
from datetime import datetime, timedelta, timezone

from . import config as cfg
from .util import atomic_write, read_text

_LOCK = threading.Lock()
_POOLS: dict[str, "CredentialPool"] = {}


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


class CredentialPool:
    def __init__(self, provider: str, keys: list[str], strategy: str = "fill_first",
                 cooldown_hours: float = 24.0):
        self.provider = provider
        self.keys = list(dict.fromkeys(k for k in keys if k))   # dedup, preserve order
        self.strategy = strategy
        self.cooldown_hours = cooldown_hours
        self._idx = 0

    # -- persisted per-provider state ---------------------------------------
    def _section(self, st: dict) -> dict:
        return st.setdefault(self.provider, {})

    def _benched(self) -> dict:
        return _load_state().get(self.provider, {}).get("cooldown", {})

    def _usage(self) -> dict:
        return _load_state().get(self.provider, {}).get("used", {})

    def available_keys(self) -> list[str]:
        """Keys not currently in billing cooldown (falls back to all if every key is benched —
        better to try a possibly-recovered key than to hard-fail)."""
        benched, now, out = self._benched(), _now(), []
        for k in self.keys:
            until = benched.get(_mask(k))
            if until:
                try:
                    if datetime.fromisoformat(until) > now:
                        continue
                except ValueError:
                    pass
            out.append(k)
        return out or list(self.keys)

    def current(self) -> str | None:
        avail = self.available_keys()
        if not avail:
            return None
        if self.strategy == "random":
            return random.choice(avail)
        if self.strategy == "least_used":
            used = self._usage()
            return min(avail, key=lambda k: used.get(_mask(k), 0))
        return avail[self._idx % len(avail)]   # fill_first / round_robin advance via rotate()

    def rotate(self) -> bool:
        if len(self.keys) <= 1:
            return False
        self._idx = (self._idx + 1) % len(self.keys)
        return True

    def report(self, kind: str, key: str | None = None) -> bool:
        """Apply pool policy for a classified failure: billing -> cooldown + rotate; rate_limit
        / auth -> rotate. Returns True when a different credential can be tried immediately."""
        key = key or self.current()
        if kind == "billing" and key:
            with _LOCK:
                st = _load_state()
                self._section(st).setdefault("cooldown", {})[_mask(key)] = (
                    _now() + timedelta(hours=self.cooldown_hours)).isoformat()
                _save_state(st)
            return self.rotate()
        elif kind in ("rate_limit", "auth"):
            return self.rotate()
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

    def status(self) -> dict:
        benched = self._benched()
        return {
            "provider": self.provider, "strategy": self.strategy, "keys": len(self.keys),
            "available": len(self.available_keys()), "cooldown_hours": self.cooldown_hours,
            "benched": {k: v for k, v in benched.items()},
        }


def pool_for(provider: str, env_vars: list[str] | None, config) -> CredentialPool | None:
    """Build (once) and return the shared pool for ``provider``, or None if no keys exist.
    Cached process-wide so every agent and subagent shares rotation state."""
    with _LOCK:
        if provider in _POOLS:
            return _POOLS[provider]
    cfg_pool = ((config.get("credential_pools", {}) or {}).get(provider, {}) if config else {}) or {}
    keys = list(cfg_pool.get("keys", []) or [])
    for var in (env_vars or []):
        v = os.environ.get(var)
        if v:
            keys += [k.strip() for k in v.split(",") if k.strip()]
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return None
    pool = CredentialPool(provider, keys, str(cfg_pool.get("strategy", "fill_first")),
                          float(cfg_pool.get("cooldown_hours", 24)))
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
    as billing cooldowns and least-used counters. Returns the number of provider
    sections removed so CLIs can report whether anything was cleared.
    """
    with _LOCK:
        state = _load_state()
        if provider:
            removed = 1 if provider in state else 0
            state.pop(provider, None)
        else:
            removed = len(state)
            state = {}
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
