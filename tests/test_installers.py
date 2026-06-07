"""Installer smoke checks that do not create venvs or touch the network."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_sh_syntax():
    res = subprocess.run(["bash", "-n", "install.sh"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_install_sh_help_mentions_onboarding_modes():
    res = subprocess.run(["bash", "install.sh", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0
    assert "--no-prompt" in res.stdout
    assert "--non-interactive" in res.stdout
    assert "--skip-onboard" in res.stdout


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
