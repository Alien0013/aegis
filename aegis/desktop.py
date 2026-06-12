"""Bootstrap and launch the Electron desktop app."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from . import config as cfg

DESKTOP_FILES = ("package.json", "package-lock.json", "main.js", "launch.js")


def _print(message: str = "") -> None:
    print(message)


def _die(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _desktop_source() -> Any:
    """Return the source template directory for the Electron app."""
    repo_desktop = Path(__file__).resolve().parent.parent / "desktop"
    if all((repo_desktop / name).exists() for name in DESKTOP_FILES):
        return repo_desktop
    return resources.files("aegis").joinpath("desktop_app")


def _desktop_dir() -> Path:
    override = os.environ.get("AEGIS_DESKTOP_DIR")
    if override:
        return Path(override).expanduser()
    return cfg.get_home() / "desktop"


def _read_source_file(source: Any, name: str) -> bytes:
    try:
        return source.joinpath(name).read_bytes()
    except FileNotFoundError as exc:
        raise RuntimeError(f"desktop template is missing {name}") from exc


def _sync_desktop_app(source: Any, target: Path) -> bool:
    """Copy packaged desktop files into a writable runtime directory."""
    target.mkdir(parents=True, exist_ok=True)
    changed = False
    for name in DESKTOP_FILES:
        data = _read_source_file(source, name)
        dst = target / name
        if not dst.exists() or dst.read_bytes() != data:
            dst.write_bytes(data)
            changed = True
    return changed


def _needs_npm_install(target: Path, *, force: bool, template_changed: bool) -> bool:
    return force or template_changed or not (target / "node_modules" / "electron").exists()


def _aegis_bin() -> str:
    env_bin = os.environ.get("AEGIS_BIN")
    if env_bin:
        return env_bin
    if sys.argv and sys.argv[0]:
        argv0 = Path(sys.argv[0]).expanduser()
        if (argv0.is_absolute() or argv0.parent != Path(".")) and argv0.exists():
            return str(argv0.resolve())
        found = shutil.which(argv0.name)
        if found:
            return found
    return shutil.which("aegis") or "aegis"


def cmd_desktop(args, config) -> int:  # noqa: ARG001
    """Install/update the Electron source-run app, then launch it."""
    npm = shutil.which("npm")
    if not npm:
        return _die("`npm` was not found. Install Node.js/npm, then run `aegis desktop` again.")

    target = _desktop_dir()
    try:
        template_changed = _sync_desktop_app(_desktop_source(), target)
    except RuntimeError as exc:
        return _die(str(exc))

    if template_changed:
        _print(f"synced desktop app -> {target}")

    if _needs_npm_install(target, force=getattr(args, "reinstall", False),
                          template_changed=template_changed):
        _print("installing desktop dependencies with npm...")
        install = subprocess.run([npm, "install"], cwd=target)
        if install.returncode != 0:
            return install.returncode
    else:
        _print("desktop dependencies already installed.")

    if getattr(args, "install_only", False):
        _print(f"desktop ready at {target}")
        return 0

    env = os.environ.copy()
    env.setdefault("AEGIS_BIN", _aegis_bin())
    run_cmd = [npm, "run", "start:sandbox"] if getattr(args, "sandbox", False) else [npm, "start"]
    return subprocess.run(run_cmd, cwd=target, env=env).returncode
