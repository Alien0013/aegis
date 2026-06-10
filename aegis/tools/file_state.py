"""Cross-call file freshness tracking.

Process-wide registry of the mtime each file had when the agent last saw it
(read or wrote). Before a write/edit, ``stale_warning`` reports if the file
changed on disk since — another subagent, the user, or a linter touched it —
so the agent re-reads instead of silently clobbering with a stale copy.
Warnings, not blocks: the agent stays in control."""

from __future__ import annotations

import os
import threading

_seen: dict[str, float] = {}      # resolved path -> mtime when last read/written
_lock = threading.Lock()


def _key(path) -> str:
    return os.path.realpath(str(path))


def note(path) -> None:
    """Record the file's current mtime (call after read_file / write_file / edit_file)."""
    try:
        m = os.path.getmtime(_key(path))
    except OSError:
        return
    with _lock:
        _seen[_key(path)] = m


def stale_warning(path) -> str:
    """'' when fresh/untracked; a warning when the file changed since last seen."""
    k = _key(path)
    with _lock:
        last = _seen.get(k)
    if last is None:
        return ""
    try:
        now = os.path.getmtime(k)
    except OSError:
        return ""
    if now > last + 1e-6:
        return ("\n\n<system-reminder>WARNING: this file changed on disk after you last "
                "read it (another agent, the user, or a tool modified it). Your copy may "
                "be stale — re-read it before further edits, and do not revert changes "
                "you didn't make.</system-reminder>")
    return ""


def reset() -> None:
    with _lock:
        _seen.clear()
