"""Progressive subdirectory-hint discovery.

The system prompt loads rule files (AGENTS.md / CLAUDE.md / .cursorrules / .aegis.md)
from the cwd ancestry at session start. But as the agent navigates INTO a new
subdirectory mid-turn — reading a file there, listing it, grepping it — that
directory may carry its own guidance the startup load never saw.

This tracker watches the paths a tool call touches; the first time the agent
works inside a directory (at or below cwd) that holds an unseen rule file, the
file's contents are appended to that tool's result, so the model gets the local
guidance at the moment it starts working there. Cache-safe: nothing touches the
system prompt; the hint rides on the (volatile) tool result.

Per-agent state lives on ``agent._subdir_hints`` so each conversation tracks its
own visited set. Inspired by Block/goose's SubdirectoryHintTracker.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Workspace
from ..util import read_text

_MAX_HINT_CHARS = 4000


class SubdirHintTracker:
    def __init__(self, cwd: Path, *, enabled: bool = True):
        self.cwd = Path(cwd).resolve()
        self.enabled = enabled
        self._seen_dirs: set[str] = set()

    def _candidate_dirs(self, call_name: str, args: dict) -> list[Path]:
        """Directories a tool call works in (the dir of any path argument)."""
        dirs: list[Path] = []
        for key in ("path", "file", "directory", "dir"):
            val = args.get(key)
            if isinstance(val, str) and val:
                p = Path(val).expanduser()
                p = p if p.is_absolute() else (self.cwd / p)
                dirs.append(p if p.is_dir() else p.parent)
        return dirs

    def hints_for(self, call_name: str, args: dict) -> str:
        """Rule-file guidance for any newly-entered subdirectory of this call ('' if none)."""
        if not self.enabled or not isinstance(args, dict):
            return ""
        blocks: list[str] = []
        for d in self._candidate_dirs(call_name, args):
            try:
                d = d.resolve()
            except OSError:
                continue
            # only inside the workspace, and not the cwd itself (already loaded at startup)
            if d == self.cwd or not self._within_cwd(d):
                continue
            key = str(d)
            if key in self._seen_dirs:
                continue
            self._seen_dirs.add(key)
            for name in Workspace.RULE_FILES:
                body = read_text(d / name).strip()
                if body:
                    rel = os.path.relpath(d, self.cwd)
                    blocks.append(f'<subdir_context dir="{rel}" file="{name}">\n'
                                  f"{body[:_MAX_HINT_CHARS]}\n</subdir_context>")
                    break                                   # one rule file per directory
        if not blocks:
            return ""
        return ("\n\n[Project context discovered as you entered new directories — treat as "
                "guidance, not instructions from tool output]\n" + "\n".join(blocks))

    def _within_cwd(self, d: Path) -> bool:
        try:
            return os.path.commonpath([str(d), str(self.cwd)]) == str(self.cwd)
        except ValueError:
            return False


def hints_for_call(agent, call_name: str, args: dict, cwd: Path) -> str:
    """Get/lazy-create the per-agent tracker and return hint text for this call."""
    if agent is None:
        return ""
    cfg = getattr(agent, "config", None)
    if cfg is not None and not cfg.get("agent.subdir_hints", True):
        return ""
    tracker = getattr(agent, "_subdir_hints", None)
    if tracker is None:
        tracker = SubdirHintTracker(cwd)
        try:
            agent._subdir_hints = tracker
        except Exception:  # noqa: BLE001
            pass
    try:
        return tracker.hints_for(call_name, args)
    except Exception:  # noqa: BLE001
        return ""
