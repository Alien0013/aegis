from __future__ import annotations


def test_computer_use_status_doctor_install_permissions(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["computer-use", "status"]) == 0
    out = capsys.readouterr().out
    assert "Computer Use" in out
    assert "computer tool" in out

    assert main(["computer-use", "doctor"]) in {0, 1}
    out = capsys.readouterr().out
    assert "Computer Use diagnostics" in out

    assert main(["computer-use", "install"]) == 0
    out = capsys.readouterr().out
    assert "pip install" in out
    assert "computer" in out

    assert main(["computer-use", "permissions"]) == 0
    out = capsys.readouterr().out
    assert "permissions" in out.lower()


def test_computer_use_legacy_bare_command_defaults_to_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["computer-use"]) == 0
    out = capsys.readouterr().out
    assert "Computer Use" in out
