
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_live_desktop_linux_runner_preflight():
    if os.getenv("AEGIS_LIVE_DESKTOP_LINUX") != "1":
        pytest.skip("set AEGIS_LIVE_DESKTOP_LINUX=1 on a Linux desktop runner")
    root = Path(__file__).resolve().parents[2]
    assert (root / "desktop" / "package.json").is_file()
    assert (root / "desktop" / "electron" / "main.js").is_file()
