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
import logging
import os
import threading

STORE_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


@contextlib.contextmanager
def file_lock(path):
    """Cross-PROCESS advisory lock on ``<path>.lock`` (fcntl flock).

    STORE_LOCK only serializes threads within one process — but the gateway, the
    CLI, and cron can all mutate the same store files from separate processes,
    where interleaved read-modify-write silently loses updates. No-op where
    platform locking is unavailable."""
    try:
        import fcntl
    except ImportError:                       # pragma: no cover - non-Unix
        fcntl = None
    try:
        import msvcrt
    except ImportError:                       # pragma: no cover - non-Windows
        msvcrt = None
    if fcntl is None and msvcrt is None:
        yield
        return
    try:
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        logger.warning("file lock unavailable for %s (%s); using in-process lock only", path, exc)
        yield
        return
    try:
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:                             # pragma: no cover - Windows
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        except OSError as exc:
            logger.warning("file lock unavailable for %s (%s); using in-process lock only", path, exc)
            yield
            return
        yield
    finally:
        try:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                else:                         # pragma: no cover - Windows
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        finally:
            os.close(fd)
