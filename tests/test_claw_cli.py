from __future__ import annotations


def test_claw_migrate_dry_run_reports_openclaw_source(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    source = tmp_path / "openclaw"
    source.mkdir()
    (source / "config.yaml").write_text("model: demo\n", encoding="utf-8")

    from aegis.cli.main import main

    assert main(["claw", "migrate", "--source", str(source), "--dry-run"]) == 0
    out = capsys.readouterr().out.lower()
    assert "openclaw migration preview" in out
    assert str(source) in out
    assert "config.yaml" in out


def test_claw_cleanup_dry_run_reports_archive_target(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    source = tmp_path / "openclaw"
    source.mkdir()

    from aegis.cli.main import main

    assert main(["claw", "cleanup", "--source", str(source), "--dry-run"]) == 0
    out = capsys.readouterr().out.lower()
    assert "openclaw cleanup preview" in out
    assert "would archive" in out
    assert source.exists()
