from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aegis import credentials


KEY_A = "sk-stage-v-alpha-0001"
KEY_B = "sk-stage-v-beta-0002"
KEY_C = "sk-stage-v-gamma-0003"


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    credentials.reset()
    yield
    credentials.reset()


def _entry(pool: credentials.CredentialPool, key: str) -> dict:
    return next(row for row in pool.entries() if row["id"] == credentials._mask(key))


def test_terminal_auth_marks_key_dead_and_redacts_state():
    pool = credentials.CredentialPool("openai", [KEY_A, KEY_B], cooldown_hours=0.01)

    assert pool.report(
        "auth",
        {"status_code": 401, "error": "invalid_token", "message": f"invalid_token for {KEY_A}"},
    ) is True

    assert pool.current() == KEY_B
    assert pool.available_keys() == [KEY_B]
    row = _entry(pool, KEY_A)
    assert row["status"] == "dead"
    assert row["available"] is False
    assert row["status_code"] == 401

    raw_state = credentials._state_path().read_text(encoding="utf-8")
    assert KEY_A not in raw_state
    assert KEY_B not in raw_state
    assert "invalid_token" in raw_state

    fresh_pool = credentials.CredentialPool("openai", [KEY_A, KEY_B], cooldown_hours=0)
    assert fresh_pool.available_keys() == [KEY_B]

    direct_reason_pool = credentials.CredentialPool("direct-auth", [KEY_A, KEY_B])
    assert direct_reason_pool.report("invalid_grant") is True
    assert _entry(direct_reason_pool, KEY_A)["status"] == "dead"


def test_rate_limit_and_billing_exhaust_with_reset_at_and_do_not_fallback_to_unavailable_keys():
    future = datetime(2035, 1, 1, tzinfo=timezone.utc)
    pool = credentials.CredentialPool("xai", [KEY_A, KEY_B, KEY_C], cooldown_hours=24)

    assert pool.current() == KEY_A
    assert pool.report(
        "rate_limit",
        {
            "status_code": 429,
            "reason": "rate_limit",
            "message": "retry later",
            "reset_at": int(future.timestamp() * 1000),
        },
    ) is True

    assert pool.available_keys() == [KEY_B, KEY_C]
    assert pool.current() == KEY_B
    row = _entry(pool, KEY_A)
    assert row["status"] == "exhausted"
    assert row["status_code"] == 429
    assert row["reason"] == "rate_limit"
    assert row["reset_at"].startswith("2035-01-01T00:00:00")

    assert pool.report(
        "billing",
        {"status_code": 402, "reason": "billing", "reset_at": future.isoformat()},
        key=KEY_B,
    ) is True
    assert pool.available_keys() == [KEY_C]
    assert pool.current() == KEY_C

    assert pool.report(
        "billing",
        {"status_code": 402, "reason": "quota", "reset_at": future.timestamp()},
        key=KEY_C,
    ) is False
    assert pool.available_keys() == []
    assert pool.current() is None
    assert pool.has_available() is False


def test_expired_exhausted_entries_reenter_but_dead_entries_do_not():
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    pool = credentials.CredentialPool("mistral", [KEY_A, KEY_B], cooldown_hours=24)

    assert pool.report("rate_limit", {"reset_at": past}) is True
    assert pool.available_keys() == [KEY_A, KEY_B]
    assert _entry(pool, KEY_A)["status"] == "ok"

    assert pool.report("auth", {"status_code": 401, "reason": "token_revoked"}, key=KEY_A) is True
    assert pool.available_keys() == [KEY_B]
    assert _entry(pool, KEY_A)["status"] == "dead"


def test_report_second_positional_key_remains_supported():
    pool = credentials.CredentialPool("compat", [KEY_A, KEY_B], cooldown_hours=24)

    assert pool.report("billing", KEY_A) is True

    assert pool.available_keys() == [KEY_B]
    assert _entry(pool, KEY_A)["status"] == "exhausted"


def test_strategies_use_available_entries_and_least_used_counters(monkeypatch):
    future = datetime(2035, 1, 1, tzinfo=timezone.utc).isoformat()

    round_robin = credentials.CredentialPool("rr", [KEY_A, KEY_B, KEY_C], strategy="round_robin")
    assert round_robin.report("rate_limit", {"reset_at": future}, key=KEY_A) is True
    assert round_robin.current() == KEY_B
    assert round_robin.rotate() is True
    assert round_robin.current() == KEY_C

    random_pool = credentials.CredentialPool("random", [KEY_A, KEY_B, KEY_C], strategy="random")
    assert random_pool.report("billing", {"reset_at": future}, key=KEY_C) is True
    seen = []
    monkeypatch.setattr(credentials.random, "choice", lambda keys: seen.append(list(keys)) or keys[-1])
    assert random_pool.current() == KEY_B
    assert seen == [[KEY_A, KEY_B]]

    least_used = credentials.CredentialPool("least", [KEY_A, KEY_B, KEY_C], strategy="least_used")
    least_used.record_use(KEY_A)
    least_used.record_use(KEY_A)
    least_used.record_use(KEY_B)
    assert least_used.current() == KEY_C

    assert least_used.report("rate_limit", {"reset_at": future}, key=KEY_C) is True
    assert least_used.current() == KEY_B
    least_used.record_use(KEY_B)
    assert _entry(least_used, KEY_B)["used"] == 2
