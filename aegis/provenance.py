"""Skill write-origin provenance — the Hermes curator invariant.

Distinguishes **agent-created** skills (written by the agent / background review, and
therefore eligible for automatic curation) from **user-created**, **bundled**, and
**hub-installed** skills (protected — never auto-edited or auto-archived). Also tracks
**pinned** skills, which bypass auto-archival but may still be improved.

State lives in ``~/.aegis/skills/provenance.json``: ``{name: {origin, pinned, at}}``.
"""

from __future__ import annotations

import contextlib
import contextvars
import json

from . import config as cfg
from .util import atomic_write, now_iso, read_text

# Active write-origin for the current execution context. The background review thread
# enters origin_scope("agent") so every skill it writes is tagged curatable.
_origin = contextvars.ContextVar("skill_origin", default="user")


def current_origin() -> str:
    return _origin.get()


@contextlib.contextmanager
def origin_scope(origin: str):
    token = _origin.set(origin)
    try:
        yield
    finally:
        _origin.reset(token)


def _path():
    return cfg.skills_dir() / "provenance.json"


def _load() -> dict[str, dict]:
    raw = read_text(_path())
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def _save(data: dict[str, dict]) -> None:
    atomic_write(_path(), json.dumps(data, indent=2, sort_keys=True))


def record(name: str, origin: str) -> None:
    """origin: 'agent' (curatable) or 'user' (protected)."""
    data = _load()
    entry = data.setdefault(name, {})
    entry.setdefault("at", now_iso())
    entry["origin"] = origin
    _save(data)


def is_agent_created(name: str) -> bool:
    return _load().get(name, {}).get("origin") == "agent"


def pin(name: str, pinned: bool = True) -> None:
    data = _load()
    data.setdefault(name, {"origin": "user", "at": now_iso()})["pinned"] = pinned
    _save(data)


def is_pinned(name: str) -> bool:
    return bool(_load().get(name, {}).get("pinned"))


def is_bundled(name: str) -> bool:
    from .skills import _bundled_dir
    return (_bundled_dir() / name / "SKILL.md").exists()


def is_hub_installed(name: str) -> bool:
    """Skills installed via the marketplace (~/.aegis/skills/.lock.json)."""
    raw = read_text(cfg.skills_dir() / ".lock.json")
    try:
        return name in (json.loads(raw) if raw.strip() else {})
    except json.JSONDecodeError:
        return False


def is_protected(name: str) -> bool:
    """Bundled or hub-installed skills are never auto-edited/archived."""
    return is_bundled(name) or is_hub_installed(name)


def curatable(name: str) -> bool:
    """Only agent-created, non-pinned, non-protected skills may be auto-curated."""
    return is_agent_created(name) and not is_pinned(name) and not is_protected(name)
