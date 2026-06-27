from __future__ import annotations


def test_routing_cli_add_list_remove_by_index(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    assert main(["routing", "add", "deploy", "ollama", "llama3.1"]) == 0
    out = capsys.readouterr().out
    assert "added route /deploy/ -> ollama/llama3.1" in out
    assert Config.load().get("routing") == [{"match": "deploy", "provider": "ollama", "model": "llama3.1"}]

    assert main(["routing", "list"]) == 0
    out = capsys.readouterr().out
    assert "prompt routes:" in out
    assert "#1 /deploy/ -> ollama / llama3.1" in out

    assert main(["routing", "remove", "1"]) == 0
    out = capsys.readouterr().out
    assert "removed route #1 /deploy/" in out
    assert Config.load().get("routing") == []


def test_route_alias_defaults_to_list(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    cfg = Config.load()
    cfg.data["routing"] = [{"match": "deploy", "provider": "ollama", "model": "llama3.1"}]
    cfg.save()

    assert main(["route"]) == 0
    out = capsys.readouterr().out
    assert "#1 /deploy/ -> ollama / llama3.1" in out


def test_routing_cli_rejects_invalid_regex(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    assert main(["routing", "add", "[", "ollama", "llama3.1"]) == 1
    err = capsys.readouterr().err
    assert "invalid route regex" in err
    assert Config.load().get("routing") == []
