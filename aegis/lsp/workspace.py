"""Workspace gating + project-root resolution.

LSP only runs for files inside a git worktree — that's the "is this a project?"
gate, and it keeps chat-surface sessions running in $HOME from spawning
language-server daemons. Per-server roots are found by walking up for the
language's marker file (pyproject.toml, go.mod, Cargo.toml, …).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_cache: dict[str, str | None] = {}

# A .git directly at / or the system temp dir is never a real project — scratch
# files under /tmp must not all gate in because something once ran `git init /tmp`.
_IGNORED_ROOTS = {"/", os.path.realpath(tempfile.gettempdir())}


def _norm(path: str) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


def find_git_worktree(start: str) -> str | None:
    """Directory containing ``.git`` (file or dir) walking up from ``start``, else None."""
    p = Path(_norm(start))
    try:
        if p.is_file():
            p = p.parent
    except OSError:
        return None
    key = str(p)
    if key in _cache:
        return _cache[key]
    cur = p
    for _ in range(64):                      # bounded walk — symlink-cycle safe
        try:
            if str(cur) not in _IGNORED_ROOTS and (cur / ".git").exists():
                _cache[key] = str(cur)
                return str(cur)
        except OSError:
            break
        if cur.parent == cur:
            break
        cur = cur.parent
    _cache[key] = None
    return None


def nearest_root(start: str, markers: list[str], *, ceiling: str | None = None) -> str | None:
    """Directory of the first marker found walking up from ``start`` (stops at ceiling)."""
    p = Path(_norm(start))
    try:
        if p.is_file():
            p = p.parent
    except OSError:
        return None
    top = Path(_norm(ceiling)) if ceiling else None
    cur = p
    for _ in range(64):
        for m in markers:
            try:
                if (cur / m).exists():
                    return str(cur)
            except OSError:
                continue
        if (top is not None and cur == top) or cur.parent == cur:
            return None
        cur = cur.parent
    return None


def resolve_workspace(file_path: str, cwd: str | None = None) -> str | None:
    """The git worktree this file belongs to, or None (LSP gated off).

    The cwd's worktree wins when the file is inside it; otherwise the file's
    own worktree is used."""
    cwd_root = find_git_worktree(cwd or os.getcwd())
    if cwd_root:
        f = _norm(file_path)
        if f == cwd_root or f.startswith(cwd_root + os.sep):
            return cwd_root
    return find_git_worktree(file_path)


def clear_cache() -> None:
    _cache.clear()
