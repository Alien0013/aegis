"""AEGIS_STRICT developer mode: fail-soft swallow points re-raise when strict."""

from __future__ import annotations

import pytest

from aegis import _strict
from aegis._log import log_exc
from aegis._strict import is_strict, set_strict, soft


@pytest.fixture(autouse=True)
def _clear_strict(monkeypatch):
    monkeypatch.delenv("AEGIS_STRICT", raising=False)
    yield


def test_is_strict_reads_env_live(monkeypatch):
    assert is_strict() is False
    for truthy in ("1", "true", "YES", "On"):
        monkeypatch.setenv("AEGIS_STRICT", truthy)
        assert is_strict() is True
    monkeypatch.setenv("AEGIS_STRICT", "0")
    assert is_strict() is False


def test_set_strict_toggles_env():
    set_strict(True)
    assert is_strict() is True
    set_strict(False)
    assert is_strict() is False


def test_soft_swallows_in_production():
    ran = []
    with soft("optional thing"):
        ran.append("before")
        raise ValueError("boom")
    # block was swallowed; execution continues past the context manager
    assert ran == ["before"]


def test_soft_reraises_in_strict(monkeypatch):
    monkeypatch.setenv("AEGIS_STRICT", "1")
    with pytest.raises(ValueError, match="boom"):
        with soft("optional thing"):
            raise ValueError("boom")


def test_log_exc_swallows_in_production():
    # called inside an except block; must not propagate in production
    try:
        raise RuntimeError("eaten")
    except RuntimeError:
        log_exc("handled")  # returns normally, no raise


def test_log_exc_reraises_active_exception_in_strict(monkeypatch):
    monkeypatch.setenv("AEGIS_STRICT", "1")
    with pytest.raises(RuntimeError, match="eaten"):
        try:
            raise RuntimeError("eaten")
        except RuntimeError:
            log_exc("handled")  # re-raises the active exception under strict


def test_log_exc_no_active_exception_is_safe_in_strict(monkeypatch):
    monkeypatch.setenv("AEGIS_STRICT", "1")
    # No active exception — must not raise "No active exception to re-raise"
    log_exc("nothing in flight")


def test_provider_observer_failure_surfaces_in_strict(monkeypatch):
    """The agent loop's fail-soft provider observer re-raises a broken hook in strict."""
    from aegis.agent import loop

    monkeypatch.setenv("AEGIS_STRICT", "1")

    def boom(*a, **k):
        raise RuntimeError("plugin exploded")

    monkeypatch.setattr("aegis.plugins.fire_hook", boom)

    class _Agent:
        config = None

    with pytest.raises(RuntimeError, match="plugin exploded"):
        loop._fire_provider_observer(_Agent(), "pre_api_request", {"k": "v"})


def test_provider_observer_failure_swallowed_in_production(monkeypatch):
    from aegis.agent import loop

    def boom(*a, **k):
        raise RuntimeError("plugin exploded")

    monkeypatch.setattr("aegis.plugins.fire_hook", boom)

    class _Agent:
        config = None

    # production: swallowed, returns normally
    loop._fire_provider_observer(_Agent(), "pre_api_request", {"k": "v"})
    assert _strict.is_strict() is False
