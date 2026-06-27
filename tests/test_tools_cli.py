from __future__ import annotations


def _core_tool_name() -> str:
    from aegis.tools.registry import default_registry

    return default_registry().available(["core"], only_usable=False)[0].name


def test_tools_cli_enables_and_disables_toolset(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    cfg = Config.load()
    cfg.set("tools.toolsets", ["core"])
    cfg.save()

    assert main(["tools", "enable", "web"]) == 0
    out = capsys.readouterr().out
    assert "enabled toolset web" in out
    assert "web" in (Config.load().get("tools.toolsets") or [])

    assert main(["tools", "disable", "web"]) == 0
    out = capsys.readouterr().out
    assert "disabled toolset web" in out
    assert "web" not in (Config.load().get("tools.toolsets") or [])


def test_tools_cli_enables_and_disables_individual_tool(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main
    from aegis.config import Config

    name = _core_tool_name()
    cfg = Config.load()
    cfg.set("tools.toolsets", ["core"])
    cfg.set("tools.disabled", [])
    cfg.save()

    assert main(["tools", "disable", name]) == 0
    out = capsys.readouterr().out
    assert f"disabled tool {name}" in out
    assert name in (Config.load().get("tools.disabled") or [])

    assert main(["tools", "enable", name]) == 0
    out = capsys.readouterr().out
    assert f"enabled tool {name}" in out
    assert name not in (Config.load().get("tools.disabled") or [])


def test_tools_cli_unknown_selector_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    from aegis.cli.main import main

    assert main(["tools", "enable", "not-a-real-tool-or-toolset"]) == 1
    err = capsys.readouterr().err
    assert "unknown tool or toolset" in err
