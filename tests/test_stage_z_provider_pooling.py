from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aegis import credentials
from aegis.config import Config


KEY_A = "sk-stage-z-provider-alpha-0001"
KEY_B = "sk-stage-z-provider-beta-0002"
KEY_C = "sk-stage-z-provider-gamma-0003"


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    credentials.reset()
    yield
    credentials.reset()


def _config_with_keys(provider: str, keys: list[str], **pool_overrides):
    config = Config.load()
    config.data["credential_pools"] = {
        provider: {"keys": keys, **pool_overrides},
    }
    return config


def test_env_source_suppression_sticks_across_pool_reload(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", f"{KEY_B},{KEY_C}")
    config = _config_with_keys("xai", [KEY_A])

    pool = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert pool is not None
    assert pool.keys == [KEY_A, KEY_B, KEY_C]

    assert pool.suppress_source("env:XAI_API_KEY", reason="removed by auth command") == 2
    assert pool.keys == [KEY_A]
    assert credentials.is_source_suppressed("xai", "env:XAI_API_KEY") is True

    credentials.reset()
    reloaded = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert reloaded is not None
    assert reloaded.keys == [KEY_A]
    assert [row["source"] for row in reloaded.entries()] == [
        "config:credential_pools.xai.keys",
    ]

    raw_state = credentials._state_path().read_text(encoding="utf-8")
    assert KEY_B not in raw_state
    assert KEY_C not in raw_state
    assert "env:XAI_API_KEY" in raw_state


def test_unsuppress_source_reseeds_env_keys(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", f"{KEY_B},{KEY_C}")
    config = _config_with_keys("xai", [KEY_A])
    pool = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert pool is not None

    pool.suppress_source("env:XAI_API_KEY")
    assert pool.unsuppress_source("env:XAI_API_KEY") is True

    credentials.reset()
    reloaded = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert reloaded is not None
    assert reloaded.keys == [KEY_A, KEY_B, KEY_C]
    assert "env:XAI_API_KEY" not in reloaded.status()["suppressed_sources"]


def test_all_suppressed_sources_do_not_fall_back_to_raw_env(monkeypatch):
    from aegis.providers.auth import AuthError, ApiKeyAuth

    monkeypatch.setenv("XAI_API_KEY", KEY_B)
    config = Config.load()
    pool = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert pool is not None

    pool.suppress_source("env:XAI_API_KEY")
    credentials.reset()

    auth = ApiKeyAuth(["XAI_API_KEY"], provider_name="xai", config=config)
    with pytest.raises(AuthError):
        auth.headers()


def test_reset_provider_state_preserves_source_suppression(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", f"{KEY_A},{KEY_B}")
    config = Config.load()
    pool = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert pool is not None
    assert pool.suppress_source("env:XAI_API_KEY", reason="removed by auth command") == 2
    pool.record_account_limit({"scope": "account", "reset_at": "2035-01-01T00:00:00+00:00"})

    assert credentials.reset_provider_state("xai") == 1
    assert credentials.is_source_suppressed("xai", "env:XAI_API_KEY") is True

    credentials.reset()
    reloaded = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert reloaded is not None
    assert reloaded.keys == []
    status = reloaded.status()
    assert "env:XAI_API_KEY" in status["suppressed_sources"]
    assert status["account_breaker"] is None


def test_soft_leases_prefer_unleased_available_keys(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", f"{KEY_A},{KEY_B},{KEY_C}")
    config = _config_with_keys("xai", [], max_concurrent_per_key=1)
    pool = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert pool is not None

    first = pool.acquire_lease()
    second = pool.acquire_lease()
    third = pool.acquire_lease()

    assert [first, second, third] == [KEY_A, KEY_B, KEY_C]
    assert pool.status()["leases"] == {
        credentials._mask(KEY_A): 1,
        credentials._mask(KEY_B): 1,
        credentials._mask(KEY_C): 1,
    }

    pool.release_lease(KEY_B)
    assert pool.acquire_lease() == KEY_B


def test_account_breaker_blocks_all_keys_until_reset(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", f"{KEY_A},{KEY_B}")
    config = Config.load()
    pool = credentials.pool_for("xai", ["XAI_API_KEY"], config)
    assert pool is not None

    reset_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    assert pool.report(
        "account_rate_limit",
        {"status_code": 429, "scope": "account", "reason": "hourly_quota", "reset_at": reset_at.isoformat()},
    ) is False

    assert pool.available_keys() == []
    assert pool.current() is None
    assert pool.status()["account_breaker"]["reason"] == "hourly_quota"
    assert pool.clear_account_limit() is True
    assert pool.available_keys() == [KEY_A, KEY_B]

    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    pool.record_account_limit({"scope": "account", "reset_at": past.isoformat()})
    assert pool.account_limit_remaining() is None
    assert pool.available_keys() == [KEY_A, KEY_B]
