from __future__ import annotations


def test_gateway_setup_persists_channels(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.cli.main import main

    assert main(["gateway", "setup", "--channels", "telegram,slack"]) == 0
    out = capsys.readouterr().out

    assert "configured gateway channels" in out.lower()
    assert Config.load().get("gateway.channels") == ["telegram", "slack"]


def test_gateway_enroll_and_list_pairing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.cli.main import main

    Config.load().set("gateway.channels", ["telegram"])

    assert main(["gateway", "enroll", "telegram", "12345"]) == 0
    out = capsys.readouterr().out
    assert "enrolled 12345 on telegram" in out.lower()

    assert main(["gateway", "list"]) == 0
    out = capsys.readouterr().out
    assert "telegram" in out
    assert "12345" in out


def test_gateway_migrate_legacy_is_safe_noop(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.cli.main import main

    assert main(["gateway", "migrate-legacy"]) == 0
    out = capsys.readouterr().out
    assert "legacy" in out.lower()
