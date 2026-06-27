from __future__ import annotations


def test_proxy_cli_providers_and_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main
    from aegis.config import Config

    cfg = Config.load()
    cfg.set("server.host", "127.0.0.9")
    cfg.set("server.port", 9999)
    cfg.save()

    assert main(["proxy", "providers"]) == 0
    out = capsys.readouterr().out
    assert "openai" in out or "ollama" in out

    assert main(["proxy", "status"]) == 0
    out = capsys.readouterr().out
    assert "OpenAI-compatible proxy" in out
    assert "127.0.0.9:9999" in out


def test_proxy_cli_start_delegates_to_serve(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    import aegis.cli.main as cli_main

    seen = {}

    def fake_serve(args, config):
        seen["action"] = getattr(args, "action", None)
        seen["host"] = args.host
        seen["port"] = args.port
        seen["config"] = config
        return 0

    monkeypatch.setattr(cli_main, "cmd_serve", fake_serve)

    assert cli_main.main(["proxy", "start", "--host", "127.0.0.2", "--port", "9998"]) == 0
    assert seen["action"] == "start"
    assert seen["host"] == "127.0.0.2"
    assert seen["port"] == 9998
    assert seen["config"] is not None
