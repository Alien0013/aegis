"""Filesystem checkpoints: snapshot files before edits so changes can be rolled back.

A shared git object store under ``~/.aegis/checkpoints/store/`` deduplicates
snapshotted file contents across checkpoint manifests. If git is unavailable,
AEGIS falls back to per-checkpoint shadow copies under
``~/.aegis/checkpoints/<id>/``. The tool executor batches all the edits of one
agent turn into ONE checkpoint (the pre-turn state), so `/rollback` undoes the
whole batch and ``diff`` previews everything the turn changed. Files that did
not exist before the turn are recorded with an empty shadow — rollback deletes
them.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as cfg
from .types import new_id
from .util import atomic_write, now_iso, read_text


def _root() -> Path:
    return cfg.sub("checkpoints")


_PROJECT_MARKERS = {
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "pom.xml",
    ".hg",
    "Gemfile",
}
_DEFAULT_MAX_SNAPSHOT_FILES = 50_000
_DEFAULT_MAX_FILE_SIZE_MB = 10
_DEFAULT_MAX_TOTAL_SIZE_MB = 500


def _safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError):
        return path.expanduser().absolute()


def _is_broad_checkpoint_parent(path: Path) -> bool:
    resolved = _safe_resolve(path)
    boundaries = {_safe_resolve(Path(tempfile.gettempdir()))}
    try:
        boundaries.add(_safe_resolve(Path.home()))
    except RuntimeError:
        pass
    return resolved in boundaries


@dataclass
class Checkpoint:
    id: str
    label: str
    created_at: str
    files: dict[str, str]  # original abs path -> shadow filename ("" = didn't exist)
    workdir: str = ""
    git_commit: str = ""
    git_store_version: int = 0


class CheckpointStore:
    def __init__(
        self,
        cwd: Path | None = None,
        *,
        max_snapshot_files: int = _DEFAULT_MAX_SNAPSHOT_FILES,
        max_file_size_mb: float = _DEFAULT_MAX_FILE_SIZE_MB,
        max_total_size_mb: float = _DEFAULT_MAX_TOTAL_SIZE_MB,
    ):
        self.cwd = cwd or Path.cwd()
        self.workdir = Path(self.get_working_dir_for_path(str(self.cwd)))
        self.max_snapshot_files = max(0, int(max_snapshot_files))
        self.max_file_size_mb = max(0.0, float(max_file_size_mb))
        self.max_total_size_mb = max(0.0, float(max_total_size_mb))

    def _abs(self, p: str) -> Path:
        src = Path(p).expanduser()
        return src if src.is_absolute() else self.cwd / src

    def snapshot(self, paths: list[str], label: str = "") -> str | None:
        """Copy the current contents of ``paths`` into a new checkpoint. New (not yet
        existing) files are recorded with an empty shadow so rollback removes them."""
        if self.max_snapshot_files > 0 and _dir_file_count(self.workdir, self.max_snapshot_files) > self.max_snapshot_files:
            return None
        cp_id = new_id("cp")
        manifest = self._copy_in(cp_id, {}, paths)
        if not manifest:
            return None
        git_commit = _write_git_checkpoint(cp_id, label, manifest)
        self._write_manifest(cp_id, label, now_iso(), manifest, git_commit=git_commit)
        self._prune()
        if self.max_total_size_mb > 0:
            prune_checkpoints(max_total_size_mb=self.max_total_size_mb)
        return cp_id

    def add_to(self, cp_id: str, paths: list[str]) -> None:
        """Extend an existing checkpoint with more files — WITHOUT overwriting shadows
        already taken (the checkpoint stays the pre-batch state, not pre-last-edit)."""
        cp = next((c for c in self.list() if c.id == cp_id), None)
        if cp is None:
            return
        manifest = self._copy_in(cp_id, dict(cp.files), paths)
        git_commit = _write_git_checkpoint(cp_id, cp.label, manifest, parent=cp.git_commit)
        self._write_manifest(
            cp_id,
            cp.label,
            cp.created_at,
            manifest,
            workdir=cp.workdir,
            git_commit=git_commit or cp.git_commit,
        )

    def _copy_in(self, cp_id: str, manifest: dict[str, str], paths: list[str]) -> dict[str, str]:
        cp_dir = _root() / cp_id
        for p in paths:
            src = self._abs(p)
            key = str(src)
            if key in manifest:                       # first version of this file already shadowed
                continue
            if src.exists() and src.is_file():
                if self._file_exceeds_size_cap(src):
                    continue
                cp_dir.mkdir(parents=True, exist_ok=True)
                shadow = _write_git_shadow(src, key)
                if not shadow:
                    shadow = f"{len(manifest)}_{src.name}"
                    shutil.copy2(src, cp_dir / shadow)
                manifest[key] = shadow
            else:
                cp_dir.mkdir(parents=True, exist_ok=True)
                manifest[key] = ""                    # new file: rollback = delete
        return manifest

    def _file_exceeds_size_cap(self, path: Path) -> bool:
        if self.max_file_size_mb <= 0:
            return False
        try:
            return path.stat().st_size > int(self.max_file_size_mb * 1024 * 1024)
        except OSError:
            return False

    def _write_manifest(
        self,
        cp_id: str,
        label: str,
        created_at: str,
        files: dict,
        *,
        workdir: str | None = None,
        git_commit: str = "",
    ) -> None:
        atomic_write(
            _root() / cp_id / "manifest.json",
            json.dumps(
                {
                    "id": cp_id,
                    "label": label,
                    "created_at": created_at,
                    "workdir": workdir or str(self.workdir.resolve()),
                    "files": files,
                    "git_commit": git_commit,
                    "git_store_version": 1 if git_commit else 0,
                },
                indent=2,
            ),
        )

    def _prune(self, keep: int = 40) -> None:
        cps = self.list()
        pruned = False
        for c in cps[keep:]:
            _delete_git_checkpoint(c.id)
            shutil.rmtree(_root() / c.id, ignore_errors=True)
            pruned = True
        if pruned:
            _gc_git_store()

    def list(self) -> list[Checkpoint]:
        rows: list[tuple[str, float, str, Checkpoint]] = []
        if not _root().exists():
            return []
        for d in _checkpoint_dirs(_root()):
            mf = read_text(d / "manifest.json")
            if mf.strip():
                try:
                    data = json.loads(mf)
                    cp = Checkpoint(**data)
                    rows.append((cp.created_at or "", _checkpoint_mtime(d), cp.id, cp))
                except (json.JSONDecodeError, TypeError):
                    continue
        return [cp for _created, _mtime, _id, cp in sorted(rows, reverse=True)]

    def history(self, *, limit: int = 40, workdir: str | Path | None = None) -> list[dict[str, Any]]:
        max_rows = max(0, int(limit))
        if max_rows == 0:
            return []
        target = None
        if workdir is not None:
            target = _safe_resolve(Path(workdir))
        rows: list[dict[str, Any]] = []
        for cp in self.list():
            cp_workdir = _safe_resolve(Path(cp.workdir)) if cp.workdir else None
            if target is not None and cp_workdir != target:
                continue
            rows.append(
                {
                    "id": cp.id,
                    "short_id": cp.id[:12],
                    "git_commit": cp.git_commit,
                    "short_commit": cp.git_commit[:8] if cp.git_commit else "",
                    "timestamp": cp.created_at,
                    "reason": cp.label,
                    "files_changed": len(cp.files),
                    "workdir": cp.workdir,
                    "exists": bool(cp.workdir) and Path(cp.workdir).exists(),
                }
            )
            if len(rows) >= max_rows:
                break
        return rows

    def status(self) -> dict[str, Any]:
        return store_status()

    def get(self, cp_id: str | None = None) -> Checkpoint | None:
        cps = self.list()
        if not cps:
            return None
        if not cp_id:
            return cps[0]
        return next((c for c in cps if c.id.startswith(cp_id)), None)

    def diff(self, cp_id: str | None = None, *, context: int = 3) -> str:
        """Unified diff: checkpoint (pre-edit) state -> current files on disk."""
        cp = self.get(cp_id)
        if cp is None:
            return ""
        cp_dir = _root() / cp.id
        chunks: list[str] = []
        for original, shadow in cp.files.items():
            before = _read_shadow_text(cp_dir, shadow) if shadow else ""
            after = read_text(Path(original)) if Path(original).exists() else ""
            if before == after:
                continue
            rel = original
            d = difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{rel}" + ("" if shadow else " (new file)"),
                tofile=f"b/{rel}", n=context)
            chunks.append("".join(d))
        return "\n".join(chunks)

    def _validated_restore_target(self, file_path: str) -> tuple[Path | None, str | None]:
        if not file_path or not file_path.strip():
            return None, "Empty file path"
        raw = Path(file_path)
        if raw.is_absolute():
            return None, f"File path must be relative, got absolute path: {file_path!r}"
        try:
            base = self.cwd.resolve()
            target = (base / raw).resolve()
            target.relative_to(base)
        except ValueError:
            return None, f"File path escapes the working directory via traversal: {file_path!r}"
        except (OSError, RuntimeError) as exc:
            return None, f"Could not resolve file path {file_path!r}: {exc}"
        return target, None

    @staticmethod
    def _resolved_original(path: str) -> Path:
        try:
            return Path(path).resolve()
        except (OSError, RuntimeError):
            return Path(path).absolute()

    def _restore_items(
        self,
        cp: Checkpoint,
        file_path: str | None,
    ) -> tuple[list[tuple[str, str]], str | None]:
        if file_path is None:
            return list(cp.files.items()), None
        target, err = self._validated_restore_target(file_path)
        if err:
            return [], err
        items = [
            (original, shadow)
            for original, shadow in cp.files.items()
            if self._resolved_original(original) == target
        ]
        if not items:
            return [], f"File path not found in checkpoint: {file_path!r}"
        return items, None

    def restore(self, cp_id: str | None = None, *, file_path: str | None = None) -> dict[str, Any]:
        """Restore a full checkpoint or one relative path from it."""
        cp = self.get(cp_id)
        if cp is None:
            return {"success": False, "restored": [], "error": "No checkpoints exist"}
        items, err = self._restore_items(cp, file_path)
        if err:
            return {"success": False, "restored": [], "error": err, "checkpoint": cp.id}
        restored: list[str] = []
        cp_dir = _root() / cp.id
        for original, shadow in items:
            if shadow:
                data = _read_shadow_bytes(cp_dir, shadow)
                if data is not None:
                    Path(original).parent.mkdir(parents=True, exist_ok=True)
                    Path(original).write_bytes(data)
                    restored.append(original)
            else:                                     # file was created by the batch — remove it
                try:
                    Path(original).unlink(missing_ok=True)
                    restored.append(f"{original} (removed)")
                except OSError:
                    pass
        result: dict[str, Any] = {"success": True, "checkpoint": cp.id, "restored": restored}
        if file_path is not None:
            result["file"] = file_path
        return result

    def rollback(self, cp_id: str | None = None, *, file_path: str | None = None) -> list[str]:
        """Restore the given (or latest) checkpoint. Returns restored/removed paths."""
        result = self.restore(cp_id, file_path=file_path)
        return list(result.get("restored") or [])

    def clear(self) -> int:
        n = len(self.list())
        if _root().exists():
            shutil.rmtree(_root())
        return n

    def clear_legacy(self) -> dict[str, int]:
        return clear_legacy()

    def prune(
        self,
        *,
        older_than_days: float = 7,
        delete_orphans: bool = True,
        keep: int | None = None,
        max_total_size_mb: float = 0,
    ) -> dict[str, int]:
        return prune_checkpoints(
            older_than_days=older_than_days,
            delete_orphans=delete_orphans,
            keep=keep,
            max_total_size_mb=max_total_size_mb,
        )

    @staticmethod
    def get_working_dir_for_path(file_path: str) -> str:
        path = _safe_resolve(Path(file_path))
        candidate = path if path.is_dir() else path.parent
        check = candidate
        while check != check.parent:
            if check != candidate and _is_broad_checkpoint_parent(check):
                break
            if any((check / marker).exists() for marker in _PROJECT_MARKERS):
                return str(check)
            check = check.parent
        return str(candidate)


_GIT_STORE_DIR = "store"
_GIT_INDEX_DIR = "indexes"
_GIT_REF_PREFIX = "refs/aegis/checkpoints"
_GIT_SHADOW_PREFIX = "git:"
_GIT_TIMEOUT = 30
_LEGACY_PREFIX = "legacy-"


def _git_store(root: Path | None = None) -> Path:
    return (root or _root()) / _GIT_STORE_DIR


def _git_index_path(cp_id: str, root: Path | None = None) -> Path:
    return _git_store(root) / _GIT_INDEX_DIR / cp_id


def _git_ref(cp_id: str) -> str:
    return f"{_GIT_REF_PREFIX}/{cp_id}"


def _git_env(*, index_file: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NAMESPACE",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(key, None)
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    return env


def _init_git_store(root: Path | None = None) -> bool:
    store = _git_store(root)
    if (store / "HEAD").exists():
        (store / _GIT_INDEX_DIR).mkdir(parents=True, exist_ok=True)
        return True
    if shutil.which("git") is None:
        return False
    store.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "init", "--bare", str(store)],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=_git_env(),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    (store / _GIT_INDEX_DIR).mkdir(parents=True, exist_ok=True)
    for key, value in (
        ("user.email", "aegis@local"),
        ("user.name", "AEGIS Checkpoint"),
        ("commit.gpgsign", "false"),
        ("tag.gpgSign", "false"),
        ("gc.auto", "0"),
    ):
        _run_git_store(["config", key, value], root=root)
    info = store / "info"
    info.mkdir(exist_ok=True)
    exclude = info / "exclude"
    if not exclude.exists():
        exclude.write_text("\n".join([".git/", "__pycache__/", "node_modules/", ".env", "*.log"]) + "\n",
                           encoding="utf-8")
    return True


def _run_git_store(
    args: list[str],
    *,
    root: Path | None = None,
    index_file: Path | None = None,
    input_bytes: bytes | None = None,
    allowed_returncodes: set[int] | None = None,
) -> tuple[bool, bytes, str]:
    store = _git_store(root)
    if not (store / "HEAD").exists() and (not args or args[0] != "init"):
        return False, b"", "checkpoint git store is not initialized"
    allowed_returncodes = allowed_returncodes or set()
    kwargs: dict[str, Any] = {}
    if input_bytes is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input_bytes
    try:
        result = subprocess.run(
            ["git", "--git-dir", str(store)] + args,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            env=_git_env(index_file=index_file),
            text=False,
            **kwargs,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, b"", str(exc)
    ok = result.returncode == 0
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode in allowed_returncodes:
        return ok, result.stdout or b"", stderr
    return ok, result.stdout or b"", stderr


def _git_entry_path(original: str) -> str:
    digest = hashlib.sha256(original.encode("utf-8", errors="surrogateescape")).hexdigest()
    return f"files/{digest}"


def _is_git_shadow(shadow: str) -> bool:
    return shadow.startswith(_GIT_SHADOW_PREFIX)


def _parse_git_shadow(shadow: str) -> tuple[str, str] | None:
    if not _is_git_shadow(shadow):
        return None
    parts = shadow.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def _write_git_shadow(path: Path, original: str) -> str:
    if not _init_git_store():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    ok, stdout, _ = _run_git_store(["hash-object", "-w", "--stdin"], input_bytes=data)
    if not ok:
        return ""
    sha = stdout.decode("ascii", errors="ignore").strip()
    if not sha:
        return ""
    return f"{_GIT_SHADOW_PREFIX}{sha}:{_git_entry_path(original)}"


def _write_git_checkpoint(
    cp_id: str,
    label: str,
    files: dict[str, str],
    *,
    parent: str = "",
) -> str:
    git_items = [
        (original, parsed[0], parsed[1])
        for original, shadow in files.items()
        if (parsed := _parse_git_shadow(shadow)) is not None
    ]
    if not git_items or not _init_git_store():
        return ""
    index_file = _git_index_path(cp_id)
    index_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        index_file.unlink(missing_ok=True)
    except OSError:
        pass
    for _original, sha, entry in git_items:
        ok, _, _ = _run_git_store(
            ["update-index", "--add", "--cacheinfo", f"100644,{sha},{entry}"],
            index_file=index_file,
        )
        if not ok:
            return ""
    ok_tree, tree_out, _ = _run_git_store(["write-tree"], index_file=index_file)
    if not ok_tree:
        return ""
    tree = tree_out.decode("ascii", errors="ignore").strip()
    if not tree:
        return ""
    message = label or cp_id
    args = ["commit-tree", tree, "-m", message, "--no-gpg-sign"]
    if parent:
        ok_parent, _, _ = _run_git_store(["cat-file", "-e", f"{parent}^{{commit}}"])
        if ok_parent:
            args = ["commit-tree", tree, "-p", parent, "-m", message, "--no-gpg-sign"]
    ok_commit, commit_out, _ = _run_git_store(args, index_file=index_file)
    if not ok_commit:
        return ""
    commit = commit_out.decode("ascii", errors="ignore").strip()
    if not commit:
        return ""
    ok_ref, _, _ = _run_git_store(["update-ref", _git_ref(cp_id), commit])
    return commit if ok_ref else ""


def _read_git_blob(sha: str) -> bytes | None:
    if not _init_git_store():
        return None
    ok, stdout, _ = _run_git_store(["cat-file", "blob", sha])
    return stdout if ok else None


def _read_shadow_bytes(cp_dir: Path, shadow: str) -> bytes | None:
    parsed = _parse_git_shadow(shadow)
    if parsed is not None:
        return _read_git_blob(parsed[0])
    shadow_path = cp_dir / shadow
    if not shadow_path.exists():
        return None
    try:
        return shadow_path.read_bytes()
    except OSError:
        return None


def _read_shadow_text(cp_dir: Path, shadow: str) -> str:
    data = _read_shadow_bytes(cp_dir, shadow)
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace")


def _delete_git_checkpoint(cp_id: str, root: Path | None = None) -> None:
    store = _git_store(root)
    if not (store / "HEAD").exists():
        return
    _run_git_store(["update-ref", "-d", _git_ref(cp_id)], root=root, allowed_returncodes={1, 128})
    try:
        _git_index_path(cp_id, root).unlink(missing_ok=True)
    except OSError:
        pass


def _gc_git_store(root: Path | None = None) -> None:
    store = _git_store(root)
    if not (store / "HEAD").exists():
        return
    _run_git_store(["reflog", "expire", "--expire=now", "--all"], root=root)
    _run_git_store(["gc", "--prune=now", "--quiet"], root=root)


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _dir_file_count(path: Path, stop_after: int = _DEFAULT_MAX_SNAPSHOT_FILES) -> int:
    count = 0
    if not path.exists():
        return count
    try:
        for _item in path.rglob("*"):
            count += 1
            if stop_after > 0 and count > stop_after:
                return count
    except OSError:
        return count
    return count


def _read_manifest(path: Path) -> dict[str, Any] | None:
    raw = read_text(path)
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _checkpoint_dirs(root: Path | None = None) -> list[Path]:
    base = root or _root()
    if not base.exists():
        return []
    return [
        path for path in sorted(base.iterdir(), reverse=True)
        if path.is_dir() and (path / "manifest.json").exists()
    ]


def _checkpoint_mtime(path: Path) -> float:
    try:
        return (path / "manifest.json").stat().st_mtime
    except OSError:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0


def _manifest_workdir(data: dict[str, Any] | None) -> str:
    if not isinstance(data, dict):
        return ""
    workdir = str(data.get("workdir") or "")
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    if not workdir and files:
        try:
            workdir = str(Path(next(iter(files))).parent)
        except Exception:
            workdir = ""
    return workdir


def _project_rows(root: Path) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for cp_dir in _checkpoint_dirs(root):
        data = _read_manifest(cp_dir / "manifest.json")
        if not data:
            continue
        workdir = _manifest_workdir(data)
        key = workdir or "(unknown)"
        row = projects.setdefault(
            key,
            {
                "workdir": workdir,
                "exists": bool(workdir) and Path(workdir).exists(),
                "checkpoints": 0,
                "last_touch": 0.0,
                "size_bytes": 0,
            },
        )
        row["checkpoints"] += 1
        row["last_touch"] = max(float(row["last_touch"] or 0), _checkpoint_mtime(cp_dir))
        row["size_bytes"] += _dir_size_bytes(cp_dir)
    return sorted(projects.values(), key=lambda row: row.get("last_touch") or 0, reverse=True)


def store_status(root: Path | None = None) -> dict[str, Any]:
    """Return reference-style operational status for the checkpoint store."""
    base = root or _root()
    checkpoints = _checkpoint_dirs(base)
    projects = _project_rows(base)
    git_store = _git_store(base)
    legacy_archives = _legacy_archives(base)
    legacy_size = sum(int(row.get("size_bytes") or 0) for row in legacy_archives)
    return {
        "base": str(base),
        "total_size_bytes": _dir_size_bytes(base),
        "store_size_bytes": _dir_size_bytes(git_store),
        "legacy_size_bytes": legacy_size,
        "git_store": {
            "path": str(git_store),
            "exists": (git_store / "HEAD").exists(),
            "size_bytes": _dir_size_bytes(git_store),
        },
        "checkpoint_count": len(checkpoints),
        "project_count": len(projects),
        "projects": projects,
        "legacy_archives": legacy_archives,
    }


def prune_checkpoints(
    *,
    older_than_days: float = 7,
    delete_orphans: bool = True,
    keep: int | None = None,
    max_total_size_mb: float = 0,
    root: Path | None = None,
) -> dict[str, int]:
    """Delete stale/orphan checkpoints and enforce a global keep count.

    This mirrors the reference checkpoint maintenance policy for AEGIS's current
    shadow-copy store: maintenance is best-effort, never raises, and reports
    scanned/deleted/error/byte counts for CLI/status surfaces.
    """
    base = root or _root()
    result = {
        "scanned": 0,
        "deleted_orphan": 0,
        "deleted_stale": 0,
        "deleted_over_limit": 0,
        "errors": 0,
        "bytes_freed": 0,
    }
    dirs = _checkpoint_dirs(base)
    if not dirs:
        return result

    cutoff = time.time() - float(older_than_days) * 86400 if older_than_days > 0 else 0.0
    survivors: list[Path] = []
    for cp_dir in dirs:
        result["scanned"] += 1
        data = _read_manifest(cp_dir / "manifest.json")
        workdir = _manifest_workdir(data)
        reason = ""
        if delete_orphans and workdir and not Path(workdir).exists():
            reason = "orphan"
        elif cutoff and _checkpoint_mtime(cp_dir) < cutoff:
            reason = "stale"
        if reason:
            _delete_checkpoint_dir(cp_dir, result, reason)
        else:
            survivors.append(cp_dir)

    if keep is not None and int(keep) >= 0:
        keep_set = set(sorted(survivors, key=_checkpoint_mtime, reverse=True)[:int(keep)])
        for cp_dir in sorted(survivors, key=_checkpoint_mtime, reverse=True)[int(keep):]:
            _delete_checkpoint_dir(cp_dir, result, "over_limit")
        survivors = [cp_dir for cp_dir in survivors if cp_dir in keep_set and cp_dir.exists()]

    if max_total_size_mb > 0:
        _prune_to_size_cap(base, survivors, float(max_total_size_mb), result)
    if result["deleted_orphan"] or result["deleted_stale"] or result["deleted_over_limit"]:
        _gc_git_store(base)
    return result


def _prune_to_size_cap(
    base: Path,
    survivors: list[Path],
    max_total_size_mb: float,
    result: dict[str, int],
) -> None:
    cap_bytes = int(max_total_size_mb * 1024 * 1024)
    if cap_bytes <= 0 or _dir_size_bytes(base) <= cap_bytes:
        return
    remaining = [cp_dir for cp_dir in survivors if cp_dir.exists()]
    workdir_counts: dict[str, int] = {}
    for cp_dir in remaining:
        workdir = _manifest_workdir(_read_manifest(cp_dir / "manifest.json")) or cp_dir.name
        workdir_counts[workdir] = workdir_counts.get(workdir, 0) + 1

    for cp_dir in sorted(remaining, key=_checkpoint_mtime):
        if _dir_size_bytes(base) <= cap_bytes:
            break
        workdir = _manifest_workdir(_read_manifest(cp_dir / "manifest.json")) or cp_dir.name
        if workdir_counts.get(workdir, 0) <= 1:
            continue
        _delete_checkpoint_dir(cp_dir, result, "over_limit")
        workdir_counts[workdir] = workdir_counts.get(workdir, 1) - 1


def _delete_checkpoint_dir(cp_dir: Path, result: dict[str, int], reason: str) -> None:
    try:
        _delete_git_checkpoint(cp_dir.name, cp_dir.parent)
        size = _dir_size_bytes(cp_dir)
        shutil.rmtree(cp_dir)
        result["bytes_freed"] += size
        if reason == "orphan":
            result["deleted_orphan"] += 1
        elif reason == "stale":
            result["deleted_stale"] += 1
        else:
            result["deleted_over_limit"] += 1
    except OSError:
        result["errors"] += 1


def maybe_auto_prune_checkpoints(
    *,
    older_than_days: float = 7,
    min_interval_hours: float = 24,
    delete_orphans: bool = True,
    keep: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Idempotent best-effort checkpoint maintenance with a run marker."""
    base = root or _root()
    if not base.exists():
        return {"skipped": False, "result": prune_checkpoints(root=base)}
    marker = base / ".last_prune"
    now = time.time()
    if marker.exists():
        try:
            last = float(marker.read_text(encoding="utf-8").strip())
            if now - last < float(min_interval_hours) * 3600:
                return {"skipped": True}
        except (OSError, ValueError):
            pass
    result = prune_checkpoints(
        older_than_days=older_than_days,
        delete_orphans=delete_orphans,
        keep=keep,
        root=base,
    )
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(now), encoding="utf-8")
    except OSError as exc:
        return {"skipped": False, "result": result, "error": str(exc)}
    return {"skipped": False, "result": result}


