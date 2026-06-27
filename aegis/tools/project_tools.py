"""AEGIS Project tools with compatibility aliases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Tool, ToolContext, ToolResult


def _primary_path(project) -> str | None:
    if getattr(project, "primary_path", None):
        return project.primary_path
    folders = list(getattr(project, "folders", []) or [])
    for folder in folders:
        if getattr(folder, "is_primary", False):
            return folder.path
    return folders[0].path if folders else None


def _project_payload(project, *, active_id: str | None = None) -> dict[str, Any]:
    primary = _primary_path(project)
    payload = project.to_dict()
    payload["primary_path"] = primary
    payload["active"] = bool(active_id and project.id == active_id)
    return payload


def _resolve_project(conn, token: str):
    from .. import projects

    raw = str(token or "").strip()
    if not raw:
        return None
    found = projects.get_project(conn, raw)
    if found is not None:
        return found
    lowered = raw.lower()
    for project in projects.list_projects(conn, include_archived=True):
        if project.name.lower() == lowered:
            return project
    return None


def _apply_workspace(ctx: ToolContext, path: str | None, project_name: str, project_id: str) -> None:
    if not path:
        return
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        ctx.emit_event(
            type="project_workspace_skipped",
            project_id=project_id,
            project=project_name,
            path=str(target),
            reason="path is not an existing directory",
        )
        return

    ctx.cwd = target
    agent = getattr(ctx, "agent", None)
    if agent is not None:
        try:
            agent.cwd = target
        except Exception:  # noqa: BLE001
            pass
        tool_context = getattr(agent, "tool_context", None)
        if tool_context is not None:
            try:
                tool_context.cwd = target
            except Exception:  # noqa: BLE001
                pass
        try:
            from ..config import Workspace, context_file_max_chars
            from ..agent.context import ContextBuilder
            from ..skills import SkillsLoader

            config = getattr(agent, "config", None) or ctx.config
            agent.workspace = Workspace(target, context_file_max_chars=context_file_max_chars(config))
            agent.context_builder = ContextBuilder(config, agent.workspace, target)
            agent.skills = SkillsLoader(config, target)
            if tool_context is not None:
                tool_context.skills = agent.skills
            agent._coding_block = None
        except Exception:  # noqa: BLE001
            pass
        refresh = getattr(agent, "refresh_volatile", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:  # noqa: BLE001
                pass
    session = getattr(ctx, "session", None)
    meta = getattr(session, "meta", None)
    if isinstance(meta, dict):
        meta["project_id"] = project_id
        meta["project_name"] = project_name
        meta["project_path"] = str(target)
    ctx.emit_event(type="project_workspace_changed", project_id=project_id, project=project_name, path=str(target))


def _ok_json(data: dict[str, Any], display: str) -> ToolResult:
    return ToolResult.ok(json.dumps(data, indent=2, sort_keys=True), display=display, data=data)


class ProjectListTool(Tool):
    name = "project_list"
    toolset = "project"
    description = "List the desktop Projects (named workspaces) and which one is active."
    parameters = {"type": "object", "properties": {}}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # noqa: ARG002
        from .. import projects

        with projects.connect_closing() as conn:
            active_id = projects.get_active_id(conn)
            rows = projects.list_projects(conn)
        data = {
            "active_id": active_id,
            "projects": [_project_payload(project, active_id=active_id) for project in rows],
        }
        return _ok_json(data, f"{len(rows)} project(s)")


class ProjectCreateTool(Tool):
    name = "project_create"
    toolset = "project"
    description = (
        "Create a desktop Project (a named workspace) and switch this chat into it. "
        "Pass `path` to anchor it to a repo/folder — this chat's workspace moves there "
        "and the sidebar follows. Use when starting work in a new repo/folder; this is "
        "the intentional way to move the session, not `cd`."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Human name, e.g. 'Aurora Demo'"},
            "path": {"type": "string", "description": "Primary repo/folder to anchor the project to"},
        },
        "required": ["name"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .. import projects

        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.error("name is required")
        folder = str(args.get("path") or "").strip()
        primary = str(Path(folder).expanduser().resolve()) if folder else None
        try:
            with projects.connect_closing() as conn:
                project_id = projects.create_project(
                    conn,
                    name=name,
                    folders=[primary] if primary else [],
                    primary_path=primary,
                )
                projects.set_active(conn, project_id)
                project = projects.get_project(conn, project_id)
        except ValueError as exc:
            return ToolResult.error(str(exc))
        if project is None:
            return ToolResult.error("project vanished after create")

        _apply_workspace(ctx, _primary_path(project), project.name, project.id)
        data = {"success": True, **_project_payload(project, active_id=project.id)}
        return _ok_json(data, f"project {project.slug}")


class ProjectSwitchTool(Tool):
    name = "project_switch"
    toolset = "project"
    description = (
        "Switch this chat into an existing desktop Project (by name, slug, or id). "
        "Moves the session's workspace to the project's primary folder and the sidebar "
        "follows. The intentional way to move between projects, not `cd`."
    )
    parameters = {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Project name, slug, or id"},
        },
        "required": ["project"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .. import projects

        token = str(args.get("project") or "").strip()
        if not token:
            return ToolResult.error("project is required")
        with projects.connect_closing() as conn:
            project = _resolve_project(conn, token)
            if project is None:
                return ToolResult.error(f"no project matching {token!r}")
            projects.set_active(conn, project.id)

        _apply_workspace(ctx, _primary_path(project), project.name, project.id)
        data = {"success": True, **_project_payload(project, active_id=project.id)}
        return _ok_json(data, f"project {project.slug}")


def project_tools() -> list[Tool]:
    return [ProjectListTool(), ProjectCreateTool(), ProjectSwitchTool()]
