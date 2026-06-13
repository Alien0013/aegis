"""Skill curator: background maintenance of personal skills.

Operates over the personal skill directory (``cfg.skills_dir()``) — the tier the
user owns and can safely rearrange. It does three jobs:

  * **review**   — scan every skill dir and report problems: stale (not used
    recently), near-duplicates (by description similarity), and malformed
    SKILL.md (missing/unparseable frontmatter, no name/description).
  * **prune**    — suggest stale skills for archival (dry-run by default).
  * **archive / restore** — move a skill dir to ``~/.aegis/skills_archive`` and
    back, without deleting anything.

Usage is tracked best-effort in ``~/.aegis/skills/usage.json``
(``{name: {count, last_used}}``). Call :func:`record_use` whenever a skill is
activated; staleness falls back to directory mtime when no usage is recorded.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from . import config as cfg
from .util import atomic_write, now_iso, read_text

# A skill unused for longer than this is considered stale.
STALE_AFTER_DAYS = 30
# Two skills whose descriptions match above this ratio are flagged as duplicates.
DUP_THRESHOLD = 0.82


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def _archive_dir() -> Path:
    return cfg.sub("skills_archive")


def _usage_path() -> Path:
    return cfg.skills_dir() / "usage.json"


# --------------------------------------------------------------------------- #
# usage tracking
# --------------------------------------------------------------------------- #
def _load_usage() -> dict[str, dict]:
    import json

    raw = read_text(_usage_path())
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save_usage(data: dict[str, dict]) -> None:
    import json

    atomic_write(_usage_path(), json.dumps(data, indent=2, sort_keys=True))


def record_use(name: str) -> None:
    """Record one activation of ``name``. Cheap; safe to call on every skill use."""
    data = _load_usage()
    entry = data.get(name, {"count": 0, "last_used": ""})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_used"] = now_iso()
    data[name] = entry
    _save_usage(data)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# scanning
# --------------------------------------------------------------------------- #
@dataclass
class SkillInfo:
    name: str
    dir: Path
    description: str = ""
    malformed: str = ""              # reason string, empty if well-formed
    count: int = 0
    last_used: str = ""              # iso, "" if never
    age_days: float = 0.0            # days since last_used (or dir mtime)


def _frontmatter(raw: str) -> dict | None:
    """Parse SKILL.md YAML frontmatter; None if absent/unparseable."""
    if not raw.startswith("---"):
        return None
    end = raw.find("\n---", 3)
    if end == -1:
        return None
    try:
        data = yaml.safe_load(raw[3:end].strip())
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _scan(now: datetime | None = None) -> list[SkillInfo]:
    now = now or datetime.now(timezone.utc)
    usage = _load_usage()
    root = cfg.skills_dir()
    out: list[SkillInfo] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        md = d / "SKILL.md"
        info = SkillInfo(name=d.name, dir=d)
        if not md.exists():
            info.malformed = "no SKILL.md"
        else:
            fm = _frontmatter(read_text(md))
            if fm is None:
                info.malformed = "missing or invalid YAML frontmatter"
            else:
                info.name = (fm.get("name") or d.name).strip()
                info.description = (fm.get("description") or "").strip()
                if not info.description:
                    info.malformed = "no description in frontmatter"

        u = usage.get(info.name) or usage.get(d.name) or {}
        info.count = int(u.get("count", 0))
        info.last_used = u.get("last_used", "")
        ref = _parse_iso(info.last_used)
        if ref is None:
            ref = datetime.fromtimestamp(md.stat().st_mtime if md.exists()
                                         else d.stat().st_mtime, tz=timezone.utc)
        info.age_days = round((now - ref).total_seconds() / 86400, 1)
        out.append(info)
    return out


def _find_duplicates(skills: list[SkillInfo]) -> list[dict]:
    """Pairs of skills whose descriptions are highly similar."""
    pairs: list[dict] = []
    valid = [s for s in skills if s.description and not s.malformed]
    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            a, b = valid[i], valid[j]
            ratio = SequenceMatcher(None, a.description.lower(), b.description.lower()).ratio()
            if ratio >= DUP_THRESHOLD:
                pairs.append({"a": a.name, "b": b.name, "similarity": round(ratio, 3)})
    return sorted(pairs, key=lambda p: -p["similarity"])


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def review(stale_after_days: int = STALE_AFTER_DAYS) -> dict:
    """Scan all personal skills and report maintenance findings.

    Returns ``{total, stale, duplicates, malformed}`` where ``stale`` and
    ``malformed`` are lists of dicts and ``duplicates`` is a list of pairs.
    """
    skills = _scan()
    stale = [
        {"name": s.name, "age_days": s.age_days, "count": s.count}
        for s in skills
        if not s.malformed and s.age_days >= stale_after_days
    ]
    malformed = [{"name": s.name, "reason": s.malformed} for s in skills if s.malformed]
    return {
        "total": len(skills),
        "stale": sorted(stale, key=lambda x: -x["age_days"]),
        "duplicates": _find_duplicates(skills),
        "malformed": malformed,
    }


ARCHIVE_AFTER_DAYS = 90


def prune(dry_run: bool = True, stale_after_days: int = STALE_AFTER_DAYS) -> list[str]:
    """Suggest (or archive) stale skills — ONLY agent-created, non-pinned, non-protected
    ones (bundled, hub-installed, user, and pinned skills are never touched).

    With ``dry_run`` (default) returns the names that *would* be archived; with
    ``dry_run=False`` it archives them and returns the names actually archived.
    """
    from . import provenance
    candidates = [
        s.name for s in _scan()
        if not s.malformed and s.age_days >= stale_after_days and provenance.curatable(s.name)
    ]
    if dry_run:
        return candidates
    archived: list[str] = []
    for name in candidates:
        if archive(name):
            archived.append(name)
    return archived


def apply_transitions(dry_run: bool = True, stale_after_days: int = STALE_AFTER_DAYS,
                      archive_after_days: int = ARCHIVE_AFTER_DAYS) -> dict[str, list[str]]:
    """Walk curatable skills and classify by the lifecycle clock: active → stale → archived.
    With ``dry_run=False`` the archive-eligible ones are archived (never deleted — archive is
    recoverable). Pinned/protected/user skills bypass entirely."""
    from . import provenance
    stale, to_archive = [], []
    for s in _scan():
        if s.malformed or not provenance.curatable(s.name):
            continue
        if s.age_days >= archive_after_days:
            to_archive.append(s.name)
        elif s.age_days >= stale_after_days:
            stale.append(s.name)
    archived: list[str] = []
    if not dry_run:
        consolidated, pruned = _classify_removed(to_archive)
        for name in to_archive:
            if archive(name):
                archived.append(name)
        return {"stale": stale, "archived": archived,
                "consolidated": consolidated, "pruned": pruned}
    return {"stale": stale, "to_archive": to_archive}


def _classify_removed(names: list[str]) -> tuple[list[str], list[str]]:
    """Split removed skills into *consolidated* (a near-duplicate survives, so the content
    lives on elsewhere) vs *pruned* (genuinely stale, no overlap)."""
    dups = {d["name"] for d in _find_duplicates(_scan())}
    consolidated = [n for n in names if n in dups]
    pruned = [n for n in names if n not in dups]
    return consolidated, pruned


def pin(name: str, pinned: bool = True) -> None:
    """Pin a skill so the curator never auto-archives it (it can still be improved)."""
    from . import provenance
    provenance.pin(name, pinned)


def archive(name: str) -> bool:
    """Move a skill dir from skills/ to skills_archive/. Returns False if absent."""
    src = cfg.skills_dir() / name
    if not src.is_dir():
        return False
    dest = _archive_dir() / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(src), str(dest))
    return True


def restore(name: str) -> bool:
    """Move a skill dir back from skills_archive/ to skills/. False if absent."""
    src = _archive_dir() / name
    if not src.is_dir():
        return False
    dest = cfg.skills_dir() / name
    if dest.exists():
        return False  # refuse to clobber a live skill
    shutil.move(str(src), str(dest))
    return True


def archived() -> list[str]:
    """Names of skills currently in the archive."""
    root = _archive_dir()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


# --------------------------------------------------------------------------- #
# run-gating state, backups/rollback, periodic run
# --------------------------------------------------------------------------- #
def _state_path() -> Path:
    return cfg.sub("curator", "state.json")


def _load_state() -> dict:
    import json
    raw = read_text(_state_path())
    if not raw.strip():
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save_state(data: dict) -> None:
    import json
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    atomic_write(_state_path(), json.dumps(data, indent=2, sort_keys=True))


def _idle_hours(now: datetime | None = None) -> float:
    """Hours since the agent was last active. Proxy = newest of the session DB mtime and the
    most recent recorded skill use; large when the machine has been quiet."""
    now = now or datetime.now(timezone.utc)
    latest: datetime | None = None
    db = cfg.sessions_db()
    if db.exists():
        latest = datetime.fromtimestamp(db.stat().st_mtime, tz=timezone.utc)
    for u in _load_usage().values():
        dt = _parse_iso(u.get("last_used", ""))
        if dt and (latest is None or dt > latest):
            latest = dt
    if latest is None:
        return 1e9   # nothing on record -> treat as fully idle
    return max(0.0, (now - latest).total_seconds() / 3600)


def _backups_dir() -> Path:
    return cfg.sub("curator", "backups")


def backup(reason: str = "auto", keep: int = 5) -> Path | None:
    """Snapshot skills/ to curator/backups/<utc-iso>/skills.tar.gz; prune to ``keep``."""
    import json
    import tarfile
    src = cfg.skills_dir()
    if not src.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    dest = _backups_dir() / stamp
    dest.mkdir(parents=True, exist_ok=True)
    tar = dest / "skills.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        tf.add(src, arcname="skills")
    atomic_write(dest / "manifest.json",
                 json.dumps({"at": now_iso(), "reason": reason}, indent=2))
    _prune_backups(keep)
    return tar


def _prune_backups(keep: int) -> None:
    root = _backups_dir()
    if not root.exists():
        return
    snaps = sorted((p for p in root.iterdir() if p.is_dir()),
                   key=lambda p: p.name, reverse=True)
    for old in snaps[max(0, keep):]:
        shutil.rmtree(old, ignore_errors=True)


def list_backups() -> list[dict]:
    import json
    root = _backups_dir()
    if not root.exists():
        return []
    out: list[dict] = []
    for p in sorted((d for d in root.iterdir() if d.is_dir()),
                    key=lambda p: p.name, reverse=True):
        m: dict = {}
        try:
            m = json.loads(read_text(p / "manifest.json") or "{}")
        except json.JSONDecodeError:
            pass
        out.append({"id": p.name, "reason": m.get("reason", ""), "at": m.get("at", "")})
    return out


def rollback(snapshot_id: str | None = None) -> str | None:
    """Restore skills/ from a snapshot (newest if id omitted). Takes a pre-rollback snapshot
    first so the rollback itself is reversible. Returns the restored id, or None."""
    import tarfile
    snaps = list_backups()
    if not snaps:
        return None
    target = snapshot_id or snaps[0]["id"]
    tar = _backups_dir() / target / "skills.tar.gz"
    if not tar.exists():
        return None
    backup(reason=f"pre-rollback to {target}")   # so a mistaken rollback can be undone
    skills = cfg.skills_dir()
    if skills.exists():
        shutil.rmtree(skills, ignore_errors=True)
    skills.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar, "r:gz") as tf:
        tf.extractall(skills.parent)   # archive root is "skills/"
    return target


def _write_report(result: dict, stale_days: int, archive_days: int) -> Path:
    import json
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    d = cfg.logs_dir() / "curator" / stamp
    d.mkdir(parents=True, exist_ok=True)
    atomic_write(d / "run.json", json.dumps(result, indent=2, sort_keys=True))
    lines = [
        f"# Curator run {stamp}", "",
        f"- archived: {', '.join(result.get('archived', [])) or 'none'}",
        f"- consolidated (near-duplicate survives): {', '.join(result.get('consolidated', [])) or 'none'}",
        f"- pruned (stale, no overlap): {', '.join(result.get('pruned', [])) or 'none'}",
        f"- now stale (>{stale_days}d, watching): {', '.join(result.get('stale', [])) or 'none'}",
        f"- archive-after: {archive_days}d",
    ]
    atomic_write(d / "REPORT.md", "\n".join(lines) + "\n")
    return d


def run(config=None, *, dry_run: bool = False) -> dict:
    """Run a full curator pass: snapshot skills/, apply lifecycle transitions, write a report,
    and stamp ``last_run_at``. Returns the transition result (plus report path)."""
    stale_days, archive_days = STALE_AFTER_DAYS, ARCHIVE_AFTER_DAYS
    backup_keep, backup_enabled = 5, True
    if config is not None:
        stale_days = int(config.get("curator.stale_after_days", stale_days) or stale_days)
        archive_days = int(config.get("curator.archive_after_days", archive_days) or archive_days)
        backup_keep = int(config.get("curator.backup.keep", backup_keep) or backup_keep)
        backup_enabled = bool(config.get("curator.backup.enabled", True))
    if not dry_run and backup_enabled:
        backup(reason="pre-curator", keep=backup_keep)
    result = apply_transitions(dry_run=dry_run, stale_after_days=stale_days,
                               archive_after_days=archive_days)
    if not dry_run:
        result["report"] = str(_write_report(result, stale_days, archive_days))
        state = _load_state()
        state["last_run_at"] = now_iso()
        _save_state(state)
    return result


def maybe_run(config) -> dict | None:
    """Gated automatic run (Hermes-style): fires only if enabled, the configured interval has
    elapsed since the last run, and the agent has been idle long enough. On a brand-new install
    the first observation seeds the clock and defers the first real pass by one full interval."""
    if config is None or not bool(config.get("curator.enabled", True)):
        return None
    interval_h = float(config.get("curator.interval_hours", 168) or 168)
    min_idle_h = float(config.get("curator.min_idle_hours", 2) or 0)
    state = _load_state()
    now = datetime.now(timezone.utc)
    last = _parse_iso(state.get("last_run_at", ""))
    if last is None:
        # First time we've ever observed: seed the clock and defer one full interval.
        state["last_run_at"] = now_iso()
        _save_state(state)
        return None
    if (now - last).total_seconds() / 3600 < interval_h:
        return None
    if min_idle_h > 0 and _idle_hours(now) < min_idle_h:
        return None
    return run(config, dry_run=False)


def maybe_run_background(config) -> None:
    """Fire :func:`maybe_run` in a daemon thread so session start never blocks on it."""
    import threading

    def _go():
        try:
            maybe_run(config)
        except Exception:  # noqa: BLE001 — maintenance must never crash a session
            pass

    threading.Thread(target=_go, daemon=True).start()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_curator(args, config) -> int:
    """`aegis curator <status|review|prune|archive|restore>`."""
    action = getattr(args, "action", None) or "status"
    name = getattr(args, "name", None)

    if action == "status":
        skills = _scan()
        print(f"  {len(skills)} personal skill(s) in {cfg.skills_dir()}")
        for s in skills:
            tag = f"!{s.malformed}" if s.malformed else f"{s.count}x, {s.age_days}d idle"
            print(f"  {s.name:<28} {tag}")
        arc = archived()
        if arc:
            print(f"  archived: {', '.join(arc)}")
        return 0

    if action == "review":
        r = review()
        print(f"  scanned {r['total']} skill(s)")
        if r["malformed"]:
            print("  malformed:")
            for m in r["malformed"]:
                print(f"    {m['name']:<26} {m['reason']}")
        if r["duplicates"]:
            print("  possible duplicates:")
            for d in r["duplicates"]:
                print(f"    {d['a']} ~ {d['b']}  (similarity {d['similarity']})")
        if r["stale"]:
            print(f"  stale (>{STALE_AFTER_DAYS}d):")
            for s in r["stale"]:
                print(f"    {s['name']:<26} {s['age_days']}d idle, used {s['count']}x")
        if not (r["malformed"] or r["duplicates"] or r["stale"]):
            print("  no issues found.")
        return 0

    if action == "prune":
        apply = bool(getattr(args, "apply", False))
        result = prune(dry_run=not apply)
        if not result:
            print("  nothing to prune.")
        elif apply:
            print(f"  archived: {', '.join(result)}")
        else:
            print("  would archive (use --apply): " + ", ".join(result))
        return 0

    if action == "archive":
        if not name:
            print("error: usage: aegis curator archive <name>")
            return 1
        print(f"archived {name}" if archive(name) else f"skill '{name}' not found")
        return 0

    if action == "restore":
        if not name:
            print("error: usage: aegis curator restore <name>")
            return 1
        ok = restore(name)
        if ok:
            print(f"restored {name}")
        else:
            print(f"cannot restore '{name}' (not archived, or a live skill exists)")
        return 0 if ok else 1

    if action == "transitions":
        apply = bool(getattr(args, "apply", False))
        r = apply_transitions(dry_run=not apply)
        if apply:
            print(f"  archived: {', '.join(r['archived']) or 'none'}")
            if r.get("consolidated"):
                print(f"  consolidated (near-duplicate survives): {', '.join(r['consolidated'])}")
            if r.get("pruned"):
                print(f"  pruned (stale, no overlap): {', '.join(r['pruned'])}")
            print(f"  now stale (watching): {', '.join(r['stale']) or 'none'}")
        else:
            print(f"  stale (>{STALE_AFTER_DAYS}d): {', '.join(r['stale']) or 'none'}")
            print(f"  would archive (>{ARCHIVE_AFTER_DAYS}d, use --apply): "
                  + (', '.join(r['to_archive']) or 'none'))
        return 0

    if action == "pin":
        if not name:
            print("error: usage: aegis curator pin <name>")
            return 1
        pin(name, True)
        print(f"pinned {name} (curator will never auto-archive it)")
        return 0

    if action == "unpin":
        if not name:
            print("error: usage: aegis curator unpin <name>")
            return 1
        pin(name, False)
        print(f"unpinned {name}")
        return 0

    if action == "run":
        dry = bool(getattr(args, "dry_run", False))
        r = run(config, dry_run=dry)
        if dry:
            print(f"  would archive (>{config.get('curator.archive_after_days', ARCHIVE_AFTER_DAYS)}d): "
                  + (', '.join(r.get('to_archive', [])) or 'none'))
            print(f"  now stale: {', '.join(r.get('stale', [])) or 'none'}")
        else:
            print(f"  archived: {', '.join(r.get('archived', [])) or 'none'}")
            print(f"  report: {r.get('report', '')}")
        return 0

    if action == "backup":
        path = backup(reason=getattr(args, "name", None) or "manual",
                      keep=int(config.get("curator.backup.keep", 5) or 5))
        print(f"snapshot: {path}" if path else "no skills/ to back up")
        return 0

    if action == "rollback":
        snaps = list_backups()
        if getattr(args, "list", False) or (not snaps):
            if not snaps:
                print("  no snapshots.")
            for s in snaps:
                print(f"  {s['id']}  {s['reason']}  {s['at']}")
            return 0
        restored = rollback(getattr(args, "id", None))
        print(f"restored skills/ from {restored}" if restored else "rollback failed (no matching snapshot)")
        return 0 if restored else 1

    if action == "list-archived":
        arc = archived()
        print("  " + (", ".join(arc) if arc else "none archived"))
        return 0

    print(f"error: unknown action '{action}'")
    return 1
