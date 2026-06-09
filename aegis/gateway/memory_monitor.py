"""Periodic RSS log for a long-lived gateway — cheap insurance against memory leaks.

Emits a ``[MEMORY] RSS <n> MB`` line every ``interval`` seconds from a daemon thread. Uses
stdlib only (``/proc`` on Linux, ``resource`` elsewhere) — no psutil dependency.
"""

from __future__ import annotations

import os
import threading
import time


def rss_mb() -> float:
    """Current resident-set size in MB (0.0 if it can't be determined)."""
    try:                                              # Linux: current RSS from /proc
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:  # noqa: BLE001
        try:                                          # macOS/BSD: peak RSS via resource
            import resource
            import sys
            r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024
        except Exception:  # noqa: BLE001
            return 0.0


def start(interval: int = 300) -> None:
    """Start the background RSS logger (every ``interval`` seconds)."""
    def _loop():
        while True:
            time.sleep(interval)
            mb = rss_mb()
            if mb:
                print(f"[MEMORY] RSS {mb:.0f} MB")
    threading.Thread(target=_loop, daemon=True).start()
