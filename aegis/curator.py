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


def prune(dry_run: bool = True, stale_after_days: int = STALE_AFTER_DAYS) -> list[str]:
    """Suggest (or archive) stale skills.

    With ``dry_run`` (default) returns the names that *would* be archived. With
    ``dry_run=False`` it archives them and returns the names actually archived.
    """
    candidates = [
        s.name for s in _scan()
        if not s.malformed and s.age_days >= stale_after_days
    ]
    if dry_run:
        return candidates
    archived: list[str] = []
    for name in candidates:
        if archive(name):
            archived.append(name)
    return archived


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

    print(f"error: unknown action '{action}'")
    return 1
