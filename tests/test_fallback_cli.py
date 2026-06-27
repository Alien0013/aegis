from __future__ import annotations


def test_fallback_cli_add_list_remove_by_index(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    assert main(["fallback", "add", "ollama", "llama3.1"]) == 0
    out = capsys.readouterr().out
    assert "added fallback ollama/llama3.1" in out
    assert Config.load().get("fallback_providers") == [{"provider": "ollama", "model": "llama3.1"}]

    assert main(["fallback", "list"]) == 0
    out = capsys.readouterr().out
    assert "fallback providers:" in out
    assert "#1 ollama / llama3.1" in out

    assert main(["fallback", "remove", "1"]) == 0
    out = capsys.readouterr().out
    assert "removed fallback #1 ollama/llama3.1" in out
    assert Config.load().get("fallback_providers") == []


def test_fallback_cli_default_action_lists(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["fallback_providers"] = [{"provider": "ollama", "model": "llama3.1"}]
    cfg.save()

    assert main(["fallback"]) == 0
    out = capsys.readouterr().out
    assert "#1 ollama / llama3.1" in out


def test_fallback_cli_rejects_duplicate(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main

    assert main(["fallback", "add", "ollama", "llama3.1"]) == 0
    capsys.readouterr()
    assert main(["fallback", "add", "ollama", "llama3.1"]) == 1
    err = capsys.readouterr().err
    assert "already exists" in err


def test_fallback_cli_clear_removes_all_entries(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["fallback_providers"] = [
        {"provider": "ollama", "model": "llama3.1"},
        {"provider": "openai", "model": "gpt-4o-mini"},
    ]
    cfg.save()

    assert main(["fallback", "clear"]) == 0
    out = capsys.readouterr().out
    assert "cleared 2 fallback providers" in out
    assert Config.load().get("fallback_providers") == []
