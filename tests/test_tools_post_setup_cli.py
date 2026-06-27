from __future__ import annotations


def test_tools_post_setup_reports_optional_setup(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["tools", "post-setup"]) == 0
    out = capsys.readouterr().out

    assert "Tool post-setup" in out
    assert "optional setup" in out.lower()


def test_top_level_postinstall_remains_available(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["postinstall"]) == 0
    out = capsys.readouterr().out
    assert "postinstall compatibility check" in out
