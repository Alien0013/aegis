"""Durable write approval gate for memory and skill mutations.

The reference write-approval contract keeps memory and skill self-edits behind an optional pending-store gate.
When enabled, foreground memory writes may be approved inline, but skill writes
and background-origin writes are staged for later human review instead of being
applied directly.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable
import uuid

from . import config as cfg
from .util import atomic_write, ensure_dir, now_iso, read_text, truncate

MEMORY = "memory"
SKILLS = "skills"
CONFIG_KEY = "write_approval"

ALLOW = "allow"
BLOCKED = "blocked"
STAGE = "stage"

_SUBSYSTEMS = {MEMORY, SKILLS}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "approve", "ask", "review"}
_FALSE_VALUES = {"", "0", "false", "no", "off", "disabled", "deny", "none"}
_BACKGROUND_ORIGINS = {"agent", "background", "background_review", "background-review"}


@dataclass(frozen=True)
class GateDecision:
    action: str
    reason: str = ""
    pending_id: str = ""
    record: dict[str, Any] | None = None

    @property
    def allowed(self) -> bool:
        return self.action == ALLOW

    @property
    def allow(self) -> bool:
        return self.allowed

    @property
    def staged(self) -> bool:
        return self.action == STAGE

    @property
    def stage(self) -> bool:
        return self.staged

    @property
    def blocked(self) -> bool:
        return self.action == BLOCKED

    @property
    def message(self) -> str:
        return self.reason


def _config(config: Any = None) -> Any:
    return config if config is not None else cfg.Config.load()


def _get_config(config: Any, dotted: str, default: Any = None) -> Any:
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(dotted, default)
    cur: Any = config
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _normalize_subsystem(subsystem: str) -> str:
    value = str(subsystem or "").strip().lower()
    if value not in _SUBSYSTEMS:
        raise ValueError(f"unsupported write approval subsystem: {subsystem!r}")
    return value


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return False


def write_approval_enabled(subsystem: str, config: Any = None) -> bool:
    """Whether durable writes for one subsystem must pass the approval gate."""
    subsystem = _normalize_subsystem(subsystem)
    config = _config(config)
    return _truthy(_get_config(config, f"{subsystem}.{CONFIG_KEY}", False))


def pending_dir(subsystem: str, config: Any = None) -> Path:
    subsystem = _normalize_subsystem(subsystem)
    return ensure_dir(cfg.get_home() / "pending" / subsystem)


def _pending_path(subsystem: str, pending_id: str, config: Any = None) -> Path:
    pending_id = str(pending_id or "").strip()
    if not pending_id or "/" in pending_id or "\\" in pending_id or pending_id in {".", ".."}:
        raise ValueError("invalid pending write id")
    return pending_dir(subsystem, config) / f"{pending_id}.json"


def _new_pending_id(subsystem: str) -> str:
    return f"{subsystem}-{uuid.uuid4().hex[:16]}"


def stage_write(
    subsystem: str,
    payload: dict[str, Any],
    summary: str,
    *,
    origin: str = "foreground",
    config: Any = None,
) -> dict[str, Any]:
    """Persist a pending durable write as one atomic JSON record."""
    subsystem = _normalize_subsystem(subsystem)
    pending_id = _new_pending_id(subsystem)
    record = {
        "id": pending_id,
        "subsystem": subsystem,
        "action": str(payload.get("action") or ""),
        "created_at": now_iso(),
        "origin": str(origin or "foreground"),
        "summary": str(summary or "").strip(),
        "payload": dict(payload or {}),
    }
    atomic_write(_pending_path(subsystem, pending_id, config), json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def get_pending(subsystem: str, pending_id: str, config: Any = None) -> dict[str, Any] | None:
    path = _pending_path(subsystem, pending_id, config)
    raw = read_text(path)
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def list_pending(subsystem: str, config: Any = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(pending_dir(subsystem, config).glob("*.json")):
        raw = read_text(path)
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return sorted(records, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))


def discard_pending(subsystem: str, pending_id: str, config: Any = None) -> bool:
    path = _pending_path(subsystem, pending_id, config)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def pending_count(subsystem: str, config: Any = None) -> int:
    return len(list_pending(subsystem, config))


def _normalize_origin(origin: str | None, default: str = "foreground") -> str:
    text = str(origin or "").strip().lower().replace(" ", "_")
    if not text:
        return default
    if text in _BACKGROUND_ORIGINS:
        return "background_review"
    if text in {"user", "foreground", "interactive"}:
        return "foreground"
    return text


def current_origin(default: str = "foreground") -> str:
    """Return the active durable-write origin, normalized for gate decisions."""
    fallback = _normalize_origin(default)
    try:
        from . import provenance

        return _normalize_origin(provenance.current_origin(), fallback)
    except Exception:  # noqa: BLE001
        return fallback


def is_background(origin: str | None = None) -> bool:
    clean_origin = current_origin() if origin is None else _normalize_origin(origin)
    return clean_origin != "foreground"


def _default_origin(origin: str | None) -> str:
    if origin is None:
        return current_origin()
    return _normalize_origin(origin)


def evaluate_gate(
    subsystem: str,
    *,
    inline_summary: str = "",
    inline_detail: str = "",
    config: Any = None,
    interactive_approver: Callable[[str], bool] | None = None,
    origin: str | None = None,
) -> GateDecision:
    """Return the reference-style gate decision for a durable write attempt."""
    subsystem = _normalize_subsystem(subsystem)
    config = _config(config)
    if not write_approval_enabled(subsystem, config):
        return GateDecision(ALLOW, "write approval is disabled")

    clean_origin = _default_origin(origin)
    if clean_origin != "foreground":
        return GateDecision(STAGE, f"{subsystem} write from {clean_origin} must be staged")

    if subsystem == SKILLS:
        return GateDecision(STAGE, "skill writes must be staged for review")

    if interactive_approver is None:
        return GateDecision(STAGE, "no inline approver is available")

    prompt = inline_summary.strip() or f"Approve {subsystem} write?"
    detail = inline_detail.strip()
    if detail:
        prompt = f"{prompt}\n\n{detail}"
    try:
        approved = bool(interactive_approver(prompt))
    except Exception:  # noqa: BLE001
        approved = False
    if approved:
        return GateDecision(ALLOW, "approved inline")
    return GateDecision(BLOCKED, "denied by inline approver")


def memory_summary(payload: dict[str, Any]) -> str:
    action = str(payload.get("action") or "write")
    target = str(payload.get("target") or MEMORY)
    if action == "add":
        return f"Add {target} memory: {truncate(str(payload.get('content') or ''), 120)}"
    if action == "replace":
        return (
            f"Replace {target} memory matching "
            f"{truncate(str(payload.get('old_text') or ''), 80)}"
        )
    if action == "remove":
        return f"Remove {target} memory matching {truncate(str(payload.get('old_text') or ''), 80)}"
    return f"{action} {target} memory"


def skill_gist(
    payload_or_action: dict[str, Any] | str,
    name: str = "",
    *,
    content: str = "",
    file_path: str = "",
    old_string: str = "",
    new_string: str = "",
) -> str:
    """Build a compact review gist for pending skill writes.

    Accepts both the AEGIS payload dict shape and the reference helper signature:
    ``skill_gist(action, name, content=..., file_path=..., ...)``.
    """
    if isinstance(payload_or_action, dict):
        payload = dict(payload_or_action or {})
        action = str(payload.get("action") or "write")
        name = str(payload.get("name") or name or "(unnamed)")
        content = str(payload.get("content") or payload.get("body") or content or "")
        file_path = str(payload.get("file_path") or file_path or "")
        old_string = str(payload.get("old_string") or old_string or "")
        new_string = str(payload.get("new_string") or new_string or "")
        description = str(payload.get("description") or "")
    else:
        action = str(payload_or_action or "write")
        name = str(name or "(unnamed)")
        description = ""

    if action in {"create", "edit"} and content:
        desc = _frontmatter_description(content)
        size = f"{len(content) // 1024 + 1} KB" if len(content) >= 1024 else f"{len(content)} chars"
        verb = "create" if action == "create" else "rewrite"
        if desc:
            return f"{verb} '{name}' - {truncate(desc, 120)} ({size})"
        return f"{verb} '{name}' ({size})"
    if action == "create":
        if description:
            return f"create '{name}' - {truncate(description, 120)}"
        return f"create '{name}'"
    if action == "patch":
        target = file_path or "SKILL.md"
        removed = old_string.count("\n") + 1 if old_string else 0
        added = new_string.count("\n") + 1 if new_string else 0
        return f"patch '{name}' {target} (+{added}/-{removed} lines)"
    if action == "write_file":
        return f"write {file_path or 'SKILL.md'} in '{name}'"
    if action == "remove_file":
        return f"remove {file_path or 'SKILL.md'} from '{name}'"
    if action == "delete":
        return f"delete skill '{name}'"
    if action == "consolidate":
        into = ""
        if isinstance(payload_or_action, dict):
            into = str(payload_or_action.get("into") or "")
        return f"consolidate '{name}' into '{into}'"
    return f"{action} '{name}'"


def _frontmatter_description(content: str) -> str:
    for line in str(content or "").splitlines():
        if line.strip().lower().startswith("description:"):
            return line.split(":", 1)[1].strip().strip("'\"")[:140]
    return ""


def skill_pending_diff(record: dict[str, Any], skills_loader: Any = None) -> str:
    """Return a compact review note for one pending skill mutation."""
    payload = dict(record.get("payload") or {})
    action = str(payload.get("action") or "")
    name = str(payload.get("name") or "")
    lines = [str(record.get("summary") or skill_gist(payload))]
    if not name:
        return "\n".join(lines)

    skill = None
    if skills_loader is not None:
        try:
            skill = skills_loader.discover().get(name)
        except Exception:  # noqa: BLE001
            skill = None
    if skill is None:
        path = cfg.skills_dir() / name / "SKILL.md"
        current = read_text(path)
    else:
        try:
            current = skill.full_body()
        except Exception:  # noqa: BLE001
            current = read_text(getattr(skill, "path", cfg.skills_dir() / name / "SKILL.md"))
    if current:
        lines.append("Current:")
        lines.append(truncate(current, 1200))
    if action in {"create", "write_file"}:
        intended = str(payload.get("content") or payload.get("body") or "")
        if intended:
            lines.append("Proposed:")
            lines.append(truncate(intended, 1200))
    elif action == "patch":
        lines.append(f"Old: {truncate(str(payload.get('old_string') or ''), 400)}")
        lines.append(f"New: {truncate(str(payload.get('new_string') or ''), 400)}")
    return "\n".join(lines)
