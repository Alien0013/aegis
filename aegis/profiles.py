"""Runtime profile lifecycle helpers.

Profiles are isolated AEGIS homes: the default profile is ``$AEGIS_HOME`` and
named profiles live under ``$AEGIS_HOME/profiles/<name>``.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from . import config as cfg
from .util import atomic_write, ensure_dir

_ROOT_FILES = ("config.yaml", ".env", "auth.json", "SOUL.md", "AGENTS.md", "README.md", "cron.json")
_PROFILE_DIRS = ("personalities", "memories", "skills")
_HISTORY_ROOTS = {
    "logs",
    "state.db",
    "state.db-shm",
    "state.db-wal",
    "runs",
    "traces",
    "trajectories",
    "checkpoints",
    "snapshots",
    "backups",
}
_SECRET_ROOTS = {".env", "auth.json"}
_DEFAULT_ONLY_ROOTS = {"profiles", "active_profile"}

_WORKSPACE_TEMPLATES = {
    "SOUL.md": (
        "# AEGIS Persona\n\n"
        "Be concise, careful, and useful. Ask before high-risk actions.\n"
    ),
    "AGENTS.md": (
        "# AEGIS Operating Rules\n\n"
        "- Prefer small, verifiable changes.\n"
        "- Explain risky actions before running them.\n"
        "- Keep secrets out of logs and replies.\n"
    ),
    "README.md": (
        "# AEGIS Workspace\n\n"
        "This directory is persistent context for AEGIS.\n\n"
        "- SOUL.md: persona and tone.\n"
        "- AGENTS.md: operating rules.\n"
        "- Your profile lives in memories/USER.md.\n"
        "- Durable notes live in memories/MEMORY.md.\n"
    ),
}


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    active: bool
    path: Path
    default: bool = False
    model: str = ""
    provider: str = ""
    skills: int = 0
    memories: int = 0
    cron_jobs: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active": self.active,
            "path": str(self.path),
            "default": self.default,
            "model": self.model,
            "provider": self.provider,
            "skills": self.skills,
            "memories": self.memories,
            "cron_jobs": self.cron_jobs,
        }


def label(profile: str | None = None) -> str:
    return cfg.profile_name(profile) or "default"


def _profile_name(profile: str | None) -> str:
    return cfg.profile_name(profile)


def _path(profile: str | None) -> Path:
    return cfg.profile_home(_profile_name(profile))


def profile_exists(profile: str | None) -> bool:
    name = _profile_name(profile)
    return True if not name else _path(name).is_dir()


def _seed_profile_home(home: Path) -> None:
    ensure_dir(home)
    for dirname in ("personalities", "memories", "skills", "logs"):
        ensure_dir(home / dirname)
    if not (home / "config.yaml").exists():
        atomic_write(home / "config.yaml", "")
    for filename, body in _WORKSPACE_TEMPLATES.items():
        path = home / filename
        if not path.exists() or not path.read_text(encoding="utf-8", errors="replace").strip():
            atomic_write(path, body)
    for filename in ("MEMORY.md", "USER.md"):
        path = home / "memories" / filename
        if not path.exists():
            atomic_write(path, "")


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    elif source.exists():
        ensure_dir(destination.parent)
        shutil.copy2(source, destination)


def _clone_selected(source: Path, destination: Path) -> None:
    ensure_dir(destination)
    for name in _ROOT_FILES:
        _copy_path(source / name, destination / name)
    for name in _PROFILE_DIRS:
        _copy_path(source / name, destination / name)


def _copytree_ignore(source_root: Path, *, include_history: bool, include_secrets: bool):
    source_root = source_root.resolve()

    def _ignore(directory: str, entries: list[str]) -> set[str]:
        current = Path(directory).resolve()
        ignored = {name for name in entries if name == "__pycache__" or name.endswith((".tmp", ".sock"))}
        if current == source_root:
            ignored.update(_DEFAULT_ONLY_ROOTS & set(entries))
            if not include_history:
                ignored.update(_HISTORY_ROOTS & set(entries))
            if not include_secrets:
                ignored.update(_SECRET_ROOTS & set(entries))
        if not include_history and current.name == "cron":
            ignored.update({"output"} & set(entries))
        return ignored

    return _ignore


def create_profile(
    name: str,
    *,
    clone_from: str | None = None,
    clone_config: bool = False,
    clone_all: bool = False,
) -> Path:
    profile = _profile_name(name)
    if not profile:
        raise ValueError("default profile already exists")
    destination = _path(profile)
    if destination.exists():
        raise FileExistsError(f"profile '{profile}' already exists at {destination}")

    if clone_all or clone_config or clone_from is not None:
        source_name = cfg.current_profile() if clone_from is None else _profile_name(clone_from)
        source = _path(source_name)
        if not source.is_dir():
            raise FileNotFoundError(f"source profile '{label(source_name)}' does not exist at {source}")
        if clone_all:
            ensure_dir(destination.parent)
            shutil.copytree(
                source,
                destination,
                ignore=_copytree_ignore(source, include_history=False, include_secrets=True),
            )
        else:
            _clone_selected(source, destination)
    else:
        ensure_dir(destination)

    _seed_profile_home(destination)
    return destination


def clone_profile(source: str, name: str, *, clone_all: bool = False) -> Path:
    return create_profile(name, clone_from=source, clone_config=True, clone_all=clone_all)


def delete_profile(profile: str) -> bool:
    name = _profile_name(profile)
    if not name:
        raise ValueError("the default profile cannot be deleted")
    path = _path(name)
    if not path.exists():
        return False
    shutil.rmtree(path)
    if cfg.current_profile() == name:
        cfg.set_active_profile(None)
        cfg.set_profile(None)
    return True


def rename_profile(source: str, destination_name: str) -> Path:
    source_name = _profile_name(source)
    destination_profile = _profile_name(destination_name)
    if not source_name:
        raise ValueError("the default profile cannot be renamed")
    if not destination_profile:
        raise ValueError("cannot rename a profile to default")
    source_path = _path(source_name)
    destination_path = _path(destination_profile)
    if not source_path.exists():
        raise FileNotFoundError(f"profile '{source_name}' does not exist at {source_path}")
    if destination_path.exists():
        raise FileExistsError(f"profile '{destination_profile}' already exists at {destination_path}")
    ensure_dir(destination_path.parent)
    shutil.move(str(source_path), str(destination_path))
    if cfg.current_profile() == source_name:
        cfg.set_active_profile(destination_profile)
        cfg.set_profile(None)
    return destination_path


def list_profiles() -> list[ProfileInfo]:
    active = cfg.current_profile()
    names = cfg.available_profiles()
    return [profile_info(name, active=active) for name in names]


def _read_config(home: Path) -> dict[str, Any]:
    raw = (home / "config.yaml").read_text(encoding="utf-8", errors="replace") if (home / "config.yaml").exists() else ""
    data = yaml.safe_load(raw) if raw.strip() else {}
    return data if isinstance(data, dict) else {}


def _count_memory_lines(home: Path) -> int:
    total = 0
    for path in (home / "memories").glob("*.md") if (home / "memories").is_dir() else []:
        try:
            total += sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
        except OSError:
            continue
    return total


def _count_cron_jobs(home: Path) -> int:
    path = home / "cron.json"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else []
    except (OSError, yaml.YAMLError):
        return 0
    return len(data) if isinstance(data, list) else 0


def profile_info(profile: str | None = None, *, active: str | None = None) -> ProfileInfo:
    name = _profile_name(profile)
    home = _path(name)
    data = _read_config(home)
    model = str(((data.get("model") or {}).get("default") if isinstance(data.get("model"), dict) else "") or "")
    provider = str(((data.get("model") or {}).get("provider") if isinstance(data.get("model"), dict) else "") or "")
    skills_dir = home / "skills"
    skills = sum(1 for child in skills_dir.iterdir() if child.is_dir()) if skills_dir.is_dir() else 0
    return ProfileInfo(
        name=label(name),
        active=(name == (cfg.current_profile() if active is None else active)),
        path=home,
        default=not name,
        model=model,
        provider=provider,
        skills=skills,
        memories=_count_memory_lines(home),
        cron_jobs=_count_cron_jobs(home),
    )


def use_profile(profile: str | None) -> str:
    name = _profile_name(profile)
    if name and not profile_exists(name):
        raise FileNotFoundError(f"profile '{name}' does not exist")
    cfg.set_active_profile(name)
    cfg.set_profile(None)
    return label(name)


def _archive_roots(archive: Path) -> set[str]:
    roots: set[str] = set()
    with tarfile.open(archive, "r:*") as tf:
        for member in tf.getmembers():
            parts = _safe_member_parts(member.name)
            if parts:
                roots.add(parts[0])
    return roots


def _safe_member_parts(name: str) -> list[str]:
    normalized = name.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(name)
    if not normalized or posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError(f"unsafe archive member path: {name}")
    parts = [part for part in posix.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"unsafe archive member path: {name}")
    return parts


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as tf:
        for member in tf.getmembers():
            parts = _safe_member_parts(member.name)
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"unsupported archive member type: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tf.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read archive member: {member.name}")
            with source, open(target, "wb") as dst:
                shutil.copyfileobj(source, dst)


def export_profile(
    profile: str | None,
    output: str | Path | None = None,
    *,
    include_history: bool = False,
    include_secrets: bool = False,
) -> Path:
    name = _profile_name(profile)
    home = _path(name)
    if not home.is_dir():
        raise FileNotFoundError(f"profile '{label(name)}' does not exist at {home}")
    destination = Path(output).expanduser() if output else Path.cwd() / f"aegis-profile-{label(name)}-{int(time.time())}.tar.gz"
    ensure_dir(destination.parent)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / label(name)
        shutil.copytree(
            home,
            root,
            ignore=_copytree_ignore(home, include_history=include_history, include_secrets=include_secrets),
        )
        with tarfile.open(destination, "w:gz") as tf:
            tf.add(root, arcname=label(name))
    return destination


def import_profile(archive_path: str | Path, *, name: str | None = None) -> Path:
    archive = Path(archive_path).expanduser()
    if not archive.exists():
        raise FileNotFoundError(f"archive not found: {archive}")
    roots = _archive_roots(archive)
    if len(roots) != 1:
        raise ValueError("profile archive must contain exactly one top-level directory")
    archive_root = next(iter(roots))
    target_name = _profile_name(name or archive_root)
    if not target_name:
        raise ValueError("importing over the default profile is not supported; pass --name for a named profile")
    destination = _path(target_name)
    if destination.exists():
        raise FileExistsError(f"profile '{target_name}' already exists at {destination}")

    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        _safe_extract(archive, stage)
        source = stage / archive_root
        if not source.is_dir():
            raise ValueError("profile archive root is not a directory")
        ensure_dir(destination.parent)
        shutil.move(str(source), str(destination))
    _seed_profile_home(destination)
    return destination
