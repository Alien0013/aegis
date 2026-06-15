"""Coding posture — orient the model to the workspace when AEGIS runs inside a code repo.

When the working directory is a code project, AEGIS injects a short *operating brief* plus a
one-time *git/workspace snapshot* (branch, dirty files, recent commits, layout) into the
system prompt. The model starts each session knowing the branch and pending changes instead
of re-deriving them tool call by tool call.

The snapshot is captured **once per session** so the system-prompt prefix stays byte-stable
for cache reuse — the working tree changes on every edit, so recomputing it each turn would
bust the prefix cache. Because it can go stale, the brief tells the model to re-check git
before trusting it. Gated by config ``agent.coding_context`` (default on); returns "" when the
cwd is not a code workspace, so non-coding sessions never see the block.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import Config

# Files that mark a code project even when there's no git repository.
_PROJECT_MARKERS = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "package.json", "tsconfig.json", "deno.json",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "CMakeLists.txt", "Makefile",
)

_BRIEF = """\
# Coding workspace
You're operating inside a code repository — work like a careful engineer:
- On an unfamiliar repo, orient first: `repo_map` for the structural overview, or
  `code_search` to find code by meaning ("where are auth tokens validated"). Then read
  the few files that are relevant — don't read everything blindly.
- Read before you edit; match the surrounding style, naming, and structure.
- Make the smallest change that satisfies the request; no speculative refactors or features.
- After editing, verify — run the relevant build/tests/linter and report real output, not claims.
- Treat version control as ground truth: re-check `git status`/`git diff` before relying on the
  snapshot below, which was captured once at session start and may now be stale.
- Don't commit, push, or rewrite history unless the user asks."""


def _git(cwd: Path, *args: str, timeout: float = 2.0) -> str:
    """Run a read-only git command, returning trimmed stdout or "" on any failure."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _is_git_repo(cwd: Path) -> bool:
    return _git(cwd, "rev-parse", "--is-inside-work-tree") == "true"


def _detect_markers(cwd: Path) -> list[str]:
    return [m for m in _PROJECT_MARKERS if (cwd / m).exists()]


def _git_snapshot(cwd: Path, *, max_status: int = 12, max_commits: int = 5) -> str:
    root = _git(cwd, "rev-parse", "--show-toplevel") or str(cwd)
    branch = (_git(cwd, "branch", "--show-current")
              or _git(cwd, "rev-parse", "--short", "HEAD") or "?")
    ahead_behind = ""
    upstream = _git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        counts = _git(cwd, "rev-list", "--left-right", "--count", f"{upstream}...HEAD").split()
        if len(counts) == 2:
            behind, ahead = counts
            ahead_behind = f"  (ahead {ahead}, behind {behind} vs {upstream})"
    lines = [
        "## Repository snapshot (captured at session start — re-check with git before relying on it)",
        f"- branch: {branch}{ahead_behind}",
        f"- root: {root}",
    ]
    status = _git(cwd, "status", "--porcelain")
    if status:
        entries = status.splitlines()
        lines.append(f"- working tree: {len(entries)} changed file(s)")
        lines.extend(f"    {e}" for e in entries[:max_status])
        if len(entries) > max_status:
            lines.append(f"    … (+{len(entries) - max_status} more)")
    else:
        lines.append("- working tree: clean")
    commits = _git(cwd, "log", "--oneline", "-n", str(max_commits))
    if commits:
        lines.append("- recent commits:")
        lines.extend(f"    {c}" for c in commits.splitlines())
    return "\n".join(lines)


def _layout(cwd: Path, limit: int = 24) -> str:
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "")
                       for p in cwd.iterdir() if not p.name.startswith("."))
    except OSError:
        return ""
    if not names:
        return ""
    extra = f"  … (+{len(names) - limit} more)" if len(names) > limit else ""
    return "- top level: " + ", ".join(names[:limit]) + extra


def coding_workspace_block(cwd: Path | str, config: Config | None = None) -> str:
    """Operating brief + one-time workspace snapshot, or "" when the cwd is not a code
    workspace or the feature is disabled (``agent.coding_context``, default on)."""
    if config is not None and not config.get("agent.coding_context", True):
        return ""
    cwd = Path(cwd).expanduser()
    if not cwd.is_dir():
        return ""
    is_repo = _is_git_repo(cwd)
    markers = _detect_markers(cwd)
    if not is_repo and not markers:
        return ""                       # not a code workspace — stay out of the prompt
    blocks = [_BRIEF]
    if is_repo:
        blocks.append(_git_snapshot(cwd))
    else:
        body = ["## Project snapshot (no git repository detected)"]
        layout = _layout(cwd)
        if layout:
            body.append(layout)
        body.append("- project files: " + ", ".join(markers))
        blocks.append("\n".join(body))
    return "\n\n".join(blocks)
