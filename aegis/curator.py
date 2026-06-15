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
from .skills import validate_skill_name
from .util import atomic_write, now_iso, read_text

# A skill unused for longer than this is considered stale.
STALE_AFTER_DAYS = 30
# Two skills whose descriptions match above this ratio are flagged as duplicates.
DUP_THRESHOLD = 0.82

# Lifecycle states (AEGIS-parity state machine): a skill is ACTIVE until it goes
# unused past stale_after_days (-> STALE), then archived past archive_after_days
# (-> ARCHIVED); using it again reactivates it. Pinned skills never transition.
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"


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


def _set_state(name: str, state: str) -> None:
    """Persist a skill's lifecycle state in usage.json so it's visible/queryable
    between curator runs (the state machine is stored, not recomputed each time)."""
    data = _load_usage()
    entry = data.get(name, {"count": 0, "last_used": ""})
    entry["state"] = state
    data[name] = entry
    _save_usage(data)


def skill_state(name: str) -> str:
    """Current persisted lifecycle state for ``name`` (defaults to active)."""
    return (_load_usage().get(name) or {}).get("state", STATE_ACTIVE)


def _seed_record(name: str) -> None:
    """Persist a baseline record anchoring a skill's lifecycle clock to now — called
    the first time the curator sees a skill with no usage record, so its stale/archive
    clock measures non-use from first-sight, not from an old directory mtime."""
    data = _load_usage()
    if name in data:
        return
    data[name] = {"count": 0, "last_used": "", "created_at": now_iso(), "state": STATE_ACTIVE}
    _save_usage(data)


# --------------------------------------------------------------------------- #
# suppression list — keeps a curator-archived skill archived across re-seeds
# (e.g. a bundled skill re-shipped by an update). One name per line in
# skills/.curator_suppressed. Cleared by an explicit restore.
# --------------------------------------------------------------------------- #
def _suppressed_path() -> Path:
    return cfg.skills_dir() / ".curator_suppressed"


def read_suppressed() -> set[str]:
    raw = read_text(_suppressed_path())
    return {ln.strip() for ln in raw.splitlines() if ln.strip()} if raw else set()


def _write_suppressed(names: set[str]) -> None:
    atomic_write(_suppressed_path(), "\n".join(sorted(names)) + "\n" if names else "")


def add_suppressed(name: str) -> None:
    names = read_suppressed()
    if name not in names:
        names.add(name)
        _write_suppressed(names)


def remove_suppressed(name: str) -> None:
    names = read_suppressed()
    if name in names:
        names.discard(name)
        _write_suppressed(names)


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
    count: int = 0                   # use_count (loaded into a prompt)
    view_count: int = 0              # skill_manage view
    patch_count: int = 0             # skill_manage patch/edit/write_file/remove_file
    last_used: str = ""              # iso, "" if never
    created_at: str = ""             # iso, first-sight anchor for the lifecycle clock
    age_days: float = 0.0            # days since last_used (or created_at, or dir mtime)
    state: str = STATE_ACTIVE        # persisted lifecycle state


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
        info.view_count = int(u.get("view_count", 0))
        info.patch_count = int(u.get("patch_count", 0))
        info.last_used = u.get("last_used", "")
        info.created_at = u.get("created_at", "")
        info.state = u.get("state", STATE_ACTIVE)
        # Anchor the inactivity clock to: last real use → first-sight created_at →
        # (only as a last resort) the dir mtime. The created_at anchor stops a
        # never-used-but-valid skill from being archived on an old directory mtime.
        ref = _parse_iso(info.last_used) or _parse_iso(info.created_at)
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
                      archive_after_days: int = ARCHIVE_AFTER_DAYS) -> dict:
    """Walk curatable skills and move them through the lifecycle state machine:
    ``active → stale → archived`` by the inactivity clock, ``stale → active`` when a
    skill is used again (reactivation). Pinned/protected/user skills bypass entirely.

    With ``dry_run=False`` the new state is persisted to usage.json and archive-eligible
    skills are archived (never deleted — archive is recoverable). Returns the per-name
    lists plus a structured ``counts`` dict (checked/marked_stale/reactivated/archived).
    """
    from . import provenance
    stale, to_archive, reactivated, resuppressed = [], [], [], []
    counts = {"checked": 0, "marked_stale": 0, "reactivated": 0, "archived": 0,
              "seeded": 0, "resuppressed": 0}
    usage = _load_usage()
    suppressed = read_suppressed()
    for s in _scan():
        if s.malformed or not provenance.curatable(s.name):
            continue
        counts["checked"] += 1
        # A skill the curator previously archived has reappeared as live (e.g. re-seeded
        # by an update): keep it archived unless the user explicitly restored it.
        if s.name in suppressed:
            if not dry_run and archive(s.name):
                resuppressed.append(s.name)
                counts["resuppressed"] += 1
            continue
        # First time the curator sees this skill with no usage record at all: anchor
        # its clock to now and defer one full pass, so an old directory mtime can't
        # archive a skill the curator has only just noticed (AEGIS seed_record_if_missing).
        if s.name not in usage and not s.last_used:
            if not dry_run:
                _seed_record(s.name)
                counts["seeded"] += 1
            continue
        if s.age_days >= archive_after_days:
            to_archive.append(s.name)
        elif s.age_days >= stale_after_days:
            stale.append(s.name)
            if not dry_run and s.state != STATE_STALE:
                _set_state(s.name, STATE_STALE)
                counts["marked_stale"] += 1
        elif s.state == STATE_STALE:
            # Used again after being marked stale — reactivate.
            reactivated.append(s.name)
            if not dry_run:
                _set_state(s.name, STATE_ACTIVE)
                counts["reactivated"] += 1
    archived: list[str] = []
    if not dry_run:
        consolidated, pruned = _classify_removed(to_archive)
        for name in to_archive:
            if archive(name):
                _set_state(name, STATE_ARCHIVED)
                add_suppressed(name)         # stay archived across future re-seeds
                archived.append(name)
        counts["archived"] = len(archived)
        return {"stale": stale, "archived": archived, "reactivated": reactivated,
                "consolidated": consolidated, "pruned": pruned,
                "resuppressed": resuppressed, "counts": counts}
    return {"stale": stale, "to_archive": to_archive, "reactivated": reactivated,
            "counts": counts}


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
    name = validate_skill_name(name)
    provenance.pin(name, pinned)


