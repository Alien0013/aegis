from __future__ import annotations


def test_hooks_doctor_reports_configured_hooks(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.config import Config
    from aegis.cli.main import main

    cfg = Config.load()
    cfg.set("hooks.user_prompt", "printf hook-ok")

    assert main(["hooks", "doctor"]) == 0
    out = capsys.readouterr().out

    assert "checking 1 configured hook" in out.lower()
    assert "user_prompt" in out
    assert "printf hook-ok" in out
    assert "healthy" in out.lower()


def test_hooks_revoke_removes_command_from_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.config import Config
    from aegis.cli.main import main

    cfg = Config.load()
    cfg.set("hooks.user_prompt", ["echo one", "echo two"])
    cfg.set("hooks.session_stop", "echo one")

    assert main(["hooks", "revoke", "echo one"]) == 0
    out = capsys.readouterr().out

    assert "removed 2 hook" in out.lower()
    reloaded = Config.load()
    assert reloaded.get("hooks.user_prompt") == ["echo two"]
    assert reloaded.get("hooks.session_stop") == []


def test_hooks_revoke_reports_missing_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.config import Config
    from aegis.cli.main import main

    Config.load().set("hooks.user_prompt", "echo one")

    assert main(["hooks", "rm", "echo missing"]) == 0
    out = capsys.readouterr().out

    assert "no configured hook command" in out.lower()
    assert Config.load().get("hooks.user_prompt") == "echo one"
