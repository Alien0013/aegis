"""Hermes-style skill management tool backed by Aegis skills and curator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .. import config as cfg
from .. import curator, provenance
from ..util import atomic_write, read_text
from .base import Tool, ToolContext, ToolResult


MAX_SKILL_CONTENT_CHARS = 100_000
ALLOWED_SUPPORT_DIRS = {"assets", "references", "scripts", "templates"}
_ACTIONS = (
    "list", "view", "create", "patch", "write_file", "delete", "usage", "pin", "unpin", "report"
)


def _json_result(payload: dict[str, Any], display: str) -> ToolResult:
    return ToolResult.ok(
        json.dumps(payload, indent=2, sort_keys=True),
        display=display,
        data=payload,
    )


def _json_error(message: str, *, data: dict[str, Any] | None = None) -> ToolResult:
    payload = {"success": False, "error": message}
    if data:
        payload.update(data)
    return ToolResult(
        content=json.dumps(payload, indent=2, sort_keys=True),
        is_error=True,
        display=f"error: {message[:100]}",
        data=payload,
    )


def _refresh_agent_prompt(ctx: ToolContext) -> None:
    refresh = getattr(getattr(ctx, "agent", None), "refresh_volatile", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:  # noqa: BLE001
            pass


def _split_skill_content(content: str) -> tuple[dict[str, Any], str, str | None]:
    if not content or not content.strip():
        return {}, "", "content cannot be empty."
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return {}, "", (
            f"content is {len(content):,} characters "
            f"(limit: {MAX_SKILL_CONTENT_CHARS:,})."
        )

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "", "content must start with YAML frontmatter."

    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, "", "frontmatter is not closed with a --- line."

    try:
        fm = yaml.safe_load("\n".join(lines[1:end]).strip()) or {}
    except yaml.YAMLError as exc:
        return {}, "", f"YAML frontmatter parse error: {exc}"
    if not isinstance(fm, dict):
        return {}, "", "frontmatter must be a YAML mapping."
    if not str(fm.get("name") or "").strip():
        return {}, "", "frontmatter must include name."
    if not str(fm.get("description") or "").strip():
        return {}, "", "frontmatter must include description."

    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        return {}, "", "SKILL.md must have a body after the frontmatter."
    return fm, body, None


def _skill_entry(skill, usage: dict[str, Any]) -> dict[str, Any]:
    ok, why = skill.satisfied()
    return {
        "name": skill.name,
        "description": skill.description,
        "path": str(skill.path),
        "tier": skill.tier,
        "available": ok,
        "unavailable_reason": why or None,
        "pinned": provenance.is_pinned(skill.name),
        "usage": usage.get(skill.name, {}),
    }


def _require_name(args: dict[str, Any], action: str) -> str | None:
    name = str(args.get("name") or "").strip()
    return name or None


def _resolve_patch_target(skill_dir: Path, file_path: str | None) -> tuple[Path | None, str | None]:
    if not file_path:
        return skill_dir / "SKILL.md", None

    rel = Path(str(file_path))
    if rel.is_absolute():
        return None, "file_path must be relative to the skill directory."
    if any(part in ("", ".", "..") for part in rel.parts):
        return None, "file_path must not contain empty, '.', or '..' path components."

    if rel.name == "SKILL.md":
        if len(rel.parts) == 1:
            return skill_dir / "SKILL.md", None
        if len(rel.parts) == 2 and rel.parts[0] == skill_dir.name:
            return skill_dir / "SKILL.md", None
        return None, "SKILL.md may only be referenced as SKILL.md or <skill-name>/SKILL.md."

    if not rel.parts or rel.parts[0] not in ALLOWED_SUPPORT_DIRS:
        allowed = ", ".join(sorted(ALLOWED_SUPPORT_DIRS))
        return None, f"file_path must be under one of: {allowed}."

    target = skill_dir / rel
    try:
        target.resolve().relative_to(skill_dir.resolve())
    except (OSError, ValueError):
        return None, "file_path escapes the skill directory."
    return target, None


def _resolve_write_target(skill_dir: Path, file_path: str | None) -> tuple[Path | None, str | None]:
    if not file_path:
        return None, "file_path is required for write_file."
    target, err = _resolve_patch_target(skill_dir, file_path)
    if err:
        return None, err
    if target is None:
        return None, "file_path could not be resolved."
    if target.name == "SKILL.md":
        allowed = ", ".join(f"{name}/" for name in sorted(ALLOWED_SUPPORT_DIRS))
        return None, f"write_file is only for support files under {allowed}."
    return target, None


def _create(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name = _require_name(args, "create")
    content = args.get("content")
    if content is not None:
        fm, body, err = _split_skill_content(str(content))
        if err:
            return _json_error(err)
        fm_name = str(fm.get("name") or "").strip()
        if name and fm_name != name:
            return _json_error(f"name '{name}' does not match frontmatter name '{fm_name}'.")
        name = fm_name
        description = str(fm.get("description") or "").strip()
        extra = {k: v for k, v in fm.items() if k not in {"name", "description"}}
    else:
        body = str(args.get("body") or "").strip()
        description = str(args.get("description") or "").strip()
        extra = {}
        if not name or not description or not body:
            return _json_error("create needs name plus either content or description and body.")

    try:
        path = ctx.skills.create(name, description, body, extra_frontmatter=extra)
    except Exception as exc:  # noqa: BLE001
        return _json_error(f"could not create skill: {exc}")

    _refresh_agent_prompt(ctx)
    return _json_result(
        {"success": True, "message": f"Skill '{name}' created.", "path": str(path)},
        f"created skill {name}",
    )


def _view(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name = _require_name(args, "view")
    if not name:
        return _json_error("name is required for view.")
    skill = ctx.skills.discover().get(name)
    if not skill:
        return _json_error(f"skill '{name}' not found.")
    ctx.skills.record_view(name)               # Hermes view_count telemetry
    content = ctx.skills.activate(name)
    return _json_result(
        {
            "success": True,
            "name": skill.name,
            "description": skill.description,
            "path": str(skill.path),
            "body": skill.full_body(),
            "content": content,
        },
        f"loaded skill {name}",
    )


def _list(ctx: ToolContext) -> ToolResult:
    usage = ctx.skills.usage()
    skills = [_skill_entry(s, usage) for s in sorted(ctx.skills.discover().values(), key=lambda s: s.name)]
    return _json_result(
        {"success": True, "count": len(skills), "skills": skills},
        "listed skills",
    )


def _usage(ctx: ToolContext) -> ToolResult:
    usage = ctx.skills.usage()
    rows = [
        {"name": name, **(entry if isinstance(entry, dict) else {})}
        for name, entry in sorted(usage.items())
    ]
    return _json_result(
        {"success": True, "usage": usage, "skills": rows},
        "skill usage",
    )


def _patch(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name = _require_name(args, "patch")
    if not name:
        return _json_error("name is required for patch.")
    old = args.get("old_string", args.get("old_text"))
    new = args.get("new_string", args.get("new_text"))
    if not old:
        return _json_error("old_string is required for patch.")
    if new is None:
        return _json_error("new_string is required for patch. Use an empty string to delete text.")

    skill = ctx.skills.discover().get(name)
    if not skill:
        return _json_error(f"skill '{name}' not found.")

    target, err = _resolve_patch_target(skill.dir, args.get("file_path"))
    if err:
        return _json_error(err)
    if target is None or not target.exists():
        rel = str(target.relative_to(skill.dir)) if target else str(args.get("file_path") or "SKILL.md")
        return _json_error(f"file not found in skill '{name}': {rel}")

    text = read_text(target)
    count = text.count(str(old))
    if count == 0:
        return _json_error("old_string was not found.")
    replace_all = bool(args.get("replace_all", False))
    if count > 1 and not replace_all:
        return _json_error(
            "old_string matched multiple times; include more context or set replace_all=true.",
            data={"matches": count},
        )
    new_text = text.replace(str(old), str(new), -1 if replace_all else 1)
    if target.name == "SKILL.md":
        _fm, _body, split_err = _split_skill_content(new_text)
        if split_err:
            return _json_error(f"patch would break SKILL.md structure: {split_err}")

    atomic_write(target, new_text)
    ctx.skills.record_patch(name)              # Hermes patch_count telemetry
    ctx.skills.invalidate()
    _refresh_agent_prompt(ctx)
    rel = str(target.relative_to(skill.dir))
    return _json_result(
        {
            "success": True,
            "message": f"Patched {rel} in skill '{name}'.",
            "path": str(target),
            "replacements": count if replace_all else 1,
        },
        f"patched skill {name}",
    )


def _write_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name = _require_name(args, "write_file")
    if not name:
        return _json_error("name is required for write_file.")
    content = args.get("content")
    if content is None:
        return _json_error("content is required for write_file.")
    content = str(content)
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return _json_error(
            f"content is {len(content):,} characters "
            f"(limit: {MAX_SKILL_CONTENT_CHARS:,})."
        )

    skill = ctx.skills.discover().get(name)
    if not skill:
        return _json_error(f"skill '{name}' not found.")
    target, err = _resolve_write_target(skill.dir, args.get("file_path"))
    if err:
        return _json_error(err)
    if target is None:
        return _json_error("file_path could not be resolved.")
    overwrite = bool(args.get("overwrite", False))
    if target.exists() and not overwrite:
        rel = str(target.relative_to(skill.dir))
        return _json_error(f"file already exists in skill '{name}': {rel}. Set overwrite=true.")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, content)
    except OSError as exc:
        return _json_error(f"could not write support file: {exc}")

    ctx.skills.record_patch(name)
    ctx.skills.invalidate()
    _refresh_agent_prompt(ctx)
    rel = str(target.relative_to(skill.dir))
    return _json_result(
        {
            "success": True,
            "message": f"Wrote {rel} in skill '{name}'.",
            "path": str(target),
            "overwrite": overwrite,
        },
        f"wrote skill file {name}/{rel}",
    )


def _delete(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name = _require_name(args, "delete")
    if not name:
        return _json_error("name is required for delete.")
    if provenance.is_pinned(name):
        return _json_error(f"skill '{name}' is pinned; unpin it before delete.")

    discovered = ctx.skills.discover().get(name)
    personal_dir = cfg.skills_dir() / name
    if not discovered and not personal_dir.is_dir():
        return _json_error(f"skill '{name}' not found.")

    if not curator.archive(name):
        return _json_error("delete can only archive personal skills managed under AEGIS_HOME/skills.")

    ctx.skills.invalidate()
    _refresh_agent_prompt(ctx)
    return _json_result(
        {
            "success": True,
            "message": f"Skill '{name}' archived.",
            "archived": True,
            "archive": str(cfg.sub("skills_archive") / name),
        },
        f"archived skill {name}",
    )


def _pin(args: dict[str, Any], ctx: ToolContext, pinned: bool) -> ToolResult:
    name = _require_name(args, "pin" if pinned else "unpin")
    if not name:
        return _json_error(f"name is required for {'pin' if pinned else 'unpin'}.")
    if name not in ctx.skills.discover():
        return _json_error(f"skill '{name}' not found.")
    curator.pin(name, pinned)
    return _json_result(
        {"success": True, "name": name, "pinned": pinned, "curatable": provenance.curatable(name)},
        f"{'pinned' if pinned else 'unpinned'} skill {name}",
    )


def _report(ctx: ToolContext) -> ToolResult:
    payload = {
        "success": True,
        "review": curator.review(),
        "transitions": curator.apply_transitions(dry_run=True),
        "archived": curator.archived(),
        "usage": ctx.skills.usage(),
    }
    return _json_result(payload, "skill curator report")


class SkillManageTool(Tool):
    name = "skill_manage"
    description = (
        "Manage skills with a Hermes-style action schema. Actions: list, view, create, "
        "patch, write_file, delete, usage, pin, unpin, report. Uses Aegis SkillsLoader for "
        "discovery/create/view/usage and the curator for pinning, reports, and "
        "recoverable delete-by-archive."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "name": {"type": "string", "description": "Skill name for view/create/patch/write_file/delete/pin/unpin."},
            "content": {
                "type": "string",
                "description": "Full SKILL.md content for create, including YAML frontmatter.",
            },
            "description": {"type": "string", "description": "Create fallback when content is not provided."},
            "body": {"type": "string", "description": "Create fallback when content is not provided."},
            "old_string": {"type": "string", "description": "Text to find for patch."},
            "new_string": {"type": "string", "description": "Replacement text for patch."},
            "replace_all": {
                "type": "boolean",
                "description": "For patch, replace all matches instead of requiring one unique match.",
            },
            "file_path": {
                "type": "string",
                "description": "Target within the skill. Patch defaults to SKILL.md; write_file requires a support directory.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "For write_file, replace an existing support file.",
            },
        },
        "required": ["action"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.skills is None:
            return _json_error("skills are not available.")
        action = str(args.get("action") or "").strip()
        if action == "list":
            return _list(ctx)
        if action == "view":
            return _view(args, ctx)
        if action == "create":
            return _create(args, ctx)
        if action == "patch":
            return _patch(args, ctx)
        if action == "write_file":
            return _write_file(args, ctx)
        if action == "delete":
            return _delete(args, ctx)
        if action == "usage":
            return _usage(ctx)
        if action == "pin":
            return _pin(args, ctx, True)
        if action == "unpin":
            return _pin(args, ctx, False)
        if action == "report":
            return _report(ctx)
        return _json_error(f"unknown action '{action}'. Use: {', '.join(_ACTIONS)}")


def skill_manage_tools() -> list[Tool]:
    return [SkillManageTool()]
