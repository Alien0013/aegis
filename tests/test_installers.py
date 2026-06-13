"""Installer smoke checks that do not create venvs or touch the network."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_sh_syntax():
    res = subprocess.run(["bash", "-n", "install.sh"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_uninstall_sh_syntax():
    res = subprocess.run(["bash", "-n", "uninstall.sh"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_install_sh_help_mentions_onboarding_modes():
    res = subprocess.run(["bash", "install.sh", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0
    assert "--no-prompt" in res.stdout
    assert "--non-interactive" in res.stdout
    assert "--skip-onboard" in res.stdout


def test_install_sh_does_not_advertise_removed_tui():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "aegis tui" not in text


def test_install_ps1_parse_when_pwsh_available():
    pwsh = shutil.which("pwsh")
    if not pwsh:
        return
    res = subprocess.run(
        [pwsh, "-NoProfile", "-Command", "$null = [scriptblock]::Create((Get-Content install.ps1 -Raw))"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr


def test_uninstall_purge_removes_installed_artifacts(monkeypatch, tmp_path):
    from aegis import config as cfg
    from aegis.cli.main import main

    home = tmp_path / ".aegis"
    venv = home / "venv"
    venv.mkdir(parents=True)
    (home / "config.yaml").write_text("model:\n  provider: openai\n", encoding="utf-8")

    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    launcher = bin_dir / "aegis"
    launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    unit_dir = tmp_path / ".config" / "systemd" / "user"
    wants_dir = unit_dir / "default.target.wants"
    wants_dir.mkdir(parents=True)
    dashboard_unit = unit_dir / "aegis-dashboard.service"
    gateway_link = wants_dir / "aegis-gateway.service"
    cron_unit = unit_dir / "aegis-cron.service"
    dashboard_unit.write_text("[Service]\n", encoding="utf-8")
    gateway_link.write_text("[Service]\n", encoding="utf-8")
    cron_unit.write_text("[Service]\n", encoding="utf-8")

    monkeypatch.setenv("AEGIS_HOME", str(home))
    monkeypatch.setenv("AEGIS_BIN_DIR", str(bin_dir))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    cfg.set_profile(None)

    assert main(["uninstall", "--purge"]) == 0
    assert not home.exists()
    assert not launcher.exists()
    assert not dashboard_unit.exists()
    assert not gateway_link.exists()
    assert not cron_unit.exists()
