"""Hermes-compatible Project CLI commands for AEGIS."""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from .. import projects as store
from ..config import Config


def add_project_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Attach the `aegis project ...` command tree to the root parser."""

    parser = subparsers.add_parser(
        "project",
        help="manage projects (named, multi-folder workspaces)",
        description=(
            "Projects are named workspaces that can span multiple folders. "
            "They are profile-local and anchor desktop/TUI/session workspace context."
        ),
    )
    sub = parser.add_subparsers(dest="project_action")

    create = sub.add_parser("create", help="create a new project")
    create.add_argument("name", help="human project name")
    create.add_argument("folders", nargs="*", help="folder paths to include; first folder becomes primary")
    create.add_argument("--path", action="append", dest="paths", default=[], help="workspace path to include; first --path becomes primary")
    create.add_argument("--slug", help="explicit project slug")
    create.add_argument("--primary", metavar="PATH", help="primary workspace path")
    create.add_argument("--description", help="project description")
    create.add_argument("--icon", help="optional icon metadata")
    create.add_argument("--color", help="optional color metadata")
    create.add_argument("--board", help="optional kanban board slug")
    create.add_argument("--use", action="store_true", help="set this project active after creation")

    list_cmd = sub.add_parser("list", aliases=["ls"], help="list projects")
    list_cmd.add_argument("--all", action="store_true", dest="include_archived", help="include archived projects")

    show = sub.add_parser("show", help="show project details")
    show.add_argument("project", help="project id or slug")

    add = sub.add_parser("add-folder", help="add a folder to a project")
    add.add_argument("project", help="project id or slug")
    add.add_argument("path", help="folder path")
    add.add_argument("--label", help="friendly folder label")
    add.add_argument("--primary", action="store_true", help="mark this folder as primary")

    remove = sub.add_parser("remove-folder", help="remove a folder from a project")
    remove.add_argument("project", help="project id or slug")
    remove.add_argument("path", help="folder path")

    rename = sub.add_parser("rename", help="rename a project")
    rename.add_argument("project", help="project id or slug")
    rename.add_argument("name", help="new project name")

    primary = sub.add_parser("set-primary", help="set the primary folder")
    primary.add_argument("project", help="project id or slug")
    primary.add_argument("path", help="folder path already attached to the project")

    use = sub.add_parser("use", help="set or clear the active project")
    use.add_argument("project", nargs="?", help="project id or slug; omit to clear")

    switch = sub.add_parser("switch", aliases=["select"], help="switch the active project")
    switch.add_argument("project", nargs="?", help="project id or slug; omit to clear")

    sub.add_parser("current", aliases=["status"], help="show the active project")

    archive = sub.add_parser("archive", help="archive a project")
    archive.add_argument("project", help="project id or slug")

    restore = sub.add_parser("restore", help="restore an archived project")
    restore.add_argument("project", help="project id or slug")

    bind = sub.add_parser("bind-board", help="bind or unbind a kanban board slug")
    bind.add_argument("project", help="project id or slug")
    bind.add_argument("board", nargs="?", default="", help="board slug; omit to unbind")

    parser.set_defaults(func=cmd_project, _project_parser=parser)
    for child in sub.choices.values():
        child.set_defaults(func=cmd_project, _project_parser=parser)
    return parser


def cmd_project(args: argparse.Namespace, config: Config) -> int:  # noqa: ARG001
    action = getattr(args, "project_action", None)
    if not action:
        parser = getattr(args, "_project_parser", None)
        if parser is not None:
            parser.print_help()
        else:
            print("usage: aegis project <action> [options]", file=sys.stderr)
        return 0

    handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "create": _cmd_create,
        "list": _cmd_list,
        "ls": _cmd_list,
        "show": _cmd_show,
        "add-folder": _cmd_add_folder,
        "remove-folder": _cmd_remove_folder,
        "rename": _cmd_rename,
        "set-primary": _cmd_set_primary,
        "use": _cmd_use,
        "switch": _cmd_use,
        "select": _cmd_use,
        "current": _cmd_current,
        "status": _cmd_current,
        "archive": _cmd_archive,
        "restore": _cmd_restore,
        "bind-board": _cmd_bind_board,
    }
    handler = handlers.get(action)
    if handler is None:
        print(f"project: unknown action: {action}", file=sys.stderr)
        return 1
    try:
        return handler(args)
    except ValueError as exc:
        print(f"project: {exc}", file=sys.stderr)
        return 2


def _resolve(conn, token: str):
    project = store.get_project(conn, token)
    if project is None:
        print(f"project: no such project: {token}", file=sys.stderr)
    return project


def _print_project(project: store.Project) -> None:
    suffix = " (archived)" if project.archived else ""
    print(f"{project.slug}  [{project.id}]{suffix}")
    print(f"  name:    {project.name}")
    if project.description:
        print(f"  about:   {project.description}")
    if project.board_slug:
        print(f"  board:   {project.board_slug}")
    if project.primary_path:
        print(f"  primary: {project.primary_path}")
    if project.folders:
        print("  folders:")
        for folder in project.folders:
            marker = "*" if folder.is_primary else " "
            label = f" ({folder.label})" if folder.label else ""
            print(f"   {marker} {folder.path}{label}")


def _cmd_create(args: argparse.Namespace) -> int:
    path_args = list(getattr(args, "paths", []) or [])
    folders = [*path_args, *list(args.folders or [])]
    primary = args.primary or (path_args[0] if path_args else None)
    with store.connect_closing() as conn:
        project_id = store.create_project(
            conn,
            name=args.name,
            slug=args.slug,
            folders=folders,
            primary_path=primary,
            description=args.description,
            icon=args.icon,
            color=args.color,
            board_slug=args.board,
        )
        if args.use:
            store.set_active(conn, project_id)
        project = store.get_project(conn, project_id)
    if project is None:
        print("project: project disappeared after create", file=sys.stderr)
        return 2
    print(f"Created project {project.slug} ({project.id})")
    _print_project(project)
    return 0


def _cmd_current(args: argparse.Namespace) -> int:  # noqa: ARG001
    with store.connect_closing() as conn:
        active_id = store.get_active_id(conn)
        project = store.get_project(conn, active_id) if active_id else None
    if project is None:
        print("No active project")
        return 0
    print("active project:")
    _print_project(project)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        active_id = store.get_active_id(conn)
        projects = store.list_projects(conn, include_archived=getattr(args, "include_archived", False))
    if not projects:
        print("No projects yet. Create one with `aegis project create <name>`.")
        return 0
    for project in projects:
        marker = "*" if project.id == active_id else " "
        suffix = " (archived)" if project.archived else ""
        print(f"{marker} {project.slug:<24} {project.name}{suffix}  [{len(project.folders)} folder(s)]")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        project = _resolve(conn, args.project)
    if project is None:
        return 1
    _print_project(project)
    return 0


def _cmd_add_folder(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        project = _resolve(conn, args.project)
        if project is None:
            return 1
        path = store.add_folder(conn, project.id, args.path, label=args.label, is_primary=args.primary)
    print(f"Added {path} to {project.slug}")
    return 0


def _cmd_remove_folder(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        project = _resolve(conn, args.project)
        if project is None:
            return 1
        removed = store.remove_folder(conn, project.id, args.path)
    if not removed:
        print(f"project: folder not in project: {args.path}", file=sys.stderr)
        return 1
    print(f"Removed {args.path} from {project.slug}")
    return 0


def _cmd_rename(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        project = _resolve(conn, args.project)
        if project is None:
            return 1
        store.update_project(conn, project.id, name=args.name)
    print(f"Renamed {project.slug} -> {args.name}")
    return 0


def _cmd_set_primary(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        project = _resolve(conn, args.project)
        if project is None:
            return 1
        changed = store.set_primary(conn, project.id, args.path)
    if not changed:
        print(f"project: folder is not attached to {project.slug}: {args.path}", file=sys.stderr)
        return 1
    print(f"Set primary of {project.slug} -> {args.path}")
    return 0


def _cmd_use(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        if not args.project:
            store.set_active(conn, None)
            print("Cleared active project")
            return 0
        project = _resolve(conn, args.project)
        if project is None:
            return 1
        store.set_active(conn, project.id)
    print(f"Active project: {project.slug}")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        project = _resolve(conn, args.project)
        if project is None:
            return 1
        store.archive_project(conn, project.id)
    print(f"Archived {project.slug}")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    with store.connect_closing() as conn:
        project = _resolve(conn, args.project)
        if project is None:
            return 1
        store.restore_project(conn, project.id)
    print(f"Restored {project.slug}")
    return 0


def _cmd_bind_board(args: argparse.Namespace) -> int:
    board = (args.board or "").strip()
    with store.connect_closing() as conn:
        project = _resolve(conn, args.project)
        if project is None:
            return 1
        store.update_project(conn, project.id, board_slug=board or None)
    if board:
        print(f"Bound {project.slug} -> board {board}")
    else:
        print(f"Unbound board from {project.slug}")
    return 0
