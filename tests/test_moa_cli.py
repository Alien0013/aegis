from __future__ import annotations


def test_moa_list_shows_configured_models(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.cli.main import main

    Config.load().set("moa.models", ["m1", "m2"])

    assert main(["moa", "list"]) == 0
    out = capsys.readouterr().out
    assert "m1" in out
    assert "m2" in out


def test_moa_configure_persists_models(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.cli.main import main

    assert main(["moa", "configure", "--models", "m1,m2,m3"]) == 0
    out = capsys.readouterr().out
    assert "configured moa models" in out.lower()
    assert Config.load().get("moa.models") == ["m1", "m2", "m3"]


def test_moa_delete_clears_models(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.cli.main import main

    Config.load().set("moa.models", ["m1", "m2"])

    assert main(["moa", "delete"]) == 0
    out = capsys.readouterr().out
    assert "cleared moa models" in out.lower()
    assert Config.load().get("moa.models") == []
