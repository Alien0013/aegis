"""Early process bootstrap for AEGIS entry points."""

from __future__ import annotations

import os
import sys

_stdio_bootstrap_applied = False


def apply_windows_utf8_stdio() -> bool:
    """Make AEGIS entry points UTF-8-safe on native Windows.

    Returns True only when the bootstrap was applied in this process. POSIX is
    already UTF-8 in the normal case, so this intentionally leaves it alone.
    """

    global _stdio_bootstrap_applied
    if sys.platform != "win32" or _stdio_bootstrap_applied:
        return False

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    for stream_name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue

    _stdio_bootstrap_applied = True
    return True


def apply_startup_bootstrap() -> None:
    """Apply all safe, dependency-free entry-point bootstrap steps."""

    apply_windows_utf8_stdio()


__all__ = ["apply_startup_bootstrap", "apply_windows_utf8_stdio"]
