"""Shared pending-write review/apply commands for memory and skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import write_approval as wa
from .config import Config
from .tools.base import ToolContext, ToolResult


_REVIEW_COMMANDS = {
    "pending",
    "approve",
    "apply",
    "reject",
    "deny",
    "drop",
    "diff",
    "approval",
    "mode",
}


def is_review_subcommand(command: str, *, subsystem: str) -> bool:
    command = str(command or "").strip().lower()
    if not command:
        return True
    if command == "diff":
        return subsystem == wa.SKILLS
    return command in _REVIEW_COMMANDS


def handle_pending_subcommand(
    subsystem: str,
    args: list[str],
    *,
    config: Config | None = None,
    memory_manager: Any = None,
    skills_loader: Any = None,
    set_mode_fn: Any = None,
) -> str | None:
    """Handle reference-style pending review commands for memory/skills."""
    config = config or Config.load()
    args = [str(arg) for arg in (args or []) if str(arg).strip()]
    if not args:
        return f"{_fmt_state(subsystem, config)}\n\n{_fmt_pending_list(subsystem, config)}"

    sub = args[0].lower()
    rest = args[1:]
    if sub == "pending":
        return _fmt_pending_list(subsystem, config)
    if sub in {"approve", "apply"}:
        return _approve(
            subsystem,
            rest,
            config=config,
            memory_manager=memory_manager,
            skills_loader=skills_loader,
        )
    if sub in {"reject", "deny", "drop"}:
        return _reject(subsystem, rest, config=config)
    if sub == "diff" and subsystem == wa.SKILLS:
        return _diff(rest, config=config, skills_loader=skills_loader)
    if sub in {"approval", "mode"}:
        return _set_approval(subsystem, rest, config=config, set_mode_fn=set_mode_fn)
    return None


def _fmt_state(subsystem: str, config: Config) -> str:
    state = "on" if wa.write_approval_enabled(subsystem, config=config) else "off"
    return f"{subsystem}.write_approval = {state}"


def _fmt_pending_list(subsystem: str, config: Config) -> str:
    records = wa.list_pending(subsystem, config=config)
    if not records:
        return f"No pending {subsystem} writes."
    lines = [f"Pending {subsystem} writes ({len(records)}):"]
    for record in records:
        origin = str(record.get("origin") or "foreground")
        tag = " [auto]" if origin == "background_review" else ""
        lines.append(f"  {record['id']}{tag}  {record.get('summary', '')}")
    lines.append("")
    lines.append(f"Apply: /{subsystem} approve <id>   Reject: /{subsystem} reject <id>")
    if subsystem == wa.SKILLS:
        lines.append("Review full diff: /skills diff <id>")
    return "\n".join(lines)


def _resolve_one(subsystem: str, rest: list[str]) -> tuple[str | None, str | None]:
    if not rest:
        return None, f"Usage: /{subsystem} approve|reject <id>  (or 'all')"
    return rest[0], None


def _approve(
    subsystem: str,
    rest: list[str],
    *,
    config: Config,
    memory_manager: Any = None,
    skills_loader: Any = None,
) -> str:
    target, err = _resolve_one(subsystem, rest)
    if err or target is None:
        return err or f"Usage: {subsystem} approve <id>"

    records = wa.list_pending(subsystem, config=config)
    if not records:
        return f"No pending {subsystem} writes."
    if target.lower() == "all":
        targets = list(records)
    else:
        record = wa.get_pending(subsystem, target, config=config)
        if not record:
            return f"No pending {subsystem} write with id '{target}'."
        targets = [record]

    applied = 0
    failed: list[str] = []
    for record in targets:
        ok, message = _apply_one(
            subsystem,
            record,
            config=config,
            memory_manager=memory_manager,
            skills_loader=skills_loader,
        )
        if ok:
            wa.discard_pending(subsystem, record["id"], config=config)
            applied += 1
        else:
            failed.append(f"{record['id']}: {message}")

    lines = [f"Approved {applied} {subsystem} write(s)."]
    if failed:
        lines.append("Failed:")
        lines.extend(f"  {item}" for item in failed)
    return "\n".join(lines)


def _apply_one(
    subsystem: str,
    record: dict[str, Any],
    *,
    config: Config,
    memory_manager: Any = None,
    skills_loader: Any = None,
) -> tuple[bool, str]:
    payload = dict(record.get("payload") or {})
    try:
        if subsystem == wa.MEMORY:
            result = _apply_memory_pending(payload, config=config, memory_manager=memory_manager)
        else:
            result = _apply_skill_pending(payload, config=config, skills_loader=skills_loader)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if _result_success(result):
        return True, getattr(result, "summary", "") or ""
    return False, _result_error(result)


def _apply_memory_pending(payload: dict[str, Any], *, config: Config, memory_manager: Any = None) -> ToolResult:
    if memory_manager is None:
        from .memory import MemoryManager

        memory_manager = MemoryManager(config, load_external=False)
    return memory_manager.handle_tool(payload, bypass_write_approval=True)


def _apply_skill_pending(payload: dict[str, Any], *, config: Config, skills_loader: Any = None) -> ToolResult:
    if skills_loader is None:
        from .skills import SkillsLoader

        skills_loader = SkillsLoader(config, cwd=Path.cwd())
    from .tools.skill_manage import apply_skill_pending

    ctx = ToolContext(cwd=Path.cwd(), config=config, skills=skills_loader)
    return apply_skill_pending(payload, ctx)


def _result_success(result: ToolResult) -> bool:
    if result.is_error:
        return False
    data = result.data
    if isinstance(data, dict) and data.get("success") is False:
        return False
    return True


def _result_error(result: ToolResult) -> str:
    data = result.data
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    return str(result.content or result.summary or "write failed")


def _reject(subsystem: str, rest: list[str], *, config: Config) -> str:
    target, err = _resolve_one(subsystem, rest)
    if err or target is None:
        return err or f"Usage: {subsystem} reject <id>"
    if target.lower() == "all":
        count = 0
        for record in wa.list_pending(subsystem, config=config):
            if wa.discard_pending(subsystem, record["id"], config=config):
                count += 1
        return f"Rejected {count} pending {subsystem} write(s)."
    if wa.discard_pending(subsystem, target, config=config):
        return f"Rejected pending {subsystem} write '{target}'."
    return f"No pending {subsystem} write with id '{target}'."


def _diff(rest: list[str], *, config: Config, skills_loader: Any = None) -> str:
    if not rest:
        return "Usage: /skills diff <id>"
    record = wa.get_pending(wa.SKILLS, rest[0], config=config)
    if not record:
        return f"No pending skill write with id '{rest[0]}'."
    header = f"# Pending skill write {record['id']}: {record.get('summary', '')}\n"
    return header + "\n" + wa.skill_pending_diff(record, skills_loader=skills_loader)


def _set_approval(subsystem: str, rest: list[str], *, config: Config, set_mode_fn: Any = None) -> str:
    if not rest or rest[0].lower() in {"status", "show"}:
        return f"{_fmt_state(subsystem, config)}\nSet with: /{subsystem} approval <on|off>"
    arg = rest[0].strip().lower()
    truthy = {"on", "true", "yes", "1", "enable", "enabled"}
    falsey = {"off", "false", "no", "0", "disable", "disabled"}
    if arg in truthy:
        enabled = True
    elif arg in falsey:
        enabled = False
    else:
        return f"Invalid value '{arg}'. Use: on or off."
    if set_mode_fn is not None:
        set_mode_fn(enabled)
    else:
        config.set(f"{subsystem}.write_approval", enabled)
    return f"{subsystem}.write_approval set to '{'on' if enabled else 'off'}'."
