#!/usr/bin/env python3
"""Compatibility launcher for the bundled AEGIS Google Workspace skill setup."""

from __future__ import annotations

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[4]
NATIVE = ROOT / "aegis/builtin_skills/productivity/google-workspace/scripts/setup.py"
runpy.run_path(str(NATIVE), run_name="__main__")
