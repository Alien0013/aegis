"""Bootstrap and launch the Electron desktop app."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from . import config as cfg

DESKTOP_FILES = (
    "package.json", "package-lock.json", "launch.js",
    "electron/main.js", "electron/main-behavior.test.cjs",
    "electron/backend-env.cjs", "electron/backend-env.test.cjs",
    "electron/windows-user-env.cjs", "electron/windows-user-env.test.cjs",
    "electron/desktop-settings.cjs", "electron/desktop-settings.test.cjs",
    "electron/desktop-status.cjs", "electron/desktop-status.test.cjs",
    "electron/updater-status.cjs",
    "electron/preload.js", "electron/preload-app.js", "electron/boot.html",
    "scripts/before-pack.cjs", "scripts/before-pack.test.cjs",
    "scripts/before-build.cjs", "scripts/write-build-stamp.cjs",
    "scripts/write-build-stamp.test.cjs",
    "build/icon.png", "build/icon.ico",
)
DESKTOP_MANIFEST = ".aegis-desktop-files.json"


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
        node = source
        for part in name.split("/"):           # traverse subdirs (electron/, build/)
            node = node.joinpath(part)
        return node.read_bytes()
    except FileNotFoundError as exc:
        raise RuntimeError(f"desktop template is missing {name}") from exc


def _read_desktop_manifest(target: Path) -> set[str]:
    path = target / DESKTOP_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    if isinstance(data, dict):
        data = data.get("files")
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data if isinstance(item, str) and item}


def _write_desktop_manifest(target: Path) -> None:
    payload = {
        "schema_version": 1,
        "files": sorted(DESKTOP_FILES),
    }
    (target / DESKTOP_MANIFEST).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _safe_relative_file(name: str) -> bool:
    rel = Path(name)
    return bool(name and not rel.is_absolute() and ".." not in rel.parts and rel.parts)


def _cleanup_removed_desktop_files(target: Path, previous: set[str]) -> bool:
    changed = False
    current = set(DESKTOP_FILES)
    for name in sorted(previous - current):
        if not _safe_relative_file(name):
            continue
        path = target / name
        try:
            resolved = path.resolve()
            resolved.relative_to(target.resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                changed = True
                parent = path.parent
                while parent != target and parent.exists():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        except OSError:
            continue
    return changed


def _sync_desktop_app(source: Any, target: Path) -> bool:
    """Copy packaged desktop files into a writable runtime directory."""
    target.mkdir(parents=True, exist_ok=True)
    changed = False
    previous = _read_desktop_manifest(target)
    if previous and _cleanup_removed_desktop_files(target, previous):
        changed = True
    for name in DESKTOP_FILES:
        data = _read_source_file(source, name)
        dst = target / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.read_bytes() != data:
            dst.write_bytes(data)
            changed = True
    _write_desktop_manifest(target)
    return changed


def _needs_npm_install(target: Path, *, force: bool, template_changed: bool) -> bool:
    return force or template_changed or not (target / "node_modules" / "electron").exists()


def _npm_install_command(npm: str, target: Path, *, package_lock: bool | None = None) -> list[str]:
    has_lock = (target / "package-lock.json").exists() if package_lock is None else bool(package_lock)
    if has_lock:
        return [npm, "ci"]
    return [npm, "install"]


def _unpacked_executable_candidates(target: Path, *, platform: str | None = None) -> list[Path]:
    platform = platform or sys.platform
    release = target / "release"
    if platform.startswith("linux"):
        return [release / "linux-unpacked" / "AEGIS"]
    if platform.startswith("win"):
        return [release / "win-unpacked" / "AEGIS.exe"]
    if platform == "darwin":
        return [
            release / "mac" / "AEGIS.app" / "Contents" / "MacOS" / "AEGIS",
            release / "mac-arm64" / "AEGIS.app" / "Contents" / "MacOS" / "AEGIS",
        ]
    return [release / "linux-unpacked" / "AEGIS"]


def _unpacked_executable(target: Path, *, platform: str | None = None) -> Path:
    candidates = _unpacked_executable_candidates(target, platform=platform)
    return next((path for path in candidates if path.exists()), candidates[0])


def _packaged_launch_command(target: Path, *, sandbox: bool = False,
                             platform: str | None = None) -> list[str]:
    platform = platform or sys.platform
    cmd = [str(_unpacked_executable(target, platform=platform))]
    if platform.startswith("linux") and not sandbox:
        cmd.append("--no-sandbox")
    return cmd


def _desktop_status(source: Any, target: Path, *, npm: str | None = None) -> dict[str, Any]:
    missing: list[str] = []
    for name in DESKTOP_FILES:
        try:
            _read_source_file(source, name)
        except RuntimeError:
            missing.append(name)
    source_has_lock = "package-lock.json" not in missing
    install_cmd = _npm_install_command(npm or "npm", target, package_lock=source_has_lock)
    return {
        "ok": bool(npm) and not missing,
        "target": str(target),
        "source": str(source),
        "npm": npm or "",
        "package_lock": (target / "package-lock.json").exists(),
        "dependencies_installed": (target / "node_modules" / "electron").exists(),
        "packaged_app": _unpacked_executable(target).exists(),
        "managed_files": len(DESKTOP_FILES),
        "missing_template_files": missing,
        "install_command": install_cmd,
    }


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


def _terminal_cwd(args) -> str:
    raw = getattr(args, "cwd", None)
    path = Path(raw).expanduser() if raw else Path.cwd()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise RuntimeError(f"desktop cwd is not accessible: {path}") from exc
    if not resolved.exists():
        raise RuntimeError(f"desktop cwd not found: {resolved}")
    if not resolved.is_dir():
        raise RuntimeError(f"desktop cwd is not a directory: {resolved}")
    return str(resolved)


def cmd_desktop(args, config) -> int:  # noqa: ARG001
    """Install/update the Electron source-run app, then launch it."""
    npm = shutil.which("npm")
    source = _desktop_source()
    target = _desktop_dir()

    if getattr(args, "status", False):
        _print(json.dumps(_desktop_status(source, target, npm=npm), indent=2, sort_keys=True))
        return 0

    if not npm:
        return _die("`npm` was not found. Install Node.js/npm, then run `aegis desktop` again.")

    try:
        template_changed = _sync_desktop_app(source, target)
    except RuntimeError as exc:
        return _die(str(exc))

    if template_changed:
        _print(f"synced desktop app -> {target}")

    if _needs_npm_install(target, force=getattr(args, "reinstall", False),
                          template_changed=template_changed):
        install_cmd = _npm_install_command(npm, target)
        _print(f"installing desktop dependencies with {' '.join(install_cmd[1:])}...")
        install = subprocess.run(install_cmd, cwd=target)
        if install.returncode != 0:
            return install.returncode
    else:
        _print("desktop dependencies already installed.")

    if getattr(args, "install_only", False):
        _print(f"desktop ready at {target}")
        return 0

    try:
        terminal_cwd = _terminal_cwd(args)
    except RuntimeError as exc:
        return _die(str(exc))

    env = os.environ.copy()
    env.setdefault("AEGIS_BIN", _aegis_bin())
    env["TERMINAL_CWD"] = terminal_cwd

    package = getattr(args, "package", None)
    if package:
        script = {"auto": "dist", "linux": "dist:linux", "win": "dist:win",
                  "mac": "dist:mac"}.get(str(package).lower())
        if script is None:
            return _die(f"unknown --package target '{package}' (use linux, win, or mac).")
        _print(f"building installable app ({package}) — this can take a few minutes...")
        rc = subprocess.run([npm, "run", script], cwd=target, env=env).returncode
        if rc == 0:
            _print(f"done — installer(s) written to {target / 'release'}")
        return rc

    if getattr(args, "source", False):
        run_cmd = [npm, "run", "start:sandbox"] if getattr(args, "sandbox", False) else [npm, "start"]
        return subprocess.run(run_cmd, cwd=target, env=env).returncode

    unpacked = _unpacked_executable(target)
    if template_changed or getattr(args, "reinstall", False) or not unpacked.exists():
        _print("building unpacked desktop app with npm run pack...")
        rc = subprocess.run([npm, "run", "pack"], cwd=target, env=env).returncode
        if rc != 0:
            return rc
        unpacked = _unpacked_executable(target)
        if not unpacked.exists():
            return _die(f"packaged desktop executable was not created: {unpacked}")

    run_cmd = _packaged_launch_command(
        target,
        sandbox=getattr(args, "sandbox", False),
    )
    return subprocess.run(run_cmd, cwd=target, env=env).returncode