def archive(name: str) -> bool:
    """Move a skill dir from skills/ to skills_archive/. Returns False if absent."""
    try:
        name = validate_skill_name(name)
    except ValueError:
        return False
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
    try:
        name = validate_skill_name(name)
    except ValueError:
        return False
    src = _archive_dir() / name
    if not src.is_dir():
        return False
    dest = cfg.skills_dir() / name
    if dest.exists():
        return False  # refuse to clobber a live skill
    shutil.move(str(src), str(dest))
    remove_suppressed(name)              # an explicit restore overrides suppression
    return True


def consolidation_candidates() -> list[dict]:
    """Near-duplicate skill pairs worth merging, oriented from→into: the less-used
    (or newer) skill folds into the more-established one. Only curatable skills."""
    from . import provenance
    by_name = {s.name: s for s in _scan()}
    out: list[dict] = []
    for pair in _find_duplicates(list(by_name.values())):
        a, b = by_name.get(pair["a"]), by_name.get(pair["b"])
        if a is None or b is None:
            continue
        if not (provenance.curatable(a.name) or provenance.curatable(b.name)):
            continue
        # Keep the more-used skill; fold the weaker one into it. Prefer to remove a
        # curatable skill (never fold away a pinned/user skill).
        weaker, stronger = (a, b) if (a.count, a.last_used) <= (b.count, b.last_used) else (b, a)
        if not provenance.curatable(weaker.name):
            weaker, stronger = stronger, weaker
        if not provenance.curatable(weaker.name):
            continue                                  # both protected — skip
        out.append({"from": weaker.name, "into": stronger.name,
                    "similarity": pair["similarity"]})
    return out


def consolidate(from_name: str, into_name: str) -> bool:
    """Merge skill ``from_name`` into ``into_name``: copy its SKILL.md body into the
    survivor's ``references/`` directory so the detail is preserved, then
    archive ``from_name`` with a pointer back to the survivor. Returns False if either
    skill is missing or ``from_name`` isn't safe to remove."""
    from . import provenance
    try:
        from_name = validate_skill_name(from_name)
        into_name = validate_skill_name(into_name)
    except ValueError:
        return False
    if from_name == into_name or not provenance.curatable(from_name):
        return False
    src, dst = cfg.skills_dir() / from_name, cfg.skills_dir() / into_name
    if not (src.is_dir() and dst.is_dir()):
        return False
    body = read_text(src / "SKILL.md")
    refs = dst / "references"
    refs.mkdir(parents=True, exist_ok=True)
    atomic_write(refs / f"consolidated-{from_name}.md",
                 f"# Consolidated from '{from_name}'\n\n"
                 f"(folded in by the curator on {now_iso()})\n\n{body.strip()}\n")
    if not archive(from_name):
        return False
    _set_state(from_name, STATE_ARCHIVED)
    add_suppressed(from_name)            # a merged-away skill stays archived across re-seeds
    # Leave a pointer in the archived copy so a restore knows where its content went.
    atomic_write(_archive_dir() / from_name / ".consolidated_into", into_name + "\n")
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
        f"- llm review: {', '.join((result.get('llm_review') or {}).get('actions', [])) or '(none / not run)'}",
    ]
    atomic_write(d / "REPORT.md", "\n".join(lines) + "\n")
    return d


