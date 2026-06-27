from __future__ import annotations


def test_debug_delete_removes_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis import config as cfg
    from aegis.cli.main import main

    report = cfg.sub("debug-report.zip")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"debug")

    assert main(["debug", "delete"]) == 0
    out = capsys.readouterr().out

    assert "deleted debug report" in out
    assert not report.exists()


def test_debug_delete_reports_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))

    from aegis.cli.main import main

    assert main(["debug", "delete"]) == 0
    out = capsys.readouterr().out
    assert "no debug report" in out.lower()
