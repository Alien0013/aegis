"""Coding posture — orient the model to the workspace when AEGIS runs inside a code repo.

When the working directory is a code project, AEGIS injects a short *operating brief* plus a
one-time *git/workspace snapshot* (branch, dirty files, recent commits, layout) into the
system prompt. The model starts each session knowing the branch and pending changes instead
of re-deriving them tool call by tool call.

The snapshot is captured **once per session** so the system-prompt prefix stays byte-stable
for cache reuse — the working tree changes on every edit, so recomputing it each turn would
bust the prefix cache. Because it can go stale, the brief tells the model to re-check git
before trusting it. Gated by config ``agent.coding_context`` (auto/focus/on/off); returns ""
when the cwd is not a code workspace, so non-coding sessions never see the block unless
explicitly forced with ``on``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..config import Config, Workspace, context_file_max_chars

# Files that mark a code project even when there's no git repository.
_PROJECT_MARKERS = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "package.json", "tsconfig.json", "deno.json",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "mix.exs", "pubspec.yaml",
    "CMakeLists.txt", "Makefile", "Dockerfile", "AGENTS.md", "CLAUDE.md", ".cursorrules",
)

# Markers of a web frontend — when present, nudge the model to close the UI loop.
_WEB_MARKERS = ("package.json", "tsconfig.json", "deno.json")
_CONTEXT_FILES = ("AGENTS.md", "CLAUDE.md", ".cursorrules")
_PY_LOCKFILES = (("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"))
_JS_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"), ("bun.lockb", "bun"), ("bun.lock", "bun"),
    ("yarn.lock", "yarn"), ("package-lock.json", "npm"),
)
_VERIFY_TARGETS = ("test", "tests", "lint", "typecheck", "check", "build", "fmt", "format")
_NON_CODING_SKILL_CATEGORIES = (
    "apple", "communication", "cooking", "creative", "email", "finance",
    "gaming", "gifs", "health", "media", "music", "note-taking",
    "productivity", "shopping", "smart-home", "social-media", "travel",
    "yuanbao",
)

_WEB_HINT = (
    "- For web/UI changes, close the loop with `web_verify` (headless browser): it loads the\n"
    "  running page and reports console errors + whether expected text/elements render."
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


def _coding_mode(config: Config | dict[str, Any] | None) -> str:
    """Return normalized agent.coding_context mode (auto/focus/on/off)."""
    raw: Any = "auto"
    if isinstance(config, Config):
        raw = config.get("agent.coding_context", "auto")
    elif isinstance(config, dict):
        raw = ((config.get("agent") or {}) if isinstance(config.get("agent"), dict) else {}).get(
            "coding_context", "auto"
        )
    if isinstance(raw, bool):
        return "auto" if raw else "off"
    mode = str(raw or "auto").strip().lower()
    if mode in {"focus", "strict", "lean"}:
        return "focus"
    if mode in {"on", "true", "yes", "1", "always"}:
        return "on"
    if mode in {"off", "false", "no", "0", "never"}:
        return "off"
    return "auto"


def _is_code_workspace(cwd: Path) -> bool:
    return _is_git_repo(cwd) or bool(_detect_markers(cwd))


def _project_root(cwd: Path) -> Path | None:
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if root:
        return Path(root)
    current = cwd.resolve()
    for directory in [current, *current.parents]:
        if _detect_markers(directory):
            return directory
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _package_manager(root: Path) -> str:
    for filename, manager in _JS_LOCKFILES:
        if (root / filename).exists():
            return manager
    for filename, manager in _PY_LOCKFILES:
        if (root / filename).exists():
            return manager
    if (root / "package.json").exists():
        return "npm"
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        return "python"
    if (root / "Cargo.toml").exists():
        return "cargo"
    if (root / "go.mod").exists():
        return "go"
    if (root / "Makefile").exists():
        return "make"
    return ""


def _makefile_targets(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    targets: list[str] = []
    for line in text.splitlines():
        if line.startswith(("\t", " ", ".", "#")) or ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if name in _VERIFY_TARGETS and name not in targets:
            targets.append(name)
    return targets


def _verify_commands(root: Path, manager: str) -> list[str]:
    commands: list[str] = []
    package = _read_json(root / "package.json")
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    for target in _VERIFY_TARGETS:
        if target in scripts:
            runner = "npm"
            if manager in {"pnpm", "yarn", "bun"}:
                runner = manager
            commands.append(f"{runner} run {target}")
    makefile = root / "Makefile"
    if makefile.exists():
        for target in _makefile_targets(makefile):
            command = f"make {target}"
            if command not in commands:
                commands.append(command)
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        if (root / "tests").exists() and "pytest" not in commands:
            commands.append("pytest")
        if (root / "pyproject.toml").exists() and "python -m build" not in commands:
            commands.append("python -m build")
    if (root / "Cargo.toml").exists():
        commands.extend(command for command in ("cargo test", "cargo check") if command not in commands)
    if (root / "go.mod").exists():
        commands.extend(command for command in ("go test ./...", "go vet ./...") if command not in commands)
    return commands[:8]


def project_facts_for(cwd: Path | str | None = None) -> dict[str, Any] | None:
    """Structured project facts for dashboard/desktop/TUI surfaces.

    Returns ``None`` when ``cwd`` is not inside a recognizable code workspace.
    This mirrors the data baked into the coding-context prompt, but keeps UI
    code from re-sniffing manifests or guessing verification commands.
    """

    base = Path(cwd or Path.cwd()).expanduser()
    if not base.exists():
        return None
    if not base.is_dir():
        base = base.parent
    try:
        root = _project_root(base)
    except OSError:
        root = None
    if root is None:
        return None
    markers = _detect_markers(root)
    manager = _package_manager(root)
    branch = _git(root, "branch", "--show-current") or _git(root, "rev-parse", "--short", "HEAD")
    status = _git(root, "status", "--porcelain")
    context_files = [
        {"name": name, "path": str(root / name)}
        for name in _CONTEXT_FILES
        if (root / name).is_file()
    ]
    manifests = [
        {"name": name, "path": str(root / name)}
        for name in markers
        if (root / name).is_file()
    ]
    return {
        "root": str(root),
        "cwd": str(base.resolve()),
        "is_git_repo": _is_git_repo(root),
        "branch": branch,
        "dirty_count": len(status.splitlines()) if status else 0,
        "markers": markers,
        "manifests": manifests,
        "package_manager": manager,
        "verify_commands": _verify_commands(root, manager),
        "context_files": context_files,
    }


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
    workspace or the feature is disabled (``agent.coding_context`` auto/focus/on/off)."""
    mode = _coding_mode(config)
    if mode == "off":
        return ""
    cwd = Path(cwd).expanduser()
    if not cwd.is_dir():
        return ""
    is_repo = _is_git_repo(cwd)
    markers = _detect_markers(cwd)
    if not is_repo and not markers and mode != "on":
        return ""                       # not a code workspace — stay out of the prompt
    brief = _BRIEF
    if any((cwd / m).exists() for m in _WEB_MARKERS):
        brief = brief + "\n" + _WEB_HINT
    blocks = [brief]
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