def clear_all(root: Path | None = None) -> dict[str, int | bool]:
    base = root or _root()
    out: dict[str, int | bool] = {"bytes_freed": 0, "deleted": False}
    if not base.exists():
        return out
    size = _dir_size_bytes(base)
    try:
        shutil.rmtree(base)
    except OSError:
        return out
    out["bytes_freed"] = size
    out["deleted"] = True
    return out


def _legacy_archives(base: Path) -> list[dict[str, Any]]:
    if not base.exists():
        return []
    try:
        children = list(base.iterdir())
    except OSError:
        return []
    archives: list[dict[str, Any]] = []
    for child in children:
        if not child.is_dir() or not child.name.startswith(_LEGACY_PREFIX):
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            mtime = 0.0
        archives.append(
            {
                "name": child.name,
                "path": str(child),
                "size_bytes": _dir_size_bytes(child),
                "mtime": mtime,
            }
        )
    return sorted(archives, key=lambda row: row.get("mtime") or 0, reverse=True)


def clear_legacy(root: Path | None = None) -> dict[str, int]:
    base = root or _root()
    out = {"bytes_freed": 0, "deleted": 0, "errors": 0}
    if not base.exists():
        return out
    for archive in _legacy_archives(base):
        path = Path(str(archive.get("path") or ""))
        try:
            size = _dir_size_bytes(path)
            shutil.rmtree(path)
            out["bytes_freed"] += size
            out["deleted"] += 1
        except OSError:
            out["errors"] += 1
    return out
