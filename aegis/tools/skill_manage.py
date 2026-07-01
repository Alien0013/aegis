"""Action-style skill management tool backed by AEGIS skills and curator."""

from __future__ import annotations

import contextvars
import json
from pathlib import Path
from typing import Any

import yaml

from .. import config as cfg
from .. import curator, provenance, write_approval
from ..skills import validate_skill_name
from ..util import atomic_write, read_text
from .base import Tool, ToolContext, ToolResult


MAX_SKILL_CONTENT_CHARS = 100_000
ALLOWED_SUPPORT_DIRS = {"assets", "references", "scripts", "templates"}
_ACTIONS = (
    "list", "view", "create", "edit", "patch", "write_file", "remove_file", "delete",
    "usage", "pin", "unpin", "report", "consolidate"
)
_skill_gate_bypass: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "aegis_skill_write_approval_bypass",
    default=False,
)
_background_review_read_paths: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "aegis_background_review_skill_read_paths",
    default=frozenset(),
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


def _change_preview(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _refresh_agent_prompt(ctx: ToolContext) -> None:
    refresh = getattr(getattr(ctx, "agent", None), "refresh_volatile", None)
    if callable(refresh):
        try:
            refresh()
        except Exception:  # noqa: BLE001
            pass


def _approval_origin() -> str:
    try:
        return "background_review" if provenance.current_origin() == "agent" else "foreground"
    except Exception:  # noqa: BLE001
        return "foreground"


def _is_background_review() -> bool:
    return _approval_origin() == "background_review"


def _mark_background_review_read(path: Path) -> None:
    if not _is_background_review():
        return
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    current = set(_background_review_read_paths.get())
    current.add(resolved)
    _background_review_read_paths.set(frozenset(current))


def _background_review_read_guard(
    name: str,
    target: Path,
    action: str,
    file_label: str,
) -> ToolResult | None:
    if not _is_background_review():
        return None
    try:
        resolved = str(target.resolve())
    except OSError:
        resolved = str(target)
    if resolved in _background_review_read_paths.get():
        return None
    return _json_error(
        f"Refusing background review {action} for skill '{name}': current {file_label} "
        "content has not been loaded in this review turn. Use skill_manage view first, "
        "then retry the write using the content just returned.",
        data={"_read_before_write_required": True},
    )


def _decision_action(decision: Any) -> str:
    action = str(getattr(decision, "action", "") or "").strip().lower()
    if action:
        return action
    if getattr(decision, "allow", False):
        return "allow"
    if getattr(decision, "stage", False):
        return "stage"
    if getattr(decision, "blocked", False):
        return "blocked"
    return "blocked"


def _skill_write_gate(
    payload: dict[str, Any],
    ctx: ToolContext,
    *,
    summary: str,
    detail: str = "",
    change: dict[str, Any] | None = None,
) -> ToolResult | None:
    if _skill_gate_bypass.get():
        return None
    origin = _approval_origin()
    config = getattr(ctx, "config", None)
    decision = write_approval.evaluate_gate(
        write_approval.SKILLS,
        inline_summary=summary,
        inline_detail=detail,
        config=config,
        interactive_approver=getattr(ctx, "approver", None),
        origin=origin,
    )
    action = _decision_action(decision)
    if action == "allow":
        return None
    message = str(getattr(decision, "message", "") or "").strip()
    if action == "blocked":
        return _json_error(message or "Skill write blocked by approval gate.")
    if action != "stage":
        return _json_error(f"skill write approval returned unknown action '{action}'.")

    gist = str(write_approval.skill_gist(payload) or summary)
    record = write_approval.stage_write(
        write_approval.SKILLS,
        payload,
        summary=gist,
        origin=origin,
        config=config,
    )
    result = {
        "success": True,
        "staged": True,
        "pending_id": record.get("id"),
        "gist": gist,
        "message": message or "Skill write staged for approval.",
        "_change": change or {"action": payload.get("action"), "name": payload.get("name")},
    }
    return _json_result(result, f"staged skill {payload.get('action', 'write')}")


def apply_skill_pending(
    payload: dict[str, Any],
    ctx: ToolContext,
) -> ToolResult:
    """Replay an already-approved staged skill write without re-staging it."""
    token = _skill_gate_bypass.set(True)
    try:
        return SkillManageTool().run(dict(payload or {}), ctx)
    finally:
        _skill_gate_bypass.reset(token)


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


def _require_safe_name(args: dict[str, Any], action: str) -> tuple[str | None, ToolResult | None]:
    name = _require_name(args, action)
    if not name:
        return None, _json_error(f"name is required for {action}.")
    try:
        return validate_skill_name(name), None
    except ValueError as exc:
        return None, _json_error(str(exc))


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


def _managed_child_dir(root: Path, path: Path) -> Path | None:
    try:
        root_resolved = root.resolve()
        path_resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if path.is_symlink():
        return None
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError:
        return None
    if path_resolved == root_resolved:
        return None
    if not path.is_dir():
        return None
    return path_resolved


def _archive_validation_error(name: str) -> str | None:
    msg = "delete can only archive personal skills managed under AEGIS_HOME/skills."
    skills_root = cfg.skills_dir()
    archive_root = cfg.sub("skills_archive")
    if _managed_child_dir(skills_root, skills_root / name) is None:
        return msg
    dest = archive_root / name
    if (dest.exists() or dest.is_symlink()) and _managed_child_dir(archive_root, dest) is None:
        return msg
    return None


def _consolidate_error(name: str, into: str) -> str:
    return (
        f"could not consolidate '{name}' into '{into}' — check both exist and '{name}' is "
        "agent-created (bundled/hub/user/pinned skills are protected)."
    )


def _consolidate_validation_error(name: str, into: str) -> str | None:
    try:
        from_name = validate_skill_name(name)
        into_name = validate_skill_name(into)
    except ValueError:
        return _consolidate_error(name, into)
    if from_name == into_name or not provenance.curatable(from_name):
        return _consolidate_error(name, into)
    root = cfg.skills_dir()
    if not ((root / from_name).is_dir() and (root / into_name).is_dir()):
        return _consolidate_error(name, into)
    return None


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
        name = validate_skill_name(str(name or ""))
    except ValueError as exc:
        return _json_error(str(exc))
    if name in ctx.skills.discover() or (cfg.skills_dir() / name).exists():
        return _json_error(f"skill '{name}' already exists.")

    change = {
        "action": "create",
        "name": name,
        "description": _change_preview(description, 120),
    }
    payload = {"action": "create", "name": name}
    if content is not None:
        payload["content"] = str(content)
    else:
        payload.update({"description": description, "body": body})
    gated = _skill_write_gate(
        payload,
        ctx,
        summary=f"create skill '{name}'",
        detail=str(content if content is not None else body),
        change=change,
    )
    if gated:
        return gated

    try:
        path = ctx.skills.create(name, description, body, extra_frontmatter=extra)
    except Exception as exc:  # noqa: BLE001
        return _json_error(f"could not create skill: {exc}")

    _refresh_agent_prompt(ctx)
    return _json_result(
        {
            "success": True,
            "message": f"Skill '{name}' created.",
            "path": str(path),
            "_change": change,
        },
        f"created skill {name}",
    )


def _edit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name, err = _require_safe_name(args, "edit")
    if err:
        return err
    content = args.get("content")
    if content is None:
        return _json_error("content is required for edit. Provide the full updated SKILL.md text.")
    content = str(content)
    fm, _body, split_err = _split_skill_content(content)
    if split_err:
        return _json_error(split_err)
    fm_name = str(fm.get("name") or "").strip()
    if fm_name != name:
        return _json_error(f"name '{name}' does not match frontmatter name '{fm_name}'.")

    skill = ctx.skills.discover().get(name)
    if not skill:
        return _json_error(f"skill '{name}' not found.")
    read_guard = _background_review_read_guard(name, skill.path, "edit", "SKILL.md")
    if read_guard:
        return read_guard

    description = str(fm.get("description") or "").strip()
    change = {
        "action": "edit",
        "name": name,
        "description": _change_preview(description, 120),
        "chars": len(content),
    }
    gated = _skill_write_gate(
        {"action": "edit", "name": name, "content": content},
        ctx,
        summary=f"edit skill '{name}'",
        detail=content,
        change=change,
    )
    if gated:
        return gated

    try:
        atomic_write(skill.path, content)
    except OSError as exc:
        return _json_error(f"could not edit skill: {exc}")

    ctx.skills.record_patch(name)
    ctx.skills.invalidate()
    _refresh_agent_prompt(ctx)
    return _json_result(
        {
            "success": True,
            "message": f"Skill '{name}' updated.",
            "path": str(skill.path),
            "_change": change,
        },
        f"edited skill {name}",
    )


def _view(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name, err = _require_safe_name(args, "view")
    if err:
        return err
    skill = ctx.skills.discover().get(name)
    if not skill:
        return _json_error(f"skill '{name}' not found.")
    file_path = str(args.get("file_path") or "").strip()
    if file_path:
        target, target_err = _resolve_patch_target(skill.dir, file_path)
        if target_err:
            return _json_error(target_err)
        if target is None or not target.exists():
            return _json_error(f"file not found in skill '{name}': {file_path}")
        body = read_text(target)
        rel = str(target.relative_to(skill.dir))
        _mark_background_review_read(target)
        ctx.skills.record_view(name)
        return _json_result(
            {
                "success": True,
                "name": skill.name,
                "description": skill.description,
                "path": str(target),
                "file_path": rel,
                "body": body,
                "content": body,
            },
            f"loaded skill {name}/{rel}",
        )
    _mark_background_review_read(skill.path)
    ctx.skills.record_view(name)               # AEGIS view_count telemetry
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
    name, err = _require_safe_name(args, "patch")
    if err:
        return err
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

    rel = str(target.relative_to(skill.dir))
    read_guard = _background_review_read_guard(name, target, "patch", rel)
    if read_guard:
        return read_guard
    text = read_text(target)
    count = text.count(str(old))
    if count == 0:
        from .fuzzy import find_fuzzy, reindent

        hit = find_fuzzy(text, str(old))
        if hit is None:
            return _json_error("old_string was not found.")
        matched, strategy = hit
        old = matched
        new = reindent(str(new), matched, str(args.get("old_string", args.get("old_text"))))
        count = 1
        fuzzy_note = f" matched via {strategy}."
    else:
        fuzzy_note = ""
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

    change = {
        "action": "patch",
        "name": name,
        "file_path": rel,
        "old": _change_preview(old, 200),
        "new": _change_preview(new, 200),
    }
    gated = _skill_write_gate(
        {
            "action": "patch",
            "name": name,
            "file_path": rel,
            "old_string": str(old),
            "new_string": str(new),
            "replace_all": replace_all,
        },
        ctx,
        summary=f"patch {rel} in skill '{name}'",
        detail=f"old: {old}\nnew: {new}",
        change=change,
    )
    if gated:
        return gated

    atomic_write(target, new_text)
    ctx.skills.record_patch(name)              # AEGIS patch_count telemetry
    ctx.skills.invalidate()
    _refresh_agent_prompt(ctx)
    return _json_result(
        {
            "success": True,
            "message": f"Patched {rel} in skill '{name}'.{fuzzy_note}",
            "path": str(target),
            "replacements": count if replace_all else 1,
            "_change": change,
        },
        f"patched skill {name}",
    )


def _write_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name, err = _require_safe_name(args, "write_file")
    if err:
        return err
    content = args.get("content", args.get("file_content"))
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

    rel = str(target.relative_to(skill.dir))
    if target.exists():
        read_guard = _background_review_read_guard(name, target, "write_file", rel)
        if read_guard:
            return read_guard
    overwrite = bool(args.get("overwrite", target.exists()))
    change = {
        "action": "write_file",
        "name": name,
        "file_path": rel,
        "overwrite": overwrite,
        "chars": len(content),
    }
    gated = _skill_write_gate(
        {
            "action": "write_file",
            "name": name,
            "file_path": rel,
            "content": content,
            "file_content": content,
            "overwrite": overwrite,
        },
        ctx,
        summary=f"write {rel} in skill '{name}'",
        detail=content,
        change=change,
    )
    if gated:
        return gated

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, content)
    except OSError as exc:
        return _json_error(f"could not write support file: {exc}")

    ctx.skills.record_patch(name)
    ctx.skills.invalidate()
    _refresh_agent_prompt(ctx)
    return _json_result(
        {
            "success": True,
            "message": f"Wrote {rel} in skill '{name}'.",
            "path": str(target),
            "overwrite": overwrite,
        },
        f"wrote skill file {name}/{rel}",
    )


def _remove_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name, err = _require_safe_name(args, "remove_file")
    if err:
        return err
    skill = ctx.skills.discover().get(name)
    if not skill:
        return _json_error(f"skill '{name}' not found.")
    target, err = _resolve_write_target(skill.dir, args.get("file_path"))
    if err:
        return _json_error(err)
    if target is None:
        return _json_error("file_path could not be resolved.")

    rel = str(target.relative_to(skill.dir))
    if not target.exists():
        available: list[str] = []
        for subdir in sorted(ALLOWED_SUPPORT_DIRS):
            root = skill.dir / subdir
            if root.exists():
                available.extend(
                    str(path.relative_to(skill.dir))
                    for path in root.rglob("*")
                    if path.is_file()
                )
        return _json_error(
            f"file not found in skill '{name}': {rel}",
            data={"available_files": available or None},
        )

    read_guard = _background_review_read_guard(name, target, "remove_file", rel)
    if read_guard:
        return read_guard
    change = {"action": "remove_file", "name": name, "file_path": rel}
    gated = _skill_write_gate(
        {"action": "remove_file", "name": name, "file_path": rel},
        ctx,
        summary=f"remove {rel} from skill '{name}'",
        change=change,
    )
    if gated:
        return gated

    try:
        target.unlink()
        parent = target.parent
        if parent != skill.dir and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as exc:
        return _json_error(f"could not remove support file: {exc}")

    ctx.skills.record_patch(name)
    ctx.skills.invalidate()
    _refresh_agent_prompt(ctx)
    return _json_result(
        {
            "success": True,
            "message": f"Removed {rel} from skill '{name}'.",
            "path": str(target),
            "_change": change,
        },
        f"removed skill file {name}/{rel}",
    )


def _delete(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name, err = _require_safe_name(args, "delete")
    if err:
        return err
    if provenance.is_pinned(name):
        return _json_error(f"skill '{name}' is pinned; unpin it before delete.")

    discovered = ctx.skills.discover().get(name)
    personal_dir = cfg.skills_dir() / name
    if not discovered and not personal_dir.is_dir():
        return _json_error(f"skill '{name}' not found.")
    if discovered:
        read_guard = _background_review_read_guard(name, discovered.path, "delete", "SKILL.md")
        if read_guard:
            return read_guard

    archive_err = _archive_validation_error(name)
    if archive_err:
        return _json_error(archive_err)
    gated = _skill_write_gate(
        {"action": "delete", "name": name},
        ctx,
        summary=f"delete skill '{name}'",
        change={"action": "delete", "name": name},
    )
    if gated:
        return gated

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
    action = "pin" if pinned else "unpin"
    name, err = _require_safe_name(args, action)
    if err:
        return err
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
        "consolidation_candidates": curator.consolidation_candidates(),
        "archived": curator.archived(),
        "usage": ctx.skills.usage(),
    }
    return _json_result(payload, "skill curator report")


def _consolidate(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Fold one skill into another: ``name`` is absorbed into ``into`` (its SKILL.md is
    filed under the survivor's references/) and then archived with a pointer."""
    name = (args.get("name") or "").strip()
    into = (args.get("into") or "").strip()
    if not name or not into:
        return _json_error("consolidate requires 'name' (folded away) and 'into' (survivor).")
    consolidate_err = _consolidate_validation_error(name, into)
    if consolidate_err:
        return _json_error(consolidate_err)
    gated = _skill_write_gate(
        {"action": "consolidate", "name": name, "into": into},
        ctx,
        summary=f"consolidate '{name}' into '{into}'",
        change={"action": "consolidate", "name": name, "into": into},
    )
    if gated:
        return gated
    if curator.consolidate(name, into):
        _refresh_agent_prompt(ctx)
        return _json_result({"success": True, "from": name, "into": into},
                            f"consolidated '{name}' into '{into}'")
    return _json_error(_consolidate_error(name, into))


class SkillManageTool(Tool):
    name = "skill_manage"
    extra_toolsets = ["skills"]
    description = (
        "Manage skills with an action-style schema. Actions: list, view, create, "
        "edit, patch, write_file, remove_file, delete, usage, pin, unpin, report, "
        "consolidate. Uses AEGIS "
        "SkillsLoader for discovery/create/view/usage and the curator for pinning, reports, "
        "recoverable delete-by-archive, and consolidating overlapping skills into one."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "name": {"type": "string", "description": "Skill name for view/create/edit/patch/write_file/remove_file/delete/pin/unpin; the skill to fold away for consolidate."},
            "into": {"type": "string", "description": "Survivor skill that absorbs `name` for consolidate (its content is filed under the survivor's references/)."},
            "content": {
                "type": "string",
                "description": "Full SKILL.md content for create/edit, including YAML frontmatter.",
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
                "description": "Target within the skill. Patch defaults to SKILL.md; write_file/remove_file require a support directory.",
            },
            "file_content": {
                "type": "string",
                "description": "Reference-compatible alias for write_file content.",
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
        if action == "edit":
            return _edit(args, ctx)
        if action == "patch":
            return _patch(args, ctx)
        if action == "write_file":
            return _write_file(args, ctx)
        if action == "remove_file":
            return _remove_file(args, ctx)
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
        if action == "consolidate":
            return _consolidate(args, ctx)
        return _json_error(f"unknown action '{action}'. Use: {', '.join(_ACTIONS)}")


def skill_manage_tools() -> list[Tool]:
    return [SkillManageTool()]