_CONSOLIDATION_PROMPT = (
    "You are the skill curator running an UMBRELLA-BUILDING consolidation pass — not a "
    "passive audit. The target shape is a few broad CLASS-LEVEL umbrella skills, each with a "
    "rich SKILL.md and a references/ directory, NOT a long flat list of narrow one-task skills. "
    "Cluster the skills below by the class of task they serve (match on what they DO, from their "
    "descriptions — not on exact names) and merge each cluster into one umbrella.\n\n"
    "Anti-laziness rules:\n"
    "  - DO NOT skip a merge because the skills have different usage counts — a rarely-used "
    "sibling still belongs under its umbrella.\n"
    "  - DO NOT reject consolidation on the grounds that 'each skill is slightly distinct'. "
    "Near-duplicate purpose is enough; the distinct detail becomes a subsection or a "
    "references/ file under the umbrella.\n"
    "  - Most passes over a cluttered library SHOULD merge something. A pass that does nothing "
    "is only right when the library is already a small set of clean umbrellas.\n\n"
    "Three ways to consolidate — pick the right one per cluster:\n"
    "  1. PROMOTE: one skill is already broad enough to be the umbrella — patch it to absorb the "
    "siblings' steps/pitfalls, then fold each sibling in with `skill_manage action=consolidate "
    "name=<sibling> into=<umbrella>` (files its SKILL.md under the umbrella's references/ and "
    "archives it).\n"
    "  2. MERGE INTO EXISTING: a suitable umbrella already exists — consolidate the cluster into it.\n"
    "  3. CREATE NEW UMBRELLA: no existing skill covers the class — `skill_manage action=create` a "
    "class-level umbrella (name must be class-level, never a PR number / error string / codename / "
    "one-off task), then consolidate the siblings into it.\n\n"
    "Package integrity: if a skill has references/, scripts/, templates/, or relative links, the "
    "`consolidate` action preserves them under the survivor's references/ — never flatten only "
    "SKILL.md into another skill by hand.\n"
    "Iterate: after one cluster is merged, scan the remaining skills for the NEXT umbrella "
    "opportunity. Don't stop after one.\n"
    "Do NOT touch bundled, hub-installed, pinned, or user-created skills. When the library is a "
    "clean set of umbrellas, say so and stop."
)


def _curator_registry():
    """A tool registry restricted to skill inspection + management."""
    from .tools.registry import ToolRegistry, default_registry
    reg = ToolRegistry()
    for t in default_registry().all():
        if t.name in ("skill", "skill_manage"):
            reg.register(t)
    return reg


def _curatable_summaries() -> list[str]:
    from . import provenance
    out: list[str] = []
    for s in _scan():
        if s.malformed or not provenance.curatable(s.name):
            continue
        out.append(f"- {s.name}: {s.description[:160]}  (used {s.count}x, {s.age_days}d idle)")
    return out


def llm_review(config, *, dry_run: bool = False, max_iterations: int = 8) -> dict:
    """Phase-2 curator: fork an aux-model agent restricted to skill tools that
    reads the agent-created skills and consolidates/patches/archives them. No-op (never raises)
    when there are no curatable skills or no provider is available."""
    summaries = _curatable_summaries()
    if not summaries:
        return {"ran": False, "reason": "no curatable skills"}
    if dry_run:
        return {"ran": False, "reason": "dry-run", "candidates": [s[2:].split(":", 1)[0] for s in summaries]}
    try:
        from . import provenance
        from .agent.agent import Agent
        from .providers.fallback import build_with_fallbacks
        from .providers.registry import build_aux_provider
        from .session import Session
        from .surface import SurfaceRunner

        try:
            main = build_with_fallbacks(config)
        except Exception:  # noqa: BLE001
            main = None
        provider = build_aux_provider(config, purpose="curator", fallback_provider=main)
    except Exception as e:  # noqa: BLE001 — maintenance must never crash
        return {"ran": False, "reason": f"no provider: {type(e).__name__}"}

    child = Agent(config=config, provider=provider, session=Session.create(title="[curator]"),
                  registry=_curator_registry())
    child._no_review = True
    child.tool_context.approver = lambda *a, **k: True   # autonomous maintenance, never blocks
    child.budget.max_iterations = max_iterations
    actions: list[str] = []
    consolidations: list[dict] = []
    pending: dict = {}

    def _capture(ev):
        # Record a structured consolidations block: pair the consolidate
        # call's args (from tool_start) with its success (from tool_result) so downstream
        # tooling can distinguish a merge from a plain archive.
        if ev.get("type") == "tool_start" and ev.get("name") == "skill_manage":
            args = ev.get("args") or {}
            if args.get("action") == "consolidate" and args.get("name") and args.get("into"):
                pending.clear()
                pending.update({"from": args["name"], "into": args["into"]})
        elif ev.get("type") == "tool_result" and ev.get("name") == "skill_manage":
            actions.append(ev.get("summary", "skill_manage"))
            if pending and not ev.get("is_error"):
                consolidations.append(dict(pending))
            pending.clear()

    prompt = _CONSOLIDATION_PROMPT + "\n\nAGENT-CREATED SKILLS:\n" + "\n".join(summaries)
    try:
        with provenance.origin_scope("agent"):
            SurfaceRunner(config, cwd=child.cwd, include_mcp=False, reuse_agents=False).run_prompt(
                prompt, session=child.session, agent=child, surface="curator",
                title="curator review", meta={"curator": True}, on_event=_capture,
            )
    except Exception as e:  # noqa: BLE001
        return {"ran": True, "actions": actions, "consolidations": consolidations,
                "error": f"{type(e).__name__}: {e}"}
    return {"ran": True, "actions": actions, "consolidations": consolidations}


