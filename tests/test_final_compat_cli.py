from __future__ import annotations

import json


def test_auth_spotify_reports_setup_guidance(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.cli.main import main

    assert main(["auth", "spotify"]) == 0
    out = capsys.readouterr().out.lower()
    assert "spotify" in out
    assert "oauth" in out or "setup" in out


def test_migrate_xai_dry_run_reports_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.cli.main import main

    assert main(["migrate", "xai", "--dry-run"]) == 0
    out = capsys.readouterr().out.lower()
    assert "xai" in out
    assert "no changes" in out or "dry-run" in out


def test_dashboard_register_dry_run_records_no_network(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.cli.main import main

    assert main(["dashboard", "register", "--dry-run"]) == 0
    out = capsys.readouterr().out.lower()
    assert "dashboard" in out
    assert "register" in out
    assert "dry-run" in out


def test_secrets_bitwarden_status_without_cli_is_safe(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: None if name in {"bw", "bws"} else None)

    from aegis.cli.main import main

    assert main(["secrets", "bitwarden", "status"]) == 0
    out = capsys.readouterr().out.lower()
    assert "bitwarden" in out
    assert "not found" in out or "not installed" in out


def test_slack_manifest_prints_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))

    from aegis.cli.main import main

    assert main(["slack", "manifest", "--name", "AEGIS Bot"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["display_information"]["name"] == "AEGIS Bot"
    assert "slash_commands" in payload["features"]
