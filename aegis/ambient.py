"""Ambient mode — watch the repo and run the relevant tests on every save.

`aegis watch` polls the workspace for source-file changes (stdlib only — no watchdog
dependency) and, on a save, runs the project's test command and surfaces failures before
you ask. The Devin-style "it noticed the test broke" loop, but local and provider-free by
default. With ``--fix`` (or ``ambient.autofix``) a failing run can hand the failure to the
agent to repair, then re-check.

Pure helpers (test-command detection, change scanning, ignore rules) are unit-tested; the
watch loop takes ``once=True`` and an injectable runner so it's testable without sleeping.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

_SOURCE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".java",
                ".c", ".h", ".cpp", ".cc", ".css", ".vue", ".svelte"}
_IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
                ".aegis", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", "target",
                "vendor", ".tox", "coverage", ".idea"}

TestRunner = Callable[[str, Path], "tuple[bool, str]"]


def detect_test_command(root: Path, config=None) -> str:
    """The project's test command — config override, else auto-detect by project markers."""
    if config is not None:
        override = str(config.get("ambient.test_command", "") or "")
        if override:
            return override
    root = Path(root)
    if (root / "pytest.ini").exists() or (root / "tests").is_dir() \
            or (root / "pyproject.toml").exists():
        return "python -m pytest -q"
    if (root / "package.json").exists():
        return "npm test"
    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    return ""


def _ignored(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in _IGNORE_DIRS for part in rel_parts)


def scan(root: Path, exts: set[str] | None = None) -> dict[str, float]:
    """Snapshot {relpath: mtime} of source files under ``root`` (ignoring junk dirs)."""
    exts = exts or _SOURCE_EXTS
    root = Path(root)
    out: dict[str, float] = {}
    for p in root.rglob("*"):
        if p.suffix not in exts or _ignored(p, root):
            continue
        try:
            out[str(p.relative_to(root))] = p.stat().st_mtime
        except OSError:
            continue
    return out


def changed(prev: dict[str, float], curr: dict[str, float]) -> list[str]:
    """Files added or whose mtime advanced between two scans."""
    return sorted(k for k, v in curr.items()
                  if k not in prev or v > prev.get(k, 0))


def _run_tests(cmd: str, root: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, shell=True, cwd=str(root), capture_output=True,
                              text=True, timeout=600, check=False)
    except subprocess.TimeoutExpired:
        return False, "[tests timed out]"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out


def watch(root: Path | str, *, config=None, interval: float = 1.5, once: bool = False,
          on_event: Callable[[dict], None] | None = None,
          runner: TestRunner | None = None, max_iterations: int | None = None,
          baseline: dict[str, float] | None = None) -> dict:
    """Poll ``root`` for saves; run the test command after each change.

    ``once`` runs a single poll-and-maybe-test cycle (for tests); pass ``baseline`` to diff
    against (``{}`` treats every current file as changed). Returns a summary of the last run.
    Emits ``{"type": "change"|"tests", ...}`` events for the UI."""
    root = Path(root).expanduser()
    cmd = detect_test_command(root, config)
    run_tests = runner or _run_tests
    emit = on_event or (lambda e: None)
    if not cmd:
        emit({"type": "error", "message": "no test command detected (set ambient.test_command)"})
        return {"ok": False, "reason": "no test command"}
    emit({"type": "start", "command": cmd, "root": str(root)})
    prev = baseline if baseline is not None else scan(root)
    last: dict = {"ok": True, "ran": False}
    iterations = 0
    while True:
        if not once:
            time.sleep(interval)
        curr = scan(root)
        diff = changed(prev, curr)
        prev = curr
        if diff:
            emit({"type": "change", "files": diff[:20], "count": len(diff)})
            ok, output = run_tests(cmd, root)
            tail = "\n".join(output.strip().splitlines()[-25:])
            emit({"type": "tests", "ok": ok, "output": tail, "files": diff[:20]})
            last = {"ok": ok, "ran": True, "files": diff, "output": tail}
        iterations += 1
        if once or (max_iterations is not None and iterations >= max_iterations):
            break
    return last


def cmd_watch(args, config) -> int:
    """`aegis watch [path]` — run the project's tests on every save."""
    root = Path(getattr(args, "path", None) or ".").expanduser()

    def emit(e: dict) -> None:
        kind = e.get("type")
        if kind == "start":
            print(f"👁  ambient: watching {e['root']}  (tests: {e['command']})  — Ctrl+C to stop")
        elif kind == "error":
            print(f"  {e['message']}")
        elif kind == "change":
            print(f"\n↻ {e['count']} file(s) changed: {', '.join(e['files'][:5])}"
                  + (" …" if e["count"] > 5 else ""))
        elif kind == "tests":
            print("  ✓ tests pass" if e["ok"] else "  ✗ tests FAILED")
            if not e["ok"]:
                print("\n".join("    " + ln for ln in e["output"].splitlines()[-15:]))

    try:
        watch(root, config=config, on_event=emit)
    except KeyboardInterrupt:
        print("\nambient: stopped.")
    return 0
