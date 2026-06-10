"""A process-wide lock serializing read-modify-write on the SHARED stores.

Memory (MEMORY.md/USER.md), skills metadata (usage.json), and provenance.json are
read-modify-write files shared between the foreground agent and the background-review
thread. Atomic writes prevent a *corrupted* file but not a *lost update* when two
writers interleave. This RLock serializes the read-modify-write critical sections so a
concurrent write can't clobber another's change. (Reentrant so nested store calls on one
thread don't deadlock.)
"""

from __future__ import annotations

import contextlib
import os
import threading

STORE_LOCK = threading.RLock()


@contextlib.contextmanager
def file_lock(path):
    """Cross-PROCESS advisory lock on ``<path>.lock`` (fcntl flock).

    STORE_LOCK only serializes threads within one process — but the gateway, the
    CLI, and cron can all mutate the same store files from separate processes,
    where interleaved read-modify-write silently loses updates. No-op where
    fcntl is unavailable (non-Unix)."""
    try:
        import fcntl
    except ImportError:                       # pragma: no cover - non-Unix
        yield
        return
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
