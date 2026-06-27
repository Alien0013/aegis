"""Compatibility helpers for legacy CLI module entry points."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Delegate legacy entry points to the native AEGIS CLI."""

    from aegis.cli.main import main as aegis_main

    try:
        return int(aegis_main(list(argv or [])) or 0)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