def run(config=None, *, dry_run: bool = False) -> dict:
    """Run a full curator pass: snapshot skills/, apply lifecycle transitions, optionally run the
    aux-model consolidation review, write a report, and stamp ``last_run_at``."""
    stale_days, archive_days = STALE_AFTER_DAYS, ARCHIVE_AFTER_DAYS
    backup_keep, backup_enabled, do_llm = 5, True, True
    if config is not None:
        stale_days = int(config.get("curator.stale_after_days", stale_days) or stale_days)
        archive_days = int(config.get("curator.archive_after_days", archive_days) or archive_days)
        backup_keep = int(config.get("curator.backup.keep", backup_keep) or backup_keep)
        backup_enabled = bool(config.get("curator.backup.enabled", True))
        do_llm = bool(config.get("curator.llm_review", True))
    if not dry_run and backup_enabled:
        backup(reason="pre-curator", keep=backup_keep)
    result = apply_transitions(dry_run=dry_run, stale_after_days=stale_days,
                               archive_after_days=archive_days)
    # Whole-agent lifecycle maintenance: prune empty 'ghost' sessions (no user/assistant
    # turns) older than the retention window, so the session store doesn't accumulate cruft.
    if config is not None and bool(config.get("curator.prune_empty_sessions", True)):
        try:
            from .session import SessionStore
            retention = float(config.get("curator.session_retention_days", 7) or 0)
            result["pruned_sessions"] = SessionStore().prune_empty(
                older_than_days=retention, dry_run=dry_run)
        except Exception:  # noqa: BLE001 — maintenance must never crash
            pass
    # ...and retire spent cron jobs (fired one-shots, recurring jobs past max_runs).
    if config is not None and bool(config.get("curator.prune_spent_cron", True)):
        try:
            from .cron import CronStore
            result["pruned_cron"] = CronStore().prune_spent(dry_run=dry_run)
        except Exception:  # noqa: BLE001
            pass
    if not dry_run and do_llm and config is not None:
        if bool(config.get("curator.verify_with_evals", False)):
            # Verified self-improvement: keep the review's skill edits only if they don't
            # regress the benchmark score, else roll skills/ back (provider-dependent).
            try:
                from .self_improve import verified_curator_review
                result["llm_review"] = verified_curator_review(config)
            except Exception as e:  # noqa: BLE001 — maintenance must never crash
                result["llm_review"] = {"ran": False, "reason": f"verify error: {type(e).__name__}"}
        else:
            result["llm_review"] = llm_review(config, dry_run=False)
    if not dry_run:
        result["report"] = str(_write_report(result, stale_days, archive_days))
        state = _load_state()
        state["last_run_at"] = now_iso()
        _save_state(state)
    return result


def maybe_run(config) -> dict | None:
    """Gated automatic run: fires only if enabled, the configured interval has
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
            tag = (f"!{s.malformed}" if s.malformed
                   else f"use {s.count} · view {s.view_count} · patch {s.patch_count} · {s.age_days}d idle")
            print(f"  {s.name:<28} {tag}")
        lru = [s for s in sorted(skills, key=lambda s: -s.age_days) if not s.malformed][:5]
        if lru:
            print("  least-recently-used (next to go stale): "
                  + ", ".join(f"{s.name} ({s.age_days}d)" for s in lru))
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
