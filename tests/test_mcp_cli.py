from __future__ import annotations


def test_mcp_configure_sets_tool_filter(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.cli.main import main

    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["servers"] = {"fs": {"command": "python", "args": ["server.py"]}}
    cfg.save()

    assert main(["mcp", "configure", "fs", "--include", "read,write"]) == 0
    out = capsys.readouterr().out
    assert "configured MCP server 'fs'" in out
    spec = Config.load().get("mcp.servers", {})["fs"]
    assert spec["tool_filter"] == {"include": ["read", "write"]}

    assert main(["mcp", "configure", "fs", "--all"]) == 0
    spec = Config.load().get("mcp.servers", {})["fs"]
    assert "tool_filter" not in spec


def test_mcp_login_reports_non_oauth_server(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.cli.main import main

    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["servers"] = {"fs": {"command": "python", "args": ["server.py"]}}
    cfg.save()

    assert main(["mcp", "login", "fs"]) == 0
    out = capsys.readouterr().out
    assert "does not declare OAuth" in out


def test_mcp_picker_lists_catalog_and_installed(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.config import Config
    from aegis.cli.main import main

    cfg = Config.load()
    cfg.data.setdefault("mcp", {})["servers"] = {"installed": {"command": "python", "args": ["server.py"]}}
    cfg.data.setdefault("mcp", {})["catalog"] = [
        {"name": "cataloged", "description": "Catalog entry", "command": "uvx", "args": ["pkg"]}
    ]
    cfg.save()

    assert main(["mcp", "picker"]) == 0
    out = capsys.readouterr().out
    assert "installed" in out
    assert "cataloged" in out
