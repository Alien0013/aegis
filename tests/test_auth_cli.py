from __future__ import annotations

import json


def test_auth_add_list_remove_pool_key_without_leaking_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    key1 = "sk-test-alpha-0001"
    key2 = "sk-test-beta-0002"

    assert main(["auth", "add", "openai", key1, key2, "--strategy", "random", "--cooldown-hours", "2"]) == 0
    out = capsys.readouterr().out
    assert "added 2 credential(s) to openai" in out
    assert key1 not in out and key2 not in out

    pool = (Config.load().get("credential_pools") or {}).get("openai") or {}
    assert pool["keys"] == [key1, key2]
    assert pool["strategy"] == "random"
    assert pool["cooldown_hours"] == 2.0

    assert main(["auth", "list", "openai"]) == 0
    out = capsys.readouterr().out
    assert "openai" in out
    assert "2 key(s)" in out
    assert "#1" in out and "#2" in out
    assert key1 not in out and key2 not in out

    assert main(["auth", "remove", "openai", "1"]) == 0
    out = capsys.readouterr().out
    assert "removed credential #1 from openai" in out
    assert key1 not in out and key2 not in out
    pool = (Config.load().get("credential_pools") or {}).get("openai") or {}
    assert pool["keys"] == [key2]


def test_auth_reset_pool_state_keeps_configured_keys(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis import credentials
    from aegis.cli.main import main
    from aegis.config import Config

    key = "sk-test-reset-0001"
    cfg = Config.load()
    cfg.data["credential_pools"] = {"openai": {"keys": [key]}}
    cfg.save()
    state_path = credentials._state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"openai": {"cooldown": {"sk-tes…0001": "2999-01-01T00:00:00+00:00"}}}), encoding="utf-8")

    assert main(["auth", "reset", "openai"]) == 0
    out = capsys.readouterr().out
    assert "reset credential pool state for openai" in out
    assert key not in out
    assert json.loads(state_path.read_text(encoding="utf-8")) == {}
    pool = (Config.load().get("credential_pools") or {}).get("openai") or {}
    assert pool["keys"] == [key]


def test_auth_add_unknown_provider_fails_without_storing_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    secret = "secret-value-that-should-not-print"
    assert main(["auth", "add", "not-a-provider", secret]) == 1
    err = capsys.readouterr().err
    assert "unknown provider" in err
    assert secret not in err
    assert (Config.load().get("credential_pools") or {}) == {}
