from __future__ import annotations


def test_memory_cli_provider_setup_status_and_off(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    monkeypatch.delenv("HONCHO_ENVIRONMENT", raising=False)

    from aegis.cli.main import main
    from aegis.config import Config

    assert main(["memory", "status"]) == 0
    out = capsys.readouterr().out
    assert "MEMORY.md" in out
    assert "External provider: builtin only" in out

    assert main(["memory", "setup", "honcho"]) == 0
    out = capsys.readouterr().out
    assert "Honcho" in out
    assert "memory.provider -> honcho" in out
    assert "HONCHO_API_KEY" in out
    assert Config.load().get("memory.provider") == "honcho"

    assert main(["memory", "status"]) == 0
    out = capsys.readouterr().out
    assert "External provider: honcho" in out
    assert "needs_setup" in out
    assert "HONCHO_API_KEY" in out

    assert main(["memory", "off"]) == 0
    out = capsys.readouterr().out
    assert "external memory provider disabled" in out
    assert Config.load().get("memory.provider") == ""


def test_memory_cli_reset_requires_confirmation_and_respects_target(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main
    from aegis.memory import MemoryStore

    store = MemoryStore()
    store.add("memory", "project uses pytest")
    store.add("user", "TJ prefers concise updates")

    assert main(["memory", "reset", "--target", "memory"]) == 1
    out = capsys.readouterr().out
    assert "requires --yes" in out
    assert store.entries("memory")
    assert store.entries("user")

    assert main(["memory", "reset", "--target", "memory", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "reset memory" in out
    assert store.entries("memory") == []
    assert store.entries("user") == ["TJ prefers concise updates"]


def test_memory_cli_rejects_unknown_provider(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main
    from aegis.config import Config

    assert main(["memory", "setup", "not-a-provider"]) == 1
    out = capsys.readouterr().out
    assert "unknown memory provider" in out
    assert Config.load().get("memory.provider") in (None, "")
