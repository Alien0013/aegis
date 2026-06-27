from __future__ import annotations


def test_pairing_clear_pending_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main
    from aegis.gateway.pairing import PairingStore

    store = PairingStore()
    code = store.request_code("telegram", "user-1")
    assert code
    assert store.list()["pending"]["telegram"]

    assert main(["pairing", "clear-pending"]) == 0
    out = capsys.readouterr().out

    assert "Cleared 1 pending pairing request" in out
    assert store.list()["pending"] == {}


def test_pairing_clear_pending_cli_empty(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["pairing", "clear-pending"]) == 0
    out = capsys.readouterr().out
    assert "No pending requests to clear" in out
