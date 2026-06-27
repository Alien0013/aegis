#!/usr/bin/env python3
"""Delegate update planning/execution to the native AEGIS CLI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis.cli.main import main as aegis_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    return aegis_main(["update", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
