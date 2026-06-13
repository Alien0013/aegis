"""Session-store compression lock semantics."""

from __future__ import annotations

import time


def test_compression_lock_acquire_release_and_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.session import SessionStore

    store = SessionStore()
    assert store.try_acquire_compression_lock("sess1", "holder1") is True
    assert store.get_compression_lock_holder("sess1") == "holder1"

    assert store.try_acquire_compression_lock("sess1", "holder1") is True
    assert store.try_acquire_compression_lock("sess1", "holder2") is False

    store.release_compression_lock("sess1", "holder2")
    assert store.get_compression_lock_holder("sess1") == "holder1"

    store.release_compression_lock("sess1", "holder1")
    assert store.get_compression_lock_holder("sess1") is None
    assert store.try_acquire_compression_lock("sess1", "holder2") is True


def test_compression_lock_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.session import SessionStore

    store = SessionStore()
    assert store.try_acquire_compression_lock("sess1", "old", ttl_seconds=0.01) is True
    time.sleep(0.02)

    assert store.get_compression_lock_holder("sess1") is None
    assert store.try_acquire_compression_lock("sess1", "new") is True
    assert store.get_compression_lock_holder("sess1") == "new"
