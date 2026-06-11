"""Filesystem checkpoints: snapshot files before edits so changes can be rolled back.

A shadow store under ``~/.aegis/checkpoints/<id>/`` keeps copies of the original
files plus a manifest mapping original→shadow. The tool executor batches all the
edits of one agent turn into ONE checkpoint (the pre-turn state), so `/rollback`
undoes the whole batch and ``diff`` previews everything the turn changed. Files
that did not exist before the turn are recorded with an empty shadow — rollback
deletes them.
"""

from __future__ import annotations

import difflib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config as cfg
from .types import new_id
from .util import atomic_write, now_iso, read_text


def _root() -> Path:
    return cfg.sub("checkpoints")


@dataclass
class Checkpoint:
    id: str
    label: str
    created_at: str
    files: dict[str, str]  # original abs path -> shadow filename ("" = didn't exist)


class CheckpointStore:
    def __init__(self, cwd: Path | None = None):
        self.cwd = cwd or Path.cwd()

    def _abs(self, p: str) -> Path:
        src = Path(p).expanduser()
        return src if src.is_absolute() else self.cwd / src

    def snapshot(self, paths: list[str], label: str = "") -> str | None:
        """Copy the current contents of ``paths`` into a new checkpoint. New (not yet
        existing) files are recorded with an empty shadow so rollback removes them."""
        cp_id = new_id("cp")
        manifest = self._copy_in(cp_id, {}, paths)
        if not manifest:
            return None
        self._write_manifest(cp_id, label, now_iso(), manifest)
        self._prune()
        return cp_id

    def add_to(self, cp_id: str, paths: list[str]) -> None:
        """Extend an existing checkpoint with more files — WITHOUT overwriting shadows
        already taken (the checkpoint stays the pre-batch state, not pre-last-edit)."""
        cp = next((c for c in self.list() if c.id == cp_id), None)
        if cp is None:
            return
        manifest = self._copy_in(cp_id, dict(cp.files), paths)
        self._write_manifest(cp_id, cp.label, cp.created_at, manifest)

    def _copy_in(self, cp_id: str, manifest: dict[str, str], paths: list[str]) -> dict[str, str]:
        cp_dir = _root() / cp_id
        for p in paths:
            src = self._abs(p)
            key = str(src)
            if key in manifest:                       # first version of this file already shadowed
                continue
            cp_dir.mkdir(parents=True, exist_ok=True)
            if src.exists() and src.is_file():
                shadow = f"{len(manifest)}_{src.name}"
                shutil.copy2(src, cp_dir / shadow)
                manifest[key] = shadow
            else:
                manifest[key] = ""                    # new file: rollback = delete
        return manifest

    def _write_manifest(self, cp_id: str, label: str, created_at: str, files: dict) -> None:
        atomic_write(_root() / cp_id / "manifest.json",
                     json.dumps({"id": cp_id, "label": label, "created_at": created_at,
                                 "files": files}, indent=2))

    def _prune(self, keep: int = 40) -> None:
        cps = self.list()
        for c in cps[keep:]:
            shutil.rmtree(_root() / c.id, ignore_errors=True)

    def list(self) -> list[Checkpoint]:
        out: list[Checkpoint] = []
        if not _root().exists():
            return out
        for d in sorted(_root().iterdir(), reverse=True):
            mf = read_text(d / "manifest.json")
            if mf.strip():
                try:
                    data = json.loads(mf)
                    out.append(Checkpoint(**data))
                except (json.JSONDecodeError, TypeError):
                    continue
        return out

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
            before = read_text(cp_dir / shadow) if shadow else ""
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

    def rollback(self, cp_id: str | None = None) -> list[str]:
        """Restore the given (or latest) checkpoint. Returns restored/removed paths."""
        cp = self.get(cp_id)
        if cp is None:
            return []
        restored: list[str] = []
        cp_dir = _root() / cp.id
        for original, shadow in cp.files.items():
            if shadow:
                shadow_path = cp_dir / shadow
                if shadow_path.exists():
                    Path(original).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(shadow_path, original)
                    restored.append(original)
            else:                                     # file was created by the batch — remove it
                try:
                    Path(original).unlink(missing_ok=True)
                    restored.append(f"{original} (removed)")
                except OSError:
                    pass
        return restored

    def clear(self) -> int:
        n = len(self.list())
        if _root().exists():
            shutil.rmtree(_root())
        return n
