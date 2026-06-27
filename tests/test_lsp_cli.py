from __future__ import annotations


def test_lsp_cli_status_lists_native_registry(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["lsp", "status"]) == 0

    out = capsys.readouterr().out
    assert "AEGIS LSP" in out
    assert "Managed dir:" in out
    assert "pyright" in out
    assert ".py" in out
    assert "auto_install:" in out


def test_lsp_cli_install_dry_run_uses_native_managed_installer(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["lsp", "install", "pyright", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "dry run" in out.lower()
    assert "pyright" in out
    assert "npm" in out
    assert "pyright-langserver" in out
