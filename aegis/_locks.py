"""A process-wide lock serializing read-modify-write on the SHARED stores.

Memory (MEMORY.md/USER.md), skills metadata (usage.json), and provenance.json are
read-modify-write files shared between the foreground agent and the background-review
thread. Atomic writes prevent a *corrupted* file but not a *lost update* when two
writers interleave. This RLock serializes the read-modify-write critical sections so a
concurrent write can't clobber another's change. (Reentrant so nested store calls on one
thread don't deadlock.)
"""

from __future__ import annotations

import threading

STORE_LOCK = threading.RLock()
