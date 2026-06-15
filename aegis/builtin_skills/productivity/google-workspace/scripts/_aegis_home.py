"""Resolve AEGIS_HOME for standalone skill scripts.

Skill scripts may run outside the AEGIS process (e.g. system Python,
nix env, CI) where the package is not importable. This module centralizes
home-directory resolution and falls back to the same ``AEGIS_HOME`` logic.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``AEGIS_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from aegis.config import get_home as get_aegis_home

    def display_aegis_home() -> str:
        home = get_aegis_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
except (ModuleNotFoundError, ImportError):

    def get_aegis_home() -> Path:
        """Return the AEGIS home directory (default: ~/.aegis).

        val = os.environ.get("AEGIS_HOME", "").strip()
        return Path(val) if val else Path.home() / ".aegis"

    def display_aegis_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        home = get_aegis_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
