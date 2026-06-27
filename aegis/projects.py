"""First-class project/workspace store.

AEGIS keeps Projects profile-local, matching the reference semantics:
a Project is an explicit named workspace with an optional primary folder and an
active-project pointer.  The desktop/TUI can use the same store as the tool layer
instead of inferring workspaces from recent session cwd values.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import config as cfg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    description   TEXT,
    icon          TEXT,
    color         TEXT,
    board_slug    TEXT,
    primary_path  TEXT,
    created_at    INTEGER NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_folders (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    label       TEXT,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    added_at    INTEGER NOT NULL,
    PRIMARY KEY (project_id, path)
);

CREATE INDEX IF NOT EXISTS idx_project_folders_path
    ON project_folders(path);

CREATE TABLE IF NOT EXISTS project_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")
_ACTIVE_META_KEY = "active_id"
_INITIALIZED_PATHS: set[str] = set()


@dataclass
class ProjectFolder:
    path: str
    label: str | None = None
    is_primary: bool = False
    added_at: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "label": self.label,
            "is_primary": bool(self.is_primary),
            "added_at": self.added_at,
        }


@dataclass
class Project:
    id: str
    slug: str
    name: str
    created_at: int
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    board_slug: str | None = None
    primary_path: str | None = None
    archived: bool = False
    folders: list[ProjectFolder] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "board_slug": self.board_slug,
            "primary_path": self.primary_path,
            "archived": bool(self.archived),
            "created_at": self.created_at,
            "folders": [folder.to_dict() for folder in self.folders],
        }


def projects_db_path() -> Path:
    """Profile-aware projects DB path."""

    return cfg.sub("projects.db")


def normalize_slug(slug: str | None) -> str | None:
    if slug is None:
        return None
    value = str(slug).strip().lower()
    if not value:
        return None
    if not _SLUG_RE.match(value):
        raise ValueError(
            f"invalid project slug {slug!r}: must be 1-64 chars, lowercase "
            "alphanumerics / hyphens / underscores, not starting with '-' or '_'"
        )
    return value


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-_")
    slug = slug[:64].strip("-_")
    return slug or "project"


def _new_project_id() -> str:
    return "p_" + secrets.token_hex(4)


def _now() -> int:
    return int(time.time())


def _normalize_path(path: str | os.PathLike[str]) -> str:
    value = os.path.abspath(os.path.expanduser(str(path).strip()))
    return value.rstrip("/\\") or value


@contextlib.contextmanager
def _write_txn(conn: sqlite3.Connection):
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or projects_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        if resolved not in _INITIALIZED_PATHS:
            conn.executescript(SCHEMA_SQL)
            _INITIALIZED_PATHS.add(resolved)
    except Exception:
        conn.close()
        raise
    return conn


@contextlib.contextmanager
def connect_closing(db_path: Path | None = None):
    conn = connect(db_path=db_path)
    try:
        yield conn
    finally:
        conn.close()


def _project_from_row(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        created_at=row["created_at"],
        description=row["description"],
        icon=row["icon"],
        color=row["color"],
        board_slug=row["board_slug"],
        primary_path=row["primary_path"],
        archived=bool(row["archived"]),
    )


def _load_folders(conn: sqlite3.Connection, project_id: str) -> list[ProjectFolder]:
    rows = conn.execute(
        "SELECT path, label, is_primary, added_at FROM project_folders "
        "WHERE project_id = ? ORDER BY is_primary DESC, added_at ASC",
        (project_id,),
    ).fetchall()
    return [
        ProjectFolder(
            path=row["path"],
            label=row["label"],
            is_primary=bool(row["is_primary"]),
            added_at=row["added_at"],
        )
        for row in rows
    ]


def _attach_folders(conn: sqlite3.Connection, project: Project) -> Project:
    project.folders = _load_folders(conn, project.id)
    return project


def _unique_slug(conn: sqlite3.Connection, candidate: str) -> str:
    base = candidate
    slug = base
    n = 1
    while conn.execute("SELECT 1 FROM projects WHERE slug = ?", (slug,)).fetchone() is not None:
        n += 1
        suffix = f"-{n}"
        slug = (base[: 64 - len(suffix)]).rstrip("-_") + suffix
    return slug


def create_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    slug: str | None = None,
    folders: Iterable[str] | None = None,
    primary_path: str | None = None,
    description: str | None = None,
    icon: str | None = None,
    color: str | None = None,
    board_slug: str | None = None,
) -> str:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("project name must not be empty")

    folder_paths: list[str] = []
    for folder in folders or []:
        normalized = _normalize_path(folder)
        if normalized and normalized not in folder_paths:
            folder_paths.append(normalized)
    primary = _normalize_path(primary_path) if primary_path else None
    if primary and primary not in folder_paths:
        folder_paths.insert(0, primary)
    if primary is None and folder_paths:
        primary = folder_paths[0]

    pid = _new_project_id()
    now = _now()
    slug_candidate = normalize_slug(slug) if slug else _slugify(clean_name)
    with _write_txn(conn):
        unique = _unique_slug(conn, slug_candidate or "project")
        conn.execute(
            "INSERT INTO projects "
            "(id, slug, name, description, icon, color, board_slug, primary_path, created_at, archived) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                pid,
                unique,
                clean_name,
                description,
                icon,
                color,
                normalize_slug(board_slug) if board_slug else None,
                primary,
                now,
            ),
        )
        for path in folder_paths:
            conn.execute(
                "INSERT INTO project_folders (project_id, path, label, is_primary, added_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, path, None, 1 if path == primary else 0, now),
            )
    return pid


def list_projects(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[Project]:
    sql = "SELECT * FROM projects"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY created_at ASC"
    return [_attach_folders(conn, _project_from_row(row)) for row in conn.execute(sql).fetchall()]


def get_project(conn: sqlite3.Connection, id_or_slug: str) -> Project | None:
    token = str(id_or_slug or "").strip()
    if not token:
        return None
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (token,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM projects WHERE slug = ?", (token.lower(),)).fetchone()
    if row is None:
        return None
    return _attach_folders(conn, _project_from_row(row))


def _require_project(conn: sqlite3.Connection, project_id: str) -> Project:
    project = get_project(conn, project_id)
    if project is None:
        raise ValueError(f"project not found: {project_id}")
    return project


def add_folder(
    conn: sqlite3.Connection,
    project_id: str,
    path: str | os.PathLike[str],
    *,
    label: str | None = None,
    is_primary: bool = False,
) -> str:
    """Add or update a folder on a project and optionally make it primary."""

    project = _require_project(conn, project_id)
    normalized = _normalize_path(path)
    clean_label = str(label).strip() if label is not None and str(label).strip() else None
    make_primary = bool(is_primary) or not project.primary_path
    now = _now()
    with _write_txn(conn):
        if make_primary:
            conn.execute("UPDATE project_folders SET is_primary = 0 WHERE project_id = ?", (project.id,))
        conn.execute(
            "INSERT INTO project_folders (project_id, path, label, is_primary, added_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, path) DO UPDATE SET "
            "label = COALESCE(excluded.label, project_folders.label), "
            "is_primary = CASE WHEN excluded.is_primary = 1 THEN 1 ELSE project_folders.is_primary END",
            (project.id, normalized, clean_label, 1 if make_primary else 0, now),
        )
        if make_primary:
            conn.execute("UPDATE projects SET primary_path = ? WHERE id = ?", (normalized, project.id))
    return normalized


def remove_folder(conn: sqlite3.Connection, project_id: str, path: str | os.PathLike[str]) -> bool:
    """Remove a project folder, promoting the oldest remaining folder if needed."""

    project = _require_project(conn, project_id)
    normalized = _normalize_path(path)
    row = conn.execute(
        "SELECT is_primary FROM project_folders WHERE project_id = ? AND path = ?",
        (project.id, normalized),
    ).fetchone()
    if row is None:
        return False

    was_primary = bool(row["is_primary"]) or project.primary_path == normalized
    with _write_txn(conn):
        conn.execute("DELETE FROM project_folders WHERE project_id = ? AND path = ?", (project.id, normalized))
        if was_primary:
            replacement = conn.execute(
                "SELECT path FROM project_folders WHERE project_id = ? ORDER BY added_at ASC LIMIT 1",
                (project.id,),
            ).fetchone()
            if replacement is None:
                conn.execute("UPDATE projects SET primary_path = NULL WHERE id = ?", (project.id,))
            else:
                conn.execute("UPDATE project_folders SET is_primary = 0 WHERE project_id = ?", (project.id,))
                conn.execute(
                    "UPDATE project_folders SET is_primary = 1 WHERE project_id = ? AND path = ?",
                    (project.id, replacement["path"]),
                )
                conn.execute("UPDATE projects SET primary_path = ? WHERE id = ?", (replacement["path"], project.id))
    return True


def set_primary(conn: sqlite3.Connection, project_id: str, path: str | os.PathLike[str]) -> bool:
    """Mark an existing project folder as the primary workspace path."""

    project = _require_project(conn, project_id)
    normalized = _normalize_path(path)
    row = conn.execute(
        "SELECT 1 FROM project_folders WHERE project_id = ? AND path = ?",
        (project.id, normalized),
    ).fetchone()
    if row is None:
        return False
    with _write_txn(conn):
        conn.execute("UPDATE project_folders SET is_primary = 0 WHERE project_id = ?", (project.id,))
        conn.execute(
            "UPDATE project_folders SET is_primary = 1 WHERE project_id = ? AND path = ?",
            (project.id, normalized),
        )
        conn.execute("UPDATE projects SET primary_path = ? WHERE id = ?", (normalized, project.id))
    return True


def update_project(conn: sqlite3.Connection, project_id: str, **fields: str | None) -> Project:
    """Update editable project metadata and return the refreshed project."""

    project = _require_project(conn, project_id)
    updates: dict[str, str | None] = {}
    if "name" in fields and fields["name"] is not None:
        name = str(fields["name"] or "").strip()
        if not name:
            raise ValueError("project name must not be empty")
        updates["name"] = name
    if "description" in fields:
        value = fields["description"]
        updates["description"] = (str(value).strip() or None) if value is not None else None
    if "icon" in fields:
        value = fields["icon"]
        updates["icon"] = (str(value).strip() or None) if value is not None else None
    if "color" in fields:
        value = fields["color"]
        updates["color"] = (str(value).strip() or None) if value is not None else None
    if "board_slug" in fields:
        value = fields["board_slug"]
        updates["board_slug"] = normalize_slug(value) if value else None
    if "slug" in fields and fields["slug"]:
        slug = normalize_slug(fields["slug"])
        existing = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
        if existing is not None and existing["id"] != project.id:
            raise ValueError(f"project slug already exists: {slug}")
        updates["slug"] = slug
    if updates:
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with _write_txn(conn):
            conn.execute(f"UPDATE projects SET {assignments} WHERE id = ?", (*updates.values(), project.id))
    refreshed = get_project(conn, project.id)
    assert refreshed is not None
    return refreshed


def archive_project(conn: sqlite3.Connection, project_id: str) -> bool:
    project = _require_project(conn, project_id)
    with _write_txn(conn):
        conn.execute("UPDATE projects SET archived = 1 WHERE id = ?", (project.id,))
        if get_active_id(conn) == project.id:
            conn.execute("DELETE FROM project_meta WHERE key = ?", (_ACTIVE_META_KEY,))
    return True


def restore_project(conn: sqlite3.Connection, project_id: str) -> bool:
    project = _require_project(conn, project_id)
    with _write_txn(conn):
        conn.execute("UPDATE projects SET archived = 0 WHERE id = ?", (project.id,))
    return True


def set_active(conn: sqlite3.Connection, project_id: str | None) -> None:
    with _write_txn(conn):
        if project_id is None:
            conn.execute("DELETE FROM project_meta WHERE key = ?", (_ACTIVE_META_KEY,))
        else:
            conn.execute(
                "INSERT INTO project_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_ACTIVE_META_KEY, project_id),
            )


def get_active_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM project_meta WHERE key = ?", (_ACTIVE_META_KEY,)).fetchone()
    return row["value"] if row else None


def project_for_path(conn: sqlite3.Connection, path: str, *, include_archived: bool = False) -> Project | None:
    if not str(path or "").strip():
        return None
    target = _normalize_path(path)
    sql = "SELECT pf.project_id AS pid, pf.path AS folder FROM project_folders pf JOIN projects p ON p.id = pf.project_id"
    if not include_archived:
        sql += " WHERE p.archived = 0"
    best_pid: str | None = None
    best_len = -1
    for row in conn.execute(sql).fetchall():
        folder = row["folder"].rstrip("/\\")
        if target == folder or target.startswith(folder + os.sep) or target.startswith(folder + "/"):
            if len(folder) > best_len:
                best_pid = row["pid"]
                best_len = len(folder)
    return get_project(conn, best_pid) if best_pid else None


def branch_name_for(project: Project, task_id: str, *, title: str = "") -> str:
    slug = project.slug or _slugify(project.name)
    base = f"{slug}/{task_id}"
    if title:
        title_slug = re.sub(r"[^a-z0-9._-]+", "-", str(title).strip().lower()).strip("-")[:40].strip("-")
        if title_slug:
            base = f"{base}-{title_slug}"
    return base
