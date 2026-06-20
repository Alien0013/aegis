"""Durable delivery-id dedupe helpers for gateway adapters."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .._locks import file_lock
from ..util import atomic_write, ensure_dir, read_text


class PersistentDeliveryIdStore:
    """Small JSON-backed, cross-process delivery-id cache.

    The in-memory :class:`aegis.webhook.DeliveryIdCache` handles hot retries inside
    one process. This store covers the restart window where providers can redeliver
    the same update after a gateway restart.
    """

    def __init__(self, path: Path, *, ttl_seconds: float = 3600, max_items: int = 10000) -> None:
        self.path = Path(path)
        self.ttl_seconds = max(1.0, float(ttl_seconds or 3600))
        self.max_items = max(1, int(max_items or 10000))
        self._lock = threading.RLock()
        self._accepted_count = 0
        self._duplicate_count = 0
        self._discarded_count = 0
        self._pruned_expired = 0
        self._pruned_capacity = 0

    def record(self, key: str, *, now: float | None = None) -> bool:
        key = str(key or "").strip()
        if not key:
            return True
        timestamp = time.time() if now is None else float(now)
        with self._locked_file():
            seen = self._load_seen()
            self._prune_seen(seen, timestamp)
            seen_at = seen.get(key)
            if seen_at is not None and timestamp - seen_at < self.ttl_seconds:
                self._duplicate_count += 1
                return False
            seen[key] = timestamp
            self._prune_seen(seen, timestamp)
            self._write_seen(seen)
            self._accepted_count += 1
            return True

    def discard(self, key: str) -> bool:
        key = str(key or "").strip()
        if not key:
            return False
        with self._locked_file():
            seen = self._load_seen()
            if key not in seen:
                return False
            seen.pop(key, None)
            self._write_seen(seen)
            self._discarded_count += 1
            return True

    def stats(self, *, now: float | None = None) -> dict[str, float | int | str]:
        timestamp = time.time() if now is None else float(now)
        if not self.path.exists():
            return {
                "entries": 0,
                "max_items": self.max_items,
                "ttl_seconds": self.ttl_seconds,
                "oldest_age_seconds": 0.0,
                "accepted_count": self._accepted_count,
                "duplicate_count": self._duplicate_count,
                "discarded_count": self._discarded_count,
                "pruned_expired": self._pruned_expired,
                "pruned_capacity": self._pruned_capacity,
                "path": str(self.path),
            }
        with self._locked_file(write_parent=False):
            seen = self._load_seen()
            self._prune_seen(seen, timestamp, count=False)
            oldest = timestamp - min(seen.values()) if seen else 0.0
            return {
                "entries": len(seen),
                "max_items": self.max_items,
                "ttl_seconds": self.ttl_seconds,
                "oldest_age_seconds": max(0.0, oldest),
                "accepted_count": self._accepted_count,
                "duplicate_count": self._duplicate_count,
                "discarded_count": self._discarded_count,
                "pruned_expired": self._pruned_expired,
                "pruned_capacity": self._pruned_capacity,
                "path": str(self.path),
            }

    def _locked_file(self, *, write_parent: bool = True):
        if write_parent:
            ensure_dir(self.path.parent)
        return _PersistentStoreLock(self._lock, self.path)

    def _load_seen(self) -> dict[str, float]:
        raw = read_text(self.path, "{}")
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        source = data.get("seen") if isinstance(data.get("seen"), dict) else data
        out: dict[str, float] = {}
        for key, value in source.items():
            text = str(key or "").strip()
            if not text:
                continue
            try:
                out[text] = float(value)
            except (TypeError, ValueError):
                continue
        return out

    def _write_seen(self, seen: dict[str, float]) -> None:
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "ttl_seconds": self.ttl_seconds,
            "max_items": self.max_items,
            "seen": dict(sorted(seen.items(), key=lambda item: item[1])),
        }
        atomic_write(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _prune_seen(self, seen: dict[str, float], now: float, *, count: bool = True) -> None:
        cutoff = now - self.ttl_seconds
        before = len(seen)
        for key, seen_at in list(seen.items()):
            if seen_at < cutoff:
                seen.pop(key, None)
        if count:
            self._pruned_expired += max(0, before - len(seen))
        if len(seen) <= self.max_items:
            return
        ordered = sorted(seen.items(), key=lambda item: item[1], reverse=True)
        keep = {key for key, _seen_at in ordered[: self.max_items]}
        removed = 0
        for key in list(seen):
            if key not in keep:
                seen.pop(key, None)
                removed += 1
        if count:
            self._pruned_capacity += removed


class _PersistentStoreLock:
    def __init__(self, lock: threading.RLock, path: Path) -> None:
        self._thread_lock = lock
        self._path = path
        self._file_lock = None

    def __enter__(self):
        self._thread_lock.acquire()
        self._file_lock = file_lock(self._path)
        self._file_lock.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        try:
            if self._file_lock is not None:
                self._file_lock.__exit__(exc_type, exc, tb)
        finally:
            self._thread_lock.release()