def coding_compact_skill_categories(
    cwd: Path | str,
    config: Config | dict[str, Any] | None = None,
) -> frozenset[str]:
    """Skill categories to render as names-only under focus coding posture."""
    if _coding_mode(config) != "focus":
        return frozenset()
    cwd = Path(cwd).expanduser()
    if not cwd.is_dir() or not _is_code_workspace(cwd):
        return frozenset()
    return frozenset(_NON_CODING_SKILL_CATEGORIES)


def _rule_target_dir(target: Path) -> Path:
    """Best-effort directory for a path argument that may not exist yet."""
    try:
        if target.exists() and target.is_dir():
            return target.resolve()
    except OSError:
        pass
    return target.parent.resolve()


def _dirs_between(root: Path, leaf: Path) -> list[Path]:
    """Ancestor directories below root, broadest to nearest leaf."""
    try:
        leaf.relative_to(root)
    except ValueError:
        return []
    dirs: list[Path] = []
    cur = leaf
    for _ in range(40):
        if cur == root or cur == cur.parent:
            break
        dirs.append(cur)
        cur = cur.parent
    return list(reversed(dirs))


def subdirectory_rule_hint(
    cwd: Path | str,
    target: Path | str,
    config: Config | None = None,
    *,
    seen: set[str] | None = None,
) -> str:
    """Return cache-safe project-rule hints for a newly touched subdirectory.

    The session system prompt is built once, so package-local rule files discovered
    later via tool use must be delivered as tool-result context instead of forcing
    a prompt rebuild. Only rule files below the original cwd are injected here; cwd
    and broad parent rules are already handled by :class:`Workspace`.
    """
    if config is not None and not config.get("agent.subdir_hints", True):
        return ""
    try:
        root = Path(cwd).expanduser().resolve()
        leaf = _rule_target_dir(Path(target).expanduser())
    except OSError:
        return ""
    if not root.is_dir():
        return ""

    workspace = Workspace(root, context_file_max_chars=context_file_max_chars(config))
    blocks: list[str] = []
    injected: list[str] = []
    for directory in _dirs_between(root, leaf):
        for name in Workspace.RULE_FILES:
            path = directory / name
            if not path.is_file():
                continue
            key = str(path.resolve())
            if seen is not None and key in seen:
                break
            body = workspace._context_text(path, f"subdir:{name} ({directory})")
            if body:
                blocks.append(f"<!-- subdir:{name} ({directory}) -->\n{body}")
                injected.append(key)
            break

    if not blocks:
        return ""
    if seen is not None:
        seen.update(injected)
    return (
        "# Additional directory rules\n"
        "AEGIS just touched a subdirectory with local project instructions. "
        "Apply these rules for work inside that subtree.\n\n"
        + "\n\n".join(blocks)
    )
