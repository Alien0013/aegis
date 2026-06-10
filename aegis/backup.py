"""Backup and restore of the AEGIS runtime home.

Bundles the meaningful contents of ``$AEGIS_HOME`` (``~/.aegis``) into a single
``.zip`` so a profile can be moved between machines or snapshotted before risky
changes. The session database (``state.db``) is large and volatile, so a
``quick`` backup omits it.

Included entries (relative to the home):

    config.yaml      main configuration
    .env             secrets (API keys)
    auth.json        OAuth tokens
    cron.json        scheduled jobs
    workspace/       SOUL.md, AGENTS.md, USER.md (identity + rules)
    skills/          SKILL.md packages
    memories/        MEMORY.md, USER.md, history.jsonl
    state.db         sessions (omitted when ``quick=True``)

Restore extracts back into the home, refusing any archive member whose path
would escape it (zip-slip guard).
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg
from .util import ensure_dir, human_size

# Top-level files included verbatim, and directories included recursively.
_BACKUP_FILES = ("config.yaml", ".env", "auth.json", "cron.json")
_BACKUP_DIRS = ("workspace", "skills", "memories")
_STATE_DB = "state.db"


def _iter_entries(home: Path, quick: bool) -> list[Path]:
    """Resolve which existing paths under ``home`` should be archived."""
    entries: list[Path] = []
    for name in _BACKUP_FILES:
        p = home / name
        if p.is_file():
            entries.append(p)
    if not quick:
        db = home / _STATE_DB
        if db.is_file():
            entries.append(db)
    for name in _BACKUP_DIRS:
        d = home / name
        if d.is_dir():
            entries.extend(p for p in sorted(d.rglob("*")) if p.is_file())
    return entries


def create_backup(out_path: Path | None = None, quick: bool = False) -> Path:
    """Zip the runtime home into ``out_path`` and return it.

    ``out_path`` defaults to ``~/.aegis/backups/aegis-backup-<UTC stamp>.zip``.
    When ``quick`` is set, ``state.db`` is excluded.
    """
    home = cfg.get_home()
    if out_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        suffix = "-quick" if quick else ""
        out_path = ensure_dir(home / "backups") / f"aegis-backup-{stamp}{suffix}.zip"
    out_path = Path(out_path).expanduser()
    ensure_dir(out_path.parent)

    entries = _iter_entries(home, quick)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in entries:
            zf.write(path, arcname=str(path.relative_to(home)))
    return out_path


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def restore_backup(zip_path: Path) -> list[str]:
    """Extract ``zip_path`` into the runtime home, overwriting existing entries.

    Returns the archive-relative names that were restored. Members whose path
    would escape the home directory (zip-slip) are skipped.
    """
    zip_path = Path(zip_path).expanduser()
    if not zip_path.is_file():
        raise FileNotFoundError(f"backup not found: {zip_path}")

    home = ensure_dir(cfg.get_home())
    restored: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member.endswith("/"):
                continue
            dest = home / member
            if not _is_within(home, dest):
                continue  # zip-slip guard
            ensure_dir(dest.parent)
            with zf.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())
            restored.append(member)
    return restored


# --------------------------------------------------------------------------- #
# CLI commands  (signature: cmd_*(args, config) -> int)
# --------------------------------------------------------------------------- #
def cmd_backup(args, config) -> int:
    """``aegis backup [--quick] [--out PATH]`` — write a home backup zip."""
    out = Path(args.out).expanduser() if getattr(args, "out", None) else None
    path = create_backup(out_path=out, quick=getattr(args, "quick", False))
    size = human_size(path.stat().st_size)
    kind = "quick " if getattr(args, "quick", False) else ""
    print(f"wrote {kind}backup -> {path} ({size})")
    return 0


def _snap_dir() -> Path:
    return ensure_dir(cfg.get_home() / "snapshots")


def make_snapshot(label: str = "manual") -> Path:
    """A quick (no state.db) labeled snapshot of config/secrets/auth/workspace."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40] or "snap"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return create_backup(out_path=_snap_dir() / f"{stamp}-{safe}.zip", quick=True)


def prune_snapshots(keep: int = 10) -> int:
    snaps = sorted(_snap_dir().glob("*.zip"))
    removed = 0
    for p in snaps[:-keep] if keep > 0 else []:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def cmd_snapshot(args, config) -> int:
    """``aegis snapshot [create [label] | restore <id> | prune [N] | list]``."""
    import sys
    sub = (getattr(args, "action", None) or "list")
    if sub == "create":
        path = make_snapshot(getattr(args, "label", None) or "manual")
        prune_snapshots(int(config.get("snapshots.keep", 10)))
        print(f"snapshot created -> {path.name}")
        return 0
    if sub == "prune":
        n = prune_snapshots(int(getattr(args, "label", None) or 10))
        print(f"pruned {n} old snapshot(s)")
        return 0
    if sub == "restore":
        sid = getattr(args, "label", None)
        snaps = sorted(_snap_dir().glob("*.zip"))
        match = next((p for p in snaps if sid and p.name.startswith(sid)), None) \
            or (snaps[-1] if sid in ("latest", None) else None)
        if match is None:
            print(f"error: no snapshot matching '{sid}'", file=sys.stderr)
            return 1
        restored = restore_backup(match)
        print(f"restored {len(restored)} entries from {match.name}")
        return 0
    # list
    snaps = sorted(_snap_dir().glob("*.zip"))
    if not snaps:
        print("no snapshots yet. `aegis snapshot create [label]` to make one.")
        return 0
    for p in snaps:
        print(f"  {p.name}  ({human_size(p.stat().st_size)})")
    return 0


def cmd_import(args, config) -> int:
    """``aegis import <zip>`` — restore a home backup zip."""
    src = getattr(args, "path", None)
    if not src:
        print("error: usage: aegis import <backup.zip>", file=__import__("sys").stderr)
        return 1
    try:
        restored = restore_backup(Path(src))
    except (FileNotFoundError, zipfile.BadZipFile) as e:
        print(f"error: {e}", file=__import__("sys").stderr)
        return 1
    print(f"restored {len(restored)} entries into {cfg.get_home()}")
    for name in restored:
        print(f"  {name}")
    return 0
