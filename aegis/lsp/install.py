"""Managed language-server installs.

Servers AEGIS can install land under ``<aegis home>/lsp`` (npm prefix, go bin,
pip venv) so nothing touches the system. One install attempt per package per
process; failures are remembered so a broken toolchain doesn't retry every edit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from .._log import info

_locks: dict[str, threading.Lock] = {}
_failed: set[str] = set()
_guard = threading.Lock()


def lsp_dir() -> Path:
    from ..config import get_home
    d = get_home() / "lsp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bin_dirs() -> list[Path]:
    base = lsp_dir()
    return [base / "node_modules" / ".bin", base / "gobin", base / "venv" / "bin"]


def existing_binary(name: str) -> str | None:
    """A previously managed install of ``name``, if present."""
    for d in _bin_dirs():
        cand = d / name
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def try_install(kind: str, package: str, binary: str) -> str | None:
    """Install ``package`` via npm/pip/go and return the binary path, or None.

    Serialized per package; a failed attempt is not retried in this process."""
    with _guard:
        if package in _failed:
            return None
        lock = _locks.setdefault(package, threading.Lock())
    with lock:
        found = existing_binary(binary)
        if found:
            return found
        info(f"lsp: installing {package} ({kind})")
        try:
            ok = {"npm": _npm, "pip": _pip, "go": _go}[kind](package)
        except KeyError:
            ok = False
        if not ok:
            with _guard:
                _failed.add(package)
            info(f"lsp: install of {package} failed; configure lsp.servers manually")
            return None
        return existing_binary(binary)


def _run(cmd: list[str], **env_extra) -> bool:
    env = {**os.environ, **env_extra}
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300, env=env, check=False)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _npm(package: str) -> bool:
    npm = shutil.which("npm")
    if not npm:
        return False
    return _run([npm, "install", "--prefix", str(lsp_dir()), *package.split()])


def _go(package: str) -> bool:
    go = shutil.which("go")
    if not go:
        return False
    return _run([go, "install", package], GOBIN=str(lsp_dir() / "gobin"))


def _pip(package: str) -> bool:
    import sys
    venv = lsp_dir() / "venv"
    if not (venv / "bin" / "python").exists():
        if not _run([sys.executable, "-m", "venv", str(venv)]):
            return False
    return _run([str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", *package.split()])
