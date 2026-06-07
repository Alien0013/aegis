"""Filesystem checkpoints: snapshot files before edits so changes can be rolled back.

A shadow store under ``~/.aegis/checkpoints/<id>/`` keeps copies of the original
files plus a manifest mapping shadow→original. The tool executor snapshots files
before ``write_file``/``edit_file``/``apply_patch``; ``/rollback`` restores them.
"""

from __future__ import annotations

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
    files: dict[str, str]  # original abs path -> shadow filename


class CheckpointStore:
    def __init__(self, cwd: Path | None = None):
        self.cwd = cwd or Path.cwd()

    def snapshot(self, paths: list[str], label: str = "") -> str | None:
        """Copy the current contents of ``paths`` into a new checkpoint."""
        cp_id = new_id("cp")
        cp_dir = _root() / cp_id
        manifest: dict[str, str] = {}
        for i, p in enumerate(paths):
            src = Path(p).expanduser()
            if not src.is_absolute():
                src = self.cwd / src
            if not src.exists() or not src.is_file():
                continue  # new file — nothing to back up (rollback = delete handled below)
            cp_dir.mkdir(parents=True, exist_ok=True)
            shadow = f"{i}_{src.name}"
            shutil.copy2(src, cp_dir / shadow)
            manifest[str(src)] = shadow
        if not manifest:
            return None
        atomic_write(cp_dir / "manifest.json",
                     json.dumps({"id": cp_id, "label": label, "created_at": now_iso(),
                                 "files": manifest}, indent=2))
        return cp_id

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

    def rollback(self, cp_id: str | None = None) -> list[str]:
        """Restore the given (or latest) checkpoint. Returns restored paths."""
        cps = self.list()
        if not cps:
            return []
        cp = next((c for c in cps if c.id.startswith(cp_id)), None) if cp_id else cps[0]
        if cp is None:
            return []
        restored: list[str] = []
        cp_dir = _root() / cp.id
        for original, shadow in cp.files.items():
            shadow_path = cp_dir / shadow
            if shadow_path.exists():
                Path(original).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shadow_path, original)
                restored.append(original)
        return restored

    def clear(self) -> int:
        n = len(self.list())
        if _root().exists():
            shutil.rmtree(_root())
        return n
