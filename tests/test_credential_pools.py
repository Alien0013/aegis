"""Credential pools: config+env key merge, strategies, billing cooldown, rotation, sharing."""

from __future__ import annotations

import pytest

from aegis import credentials
from aegis.config import Config


@pytest.fixture(autouse=True)
def _reset_pools():
    credentials.reset()
    yield
    credentials.reset()


def test_pool_merges_config_and_env_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-111111111,env-key-222222222")
    c = Config.load()
    c.data["credential_pools"] = {"anthropic": {"keys": ["cfg-key-AAAAAAAAAAAA", "env-key-111111111"]}}
    pool = credentials.pool_for("anthropic", ["ANTHROPIC_API_KEY"], c)
    assert pool is not None
    # config keys first, env keys appended, de-duplicated (envkey1 appears once)
    assert pool.keys == ["cfg-key-AAAAAAAAAAAA", "env-key-111111111", "env-key-222222222"]


def test_pool_none_without_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert credentials.pool_for("anthropic", ["ANTHROPIC_API_KEY"], Config.load()) is None


def test_fill_first_and_rotate(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-key-AAAAAAAAAAAA,xai-key-BBBBBBBBBBBB,xai-key-CCCCCCCCCCCC")
    pool = credentials.pool_for("xai", ["XAI_API_KEY"], Config.load())
    assert pool.current() == "xai-key-AAAAAAAAAAAA"
    assert pool.rotate() is True
    assert pool.current() == "xai-key-BBBBBBBBBBBB"


def test_billing_cooldown_benches_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-key-AAAAAAAAAAAA,xai-key-BBBBBBBBBBBB")
    pool = credentials.pool_for("xai", ["XAI_API_KEY"], Config.load())
    first = pool.current()
    pool.report("billing")                       # bench `first` for cooldown_hours, then rotate
    assert first not in pool.available_keys()
    assert pool.current() == "xai-key-BBBBBBBBBBBB"


def test_least_used_strategy(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-key-AAAAAAAAAAAA,groq-key-BBBBBBBBBBBB")
    c = Config.load()
    c.data["credential_pools"] = {"groq": {"strategy": "least_used"}}
    pool = credentials.pool_for("groq", ["GROQ_API_KEY"], c)
    pool.record_use("groq-key-AAAAAAAAAAAA")
    # groq-key-BBBBBBBBBBBB has zero uses, so least_used must pick it
    assert pool.current() == "groq-key-BBBBBBBBBBBB"


def test_pool_is_shared_singleton(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-key-AAAAAAAAAAAA,xai-key-BBBBBBBBBBBB")
    a = credentials.pool_for("xai", ["XAI_API_KEY"], Config.load())
    b = credentials.pool_for("xai", ["XAI_API_KEY"], Config.load())
    assert a is b                                 # subagents reuse the same rotation state


def test_apikeyauth_uses_pool_and_reports(monkeypatch):
    from aegis.providers.auth import ApiKeyAuth
    monkeypatch.setenv("XAI_API_KEY", "xai-key-AAAAAAAAAAAA,xai-key-BBBBBBBBBBBB")
    auth = ApiKeyAuth(["XAI_API_KEY"], "bearer", provider_name="xai", config=Config.load())
    assert "xai-key-AAAAAAAAAAAA" in auth.headers()["Authorization"]
    auth.report("billing")                        # bench key1 -> next header uses key2
    assert "xai-key-BBBBBBBBBBBB" in auth.headers()["Authorization"]
