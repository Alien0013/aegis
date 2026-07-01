"""Session model + SQLite-backed session store (resume, list, search)."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config as cfg
from .types import Message, new_id
from .util import now_iso, slugify


_WAL_INCOMPAT_MARKERS = ("locking protocol", "not authorized")
_MALFORMED_DB_MARKERS = (
    "malformed database schema",
    "database disk image is malformed",
)
_WAL_SIDECAR_ERROR_MARKERS = (
    "database disk image is malformed",
    "disk i/o error",
)
_MALFORMED_REPAIR_ATTEMPTED_PATHS: set[str] = set()
_MALFORMED_REPAIR_ATTEMPT_LOCK = threading.Lock()


@dataclass
class _SessionRepairFileLock:
    path: Path
    handle: Any | None = None
    acquired: bool = False
    reason: str | None = None


def _claim_malformed_repair_attempt(db_path: Path) -> bool:
    key = str(db_path)
    with _MALFORMED_REPAIR_ATTEMPT_LOCK:
        if key in _MALFORMED_REPAIR_ATTEMPTED_PATHS:
            return False
        _MALFORMED_REPAIR_ATTEMPTED_PATHS.add(key)
        return True


def _session_source(meta: dict[str, Any]) -> str:
    """Classify a session as user-facing ('') or internal. Internal = the forked
    self-improvement sessions (memory/skill review, curator) that aren't real
    conversations — the dashboard's session list hides them (dashboard source scoping).
    Compaction-child sessions are NOT internal: they carry the user's conversation forward."""
    if not isinstance(meta, dict):
        return ""
    if meta.get("review_kind") or meta.get("curator") or meta.get("review"):
        return "internal"
    if str(meta.get("creator_kind") or "") in {"review", "curator"}:
        return "internal"
    if str(meta.get("surface") or "") in {"review", "curator"}:
        return "internal"
    return ""


_GATEWAY_GENERATION_META = "_gateway_generation"


def _gateway_generation(meta: dict[str, Any] | None) -> int | None:
    if not isinstance(meta, dict) or _GATEWAY_GENERATION_META not in meta:
        return None
    try:
        return int(meta.get(_GATEWAY_GENERATION_META))
    except (TypeError, ValueError):
        return None


def _row_gateway_generation(row: sqlite3.Row | None) -> int | None:
    if row is None:
        return None
    try:
        data = json.loads(row["data"] or "{}")
    except Exception:  # noqa: BLE001
        return None
    return _gateway_generation(data.get("meta", {}))


def _jsonable_meta_value(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _session_meta_view(sess: "Session") -> dict[str, Any]:
    meta = sess.meta if isinstance(sess.meta, dict) else {}
    out: dict[str, Any] = {
        "title": sess.title,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "parent_id": sess.parent_id,
        "profile": sess.profile,
    }
    for key in (
        "surface",
        "gateway",
        "platform",
        "chat_id",
        "thread_id",
        "user_id",
        "user_name",
        "message_id",
        "runtime",
        "runtime_controls",
        "trace_id",
        "turn_id",
        "last_trace_id",
        "last_run_id",
        "last_turn_id",
        "response_state",
        "resume_pending",
        "resume_reason",
        "last_resume_marked_at",
    ):
        if key in meta:
            out[key] = _jsonable_meta_value(meta[key])
    return out


@dataclass
class Session:
    id: str
    title: str = ""
    messages: list[Message] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    parent_id: str | None = None        # session lineage (set when forked, e.g. on compaction)
    profile: str = ""                   # config/runtime profile that owns this session

    @staticmethod
    def create(title: str = "", parent_id: str | None = None) -> "Session":
        sid = new_id("sess")
        return Session(id=sid, title=title or sid, parent_id=parent_id, profile=cfg.current_profile())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": now_iso(),
            "parent_id": self.parent_id,
            "profile": self.profile,
            "data": json.dumps(
                {
                    "messages": [m.to_dict() for m in self.messages],
                    "todos": self.todos,
                    "meta": self.meta,
                }
            ),
        }

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Session":
        data = json.loads(row["data"])
        keys = row.keys()
        return Session(
            id=row["id"],
            title=row["title"],
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            todos=data.get("todos", []),
            meta=data.get("meta", {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            parent_id=row["parent_id"] if "parent_id" in keys else None,
            profile=row["profile"] if "profile" in keys else data.get("meta", {}).get("profile", ""),
        )

    def maybe_title_from(self, text: str) -> None:
        if self.title == self.id and text.strip():
            self.title = slugify(text, 60).replace("-", " ")


class SessionStore:
    _MESSAGES_TABLE_SQL = """CREATE TABLE IF NOT EXISTS messages (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           session_id TEXT NOT NULL,
           message_index INTEGER NOT NULL,
           role TEXT,
           content TEXT,
           tool_name TEXT,
           tool_call_id TEXT,
           created_at TEXT,
           profile TEXT DEFAULT '',
           active INTEGER DEFAULT 1,
           compacted INTEGER DEFAULT 0,
           UNIQUE(session_id, message_index)
       )"""
    _FTS_TABLE_SQL = (
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
        "content, session_id UNINDEXED, title UNINDEXED, role UNINDEXED, "
        "ts UNINDEXED, message_id UNINDEXED, profile UNINDEXED)"
    )
    _FTS_REQUIRED_COLUMNS = {"message_id", "profile"}
    _MESSAGE_COPY_COLUMNS = (
        "session_id",
        "message_index",
        "role",
        "content",
        "tool_name",
        "tool_call_id",
        "created_at",
        "profile",
        "active",
        "compacted",
    )

    def __init__(self, profile: str | None = None, *, read_only: bool = False):
        self.profile = cfg.current_profile() if profile is None else cfg.profile_name(profile)
        self.read_only = read_only
        self._fts = False
        self.db = (
            cfg.profile_home(profile if profile is not None else self.profile) / "state.db"
            if self.read_only else cfg.sessions_db(profile)
        )
        if not self.read_only:
            def _init_and_probe() -> None:
                self._init()
                self._probe_session_db()

            try:
                _init_and_probe()
            except sqlite3.DatabaseError as exc:
                if self._repair_wal_sidecars(exc):
                    _init_and_probe()
                elif self._repair_malformed_sqlite_schema(exc):
                    _init_and_probe()
                else:
                    raise

    @staticmethod
    def _is_malformed_db_error(exc: BaseException) -> bool:
        if not isinstance(exc, sqlite3.DatabaseError):
            return False
        return any(marker in str(exc).lower() for marker in _MALFORMED_DB_MARKERS)

    @staticmethod
    def _is_wal_sidecar_error(exc: BaseException) -> bool:
        if not isinstance(exc, sqlite3.DatabaseError):
            return False
        return any(marker in str(exc).lower() for marker in _WAL_SIDECAR_ERROR_MARKERS)

    @staticmethod
    def _apply_wal_with_fallback(conn: sqlite3.Connection) -> None:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            if any(marker in str(exc).lower() for marker in _WAL_INCOMPAT_MARKERS):
                try:
                    conn.execute("PRAGMA journal_mode=DELETE")
                except sqlite3.OperationalError:
                    pass
            else:
                raise

    @staticmethod
    def _copy_db_repair_backup(db_path: Path, reason: str) -> Path | None:
        """Copy raw SQLite files before repair so recovery never erases evidence."""
        if not db_path.exists():
            return None
        safe_reason = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "-"
            for ch in str(reason).strip().lower()
        ).strip("-") or "repair"
        backup_path = db_path.with_name(
            f"{db_path.name}.{safe_reason}-backup-{time.time_ns()}"
        )
        try:
            shutil.copy2(db_path, backup_path)
            for suffix in ("-wal", "-shm"):
                sidecar = db_path.with_name(db_path.name + suffix)
                if sidecar.exists():
                    shutil.copy2(
                        sidecar,
                        backup_path.with_name(backup_path.name + suffix),
                    )
            return backup_path
        except OSError:
            return None

    @staticmethod
    def _repair_lock_path(db_path: Path) -> Path:
        return db_path.with_name(db_path.name + ".repair.lock")

    @classmethod
    def _acquire_repair_file_lock(cls, db_path: Path, reason: str) -> _SessionRepairFileLock:
        lock_path = cls._repair_lock_path(db_path)
        handle = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            metadata = {
                "acquired_at": now_iso(),
                "database_path": str(db_path),
                "pid": os.getpid(),
                "reason": str(reason or "repair"),
            }
            try:
                handle.seek(0)
                handle.truncate()
                handle.write(json.dumps(metadata, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                pass
            return _SessionRepairFileLock(path=lock_path, handle=handle, acquired=True)
        except BlockingIOError:
            if handle is not None:
                handle.close()
            return _SessionRepairFileLock(
                path=lock_path,
                acquired=False,
                reason="locked_by_another_process",
            )
        except OSError as exc:
            if handle is not None:
                handle.close()
            return _SessionRepairFileLock(
                path=lock_path,
                acquired=False,
                reason=f"lock_unavailable: {exc}",
            )

    @staticmethod
    def _release_repair_file_lock(lock: _SessionRepairFileLock | None) -> None:
        if lock is None or lock.handle is None:
            return
        try:
            fcntl.flock(lock.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            lock.handle.close()
        except OSError:
            pass

    @staticmethod
    def _repair_lock_status(lock: _SessionRepairFileLock | None) -> dict[str, Any]:
        if lock is None:
            return {"acquired": False, "path": None, "reason": "not_attempted"}
        return {
            "acquired": lock.acquired,
            "path": str(lock.path),
            "reason": lock.reason,
        }

    @staticmethod
    def _is_primary_repair_backup(path: Path, db_name: str) -> bool:
        return (
            path.is_file()
            and path.name.startswith(db_name + ".")
            and "-backup-" in path.name
            and not path.name.endswith(("-wal", "-shm", ".repair.json"))
        )

    def list_repair_backups(self) -> list[dict[str, Any]]:
        """Return raw repair backups preserved beside the session database."""
        out: list[dict[str, Any]] = []
        for backup in sorted(self.db.parent.glob(f"{self.db.name}.*-backup-*")):
            if not self._is_primary_repair_backup(backup, self.db.name):
                continue
            status_path = backup.with_name(backup.name + ".repair.json")
            status: dict[str, Any] = {}
            if status_path.exists():
                try:
                    loaded = json.loads(status_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        status = loaded
                except (OSError, ValueError):
                    status = {}
            out.append(
                {
                    "backup_path": str(backup),
                    "bytes": backup.stat().st_size,
                    "kind": status.get("kind"),
                    "result": status.get("result"),
                    "status_path": str(status_path) if status_path.exists() else "",
                    "wal_path": (
                        str(backup.with_name(backup.name + "-wal"))
                        if backup.with_name(backup.name + "-wal").exists()
                        else ""
                    ),
                    "shm_path": (
                        str(backup.with_name(backup.name + "-shm"))
                        if backup.with_name(backup.name + "-shm").exists()
                        else ""
                    ),
                }
            )
        return out

    def restore_repair_backup(self, backup_path: str | Path) -> dict[str, Any]:
        """Restore a raw repair backup under the session repair lock."""
        backup = Path(backup_path).expanduser()
        if not backup.is_absolute():
            backup = self.db.parent / backup
        backup = backup.resolve()
        db_dir = self.db.parent.resolve()
        try:
            backup.relative_to(db_dir)
        except ValueError:
            return {
                "error": "backup is outside the session database directory",
                "restored": False,
            }
        if self.read_only:
            return {"error": "session store is read-only", "restored": False}
        if not self._is_primary_repair_backup(backup, self.db.name):
            return {"error": "not a primary session repair backup", "restored": False}

        repair_lock = self._acquire_repair_file_lock(self.db, "manual-restore")
        if not repair_lock.acquired:
            return {
                "error": repair_lock.reason or "repair lock unavailable",
                "repair_lock": self._repair_lock_status(repair_lock),
                "restored": False,
            }

        restored_sidecars: list[dict[str, str]] = []
        status_path = self.db.with_name(f"{self.db.name}.manual-restore-{time.time_ns()}.json")
        preserved_current = None
        try:
            preserved_current = self._copy_db_repair_backup(self.db, "pre-manual-restore")
            for suffix in ("-wal", "-shm"):
                backup_sidecar = backup.with_name(backup.name + suffix)
                live_sidecar = self.db.with_name(self.db.name + suffix)
                if backup_sidecar.exists():
                    shutil.copy2(backup_sidecar, live_sidecar)
                    action = "restored_from_backup"
                else:
                    try:
                        live_sidecar.unlink()
                        action = "removed_live_sidecar"
                    except FileNotFoundError:
                        action = "absent"
                restored_sidecars.append(
                    {
                        "action": action,
                        "backup_path": str(backup_sidecar),
                        "live_path": str(live_sidecar),
                    }
                )
            shutil.copy2(backup, self.db)
            result = {
                "backup_path": str(backup),
                "pre_restore_backup_path": str(preserved_current or ""),
                "repair_lock": self._repair_lock_status(repair_lock),
                "restored": True,
                "sidecars": restored_sidecars,
                "status_path": str(status_path),
            }
            self._write_repair_status(
                status_path,
                {
                    "kind": "manual_repair_backup_restore",
                    "result": "restored",
                    "schema_version": 1,
                    **result,
                },
            )
            return result
        except OSError as exc:
            result = {
                "backup_path": str(backup),
                "error": str(exc),
                "pre_restore_backup_path": str(preserved_current or ""),
                "repair_lock": self._repair_lock_status(repair_lock),
                "restored": False,
                "sidecars": restored_sidecars,
                "status_path": str(status_path),
            }
            self._write_repair_status(
                status_path,
                {
                    "kind": "manual_repair_backup_restore",
                    "result": "not_restored",
                    "schema_version": 1,
                    **result,
                },
            )
            return result
        finally:
            self._release_repair_file_lock(repair_lock)

    @staticmethod
    def _write_repair_status(status_path: Path, status: dict[str, Any]) -> bool:
        try:
            status_path.write_text(
                json.dumps(status, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return True
        except OSError:
            return False

    def _write_wal_sidecar_repair_status(
        self,
        *,
        backup_path: Path,
        sidecars: list[Path],
        trigger_error: BaseException,
        result: str,
        main_db_health: str | None,
        final_health: str | None = "not_run",
        failure_stage: str | None = None,
        header_rewritten: bool = False,
        deleted_sidecars: set[Path] | None = None,
        wal_evidence: dict[str, Any] | None = None,
        sqlite_replay: dict[str, Any] | None = None,
        repair_lock: _SessionRepairFileLock | None = None,
    ) -> None:
        deleted_sidecars = deleted_sidecars or set()
        sidecar_status = []
        for sidecar in sidecars:
            suffix = sidecar.name.removeprefix(self.db.name)
            sidecar_status.append(
                {
                    "live_path": str(sidecar),
                    "backup_path": str(backup_path.with_name(backup_path.name + suffix)),
                    "action": (
                        "deleted_from_live_db"
                        if sidecar in deleted_sidecars
                        else "left_in_live_db"
                    ),
                }
            )
        status_path = backup_path.with_name(backup_path.name + ".repair.json")
        replay_attempted = bool(sqlite_replay and sqlite_replay.get("attempted"))
        replay_result = str((sqlite_replay or {}).get("result") or "")
        replay_applied = result == "recovered_by_sqlite_wal_checkpoint"
        self._write_repair_status(
            status_path,
            {
                "backup_path": str(backup_path),
                "database_path": str(self.db),
                "failure_stage": failure_stage,
                "final_health": "ok" if final_health is None else final_health,
                "header_action": (
                    "forced_rollback_journal"
                    if header_rewritten
                    else "not_changed"
                ),
                "kind": "wal_sidecar_repair",
                "main_db_probe_without_sidecars": (
                    "ok" if main_db_health is None else main_db_health
                ),
                "policy": {
                    "manual_wal_frame_decode": "not_attempted",
                    "manual_restore": "available_via_restore_repair_backup",
                    "recovery_source": (
                        "sqlite_checkpointed_wal_backup"
                        if replay_applied
                        else "checkpointed_main_db"
                    ),
                    "safe_replay": (
                        "attempted_with_sqlite_checkpoint_on_backup_copy"
                        if replay_attempted
                        else f"not_attempted_{replay_result or 'no_wal_sidecar'}"
                    ),
                    "safe_replay_possible": replay_applied,
                    "uncheckpointed_wal_frames": (
                        "applied_by_sqlite_checkpoint"
                        if replay_applied
                        else "preserved_in_backup_only"
                    ),
                },
                "repair_lock": self._repair_lock_status(repair_lock),
                "result": result,
                "schema_version": 1,
                "sidecars": sidecar_status,
                "sqlite_replay": sqlite_replay or {"attempted": False},
                "status_path": str(status_path),
                "trigger_error": str(trigger_error)[:400],
                "wal_evidence": wal_evidence or {},
            },
        )

    @staticmethod
    def _malformed_db_repair_policy() -> dict[str, Any]:
        return {
            "recovery_source": "canonical_sessions_and_messages_rows_when_readable",
            "automatic_rebuild": {
                "messages_fts": "dropped_and_recreated_from_messages",
                "corrupt_session_snapshot": "rebuilt_from_messages_on_load",
            },
            "not_rebuilt": {
                "main_db_pages": "not_attempted",
                "wal_frames": "not_replayed",
            },
            "unrecoverable_result": (
                "automatic repair stops after preserving a raw backup; "
                "manual restore or external SQLite recovery is required"
            ),
        }

    @staticmethod
    def _canonical_rebuild_evidence(db_path: Path) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "sessions_table": "unknown",
            "messages_table": "unknown",
            "session_rows": None,
            "message_rows": None,
            "corrupt_session_snapshots": None,
            "corrupt_snapshot_session_ids": [],
            "message_rows_without_session": None,
        }
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(db_path, timeout=1)
            conn.row_factory = sqlite3.Row
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            has_sessions = "sessions" in tables
            has_messages = "messages" in tables
            evidence["sessions_table"] = "present" if has_sessions else "missing"
            evidence["messages_table"] = "present" if has_messages else "missing"

            if has_sessions:
                session_rows = conn.execute(
                    "SELECT id, data FROM sessions ORDER BY updated_at, id"
                ).fetchall()
                corrupt_ids: list[str] = []
                for row in session_rows:
                    try:
                        payload = json.loads(row["data"] or "{}")
                    except (TypeError, ValueError):
                        corrupt_ids.append(str(row["id"]))
                        continue
                    if not isinstance(payload, dict):
                        corrupt_ids.append(str(row["id"]))
                evidence["session_rows"] = len(session_rows)
                evidence["corrupt_session_snapshots"] = len(corrupt_ids)
                evidence["corrupt_snapshot_session_ids"] = corrupt_ids[:20]

            if has_messages:
                evidence["message_rows"] = conn.execute(
                    "SELECT COUNT(*) FROM messages"
                ).fetchone()[0]

            if has_sessions and has_messages:
                evidence["message_rows_without_session"] = conn.execute(
                    "SELECT COUNT(*) FROM messages m "
                    "LEFT JOIN sessions s ON s.id=m.session_id "
                    "WHERE s.id IS NULL"
                ).fetchone()[0]
        except (sqlite3.DatabaseError, OSError) as exc:
            evidence["error"] = str(exc)[:400]
        finally:
            if conn is not None:
                conn.close()
        return evidence

    def _write_malformed_db_repair_status(
        self,
        *,
        backup_path: Path,
        trigger_error: BaseException,
        result: str,
        strategy: str | None,
        final_health: str | None = "not_run",
        failure_stage: str | None = None,
        evidence: dict[str, Any] | None = None,
        repair_lock: _SessionRepairFileLock | None = None,
    ) -> None:
        status_path = backup_path.with_name(backup_path.name + ".repair.json")
        self._write_repair_status(
            status_path,
            {
                "backup_path": str(backup_path),
                "database_path": str(self.db),
                "evidence": evidence or {},
                "failure_stage": failure_stage,
                "final_health": "ok" if final_health is None else final_health,
                "kind": "malformed_sqlite_repair",
                "policy": self._malformed_db_repair_policy(),
                "repair_lock": self._repair_lock_status(repair_lock),
                "result": result,
                "schema_version": 1,
                "status_path": str(status_path),
                "strategy": strategy,
                "trigger_error": str(trigger_error)[:400],
            },
        )

    @staticmethod
    def _sqlite_sidecars(db_path: Path) -> tuple[Path, Path]:
        return (
            db_path.with_name(db_path.name + "-wal"),
            db_path.with_name(db_path.name + "-shm"),
        )

    @staticmethod
    def _wal_frame_accounting(wal_path: Path) -> dict[str, Any]:
        accounting: dict[str, Any] = {
            "complete_frame_count": 0,
            "commit_frame_count": 0,
            "format": "missing",
            "trailing_bytes": 0,
        }
        try:
            size = wal_path.stat().st_size
            accounting["bytes"] = size
            if size < 32:
                accounting["format"] = "too_short"
                accounting["trailing_bytes"] = size
                return accounting
            with wal_path.open("rb") as fh:
                header = fh.read(32)
                magic = int.from_bytes(header[0:4], "big")
                page_size = int.from_bytes(header[8:12], "big") or 1024
                valid_header = magic in {0x377F0682, 0x377F0683}
                accounting.update(
                    {
                        "format": "wal" if valid_header else "unknown",
                        "magic": f"0x{magic:08x}",
                        "page_size": page_size,
                        "sequence": int.from_bytes(header[12:16], "big"),
                        "valid_header": valid_header,
                    }
                )
                if not valid_header or page_size <= 0:
                    return accounting
                frame_size = page_size + 24
                frame_bytes = max(size - 32, 0)
                complete_frames = frame_bytes // frame_size
                accounting["complete_frame_count"] = complete_frames
                accounting["frame_size"] = frame_size
                accounting["trailing_bytes"] = frame_bytes % frame_size
                commit_frames = 0
                last_commit_pages = 0
                last_page_number = 0
                for frame_index in range(complete_frames):
                    fh.seek(32 + frame_index * frame_size)
                    frame_header = fh.read(8)
                    if len(frame_header) < 8:
                        break
                    last_page_number = int.from_bytes(frame_header[0:4], "big")
                    commit_pages = int.from_bytes(frame_header[4:8], "big")
                    if commit_pages:
                        commit_frames += 1
                        last_commit_pages = commit_pages
                accounting["commit_frame_count"] = commit_frames
                accounting["last_commit_db_pages"] = last_commit_pages
                accounting["last_frame_page_number"] = last_page_number
        except OSError as exc:
            accounting["format"] = "unreadable"
            accounting["error"] = str(exc)[:400]
        return accounting

    @classmethod
    def _sqlite_sidecar_evidence(cls, db_path: Path) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        for suffix, key in (("-wal", "wal"), ("-shm", "shm")):
            path = db_path.with_name(db_path.name + suffix)
            item: dict[str, Any] = {"exists": path.exists(), "path": str(path)}
            if path.exists():
                try:
                    item["bytes"] = path.stat().st_size
                except OSError as exc:
                    item["error"] = str(exc)[:400]
                if suffix == "-wal":
                    item["frame_accounting"] = cls._wal_frame_accounting(path)
            evidence[key] = item
        return evidence

    @staticmethod
    def _db_health_error(db_path: Path, *, require_sessions: bool = False) -> str | None:
        conn = sqlite3.connect(db_path, timeout=1)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            problems = [str(row[0]) for row in rows if row and str(row[0]).lower() != "ok"]
            if problems:
                return "; ".join(problems[:3])
            if require_sessions:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
                ).fetchone()
                if row is None:
                    return "missing sessions table in main database"
                conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
            return None
        except sqlite3.DatabaseError as exc:
            return str(exc)
        finally:
            conn.close()

    @classmethod
    def _main_db_health_without_sidecars(cls, db_path: Path) -> str | None:
        with tempfile.TemporaryDirectory(prefix="aegis-session-db-probe-") as tmp:
            probe_path = Path(tmp) / db_path.name
            shutil.copy2(db_path, probe_path)
            return cls._db_health_error(probe_path, require_sessions=True)

    @classmethod
    def _checkpoint_wal_backup_copy(
        cls,
        backup_path: Path,
        *,
        live_db_name: str,
    ) -> tuple[Path | None, dict[str, Any]]:
        report: dict[str, Any] = {
            "attempted": False,
            "checkpoint_result": None,
            "checkpointed_db_path": "",
            "error": None,
            "final_health": "not_run",
            "result": "not_attempted",
            "source_backup_path": str(backup_path),
        }
        backup_wal = backup_path.with_name(backup_path.name + "-wal")
        if not backup_wal.exists():
            report["result"] = "no_wal_sidecar"
            return None, report
        wal_accounting = cls._wal_frame_accounting(backup_wal)
        report["wal_frame_accounting"] = wal_accounting
        if int(wal_accounting.get("complete_frame_count") or 0) <= 0:
            report["result"] = "no_complete_wal_frames"
            return None, report

        report["attempted"] = True
        try:
            with tempfile.TemporaryDirectory(prefix="aegis-session-wal-replay-") as tmp:
                scratch_db = Path(tmp) / live_db_name
                shutil.copy2(backup_path, scratch_db)
                shutil.copy2(backup_wal, scratch_db.with_name(scratch_db.name + "-wal"))
                backup_shm = backup_path.with_name(backup_path.name + "-shm")
                if backup_shm.exists():
                    shutil.copy2(backup_shm, scratch_db.with_name(scratch_db.name + "-shm"))
                conn: sqlite3.Connection | None = None
                try:
                    conn = sqlite3.connect(scratch_db, timeout=1)
                    conn.execute("PRAGMA busy_timeout=1000")
                    conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
                    checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    report["checkpoint_result"] = list(checkpoint) if checkpoint else None
                finally:
                    if conn is not None:
                        conn.close()
                final_health = cls._db_health_error(scratch_db, require_sessions=True)
                report["final_health"] = "ok" if final_health is None else final_health
                if final_health is not None:
                    report["result"] = "checkpointed_copy_unhealthy"
                    return None, report
                with tempfile.NamedTemporaryFile(
                    dir=backup_path.parent,
                    prefix=f".{live_db_name}.wal-replay-",
                    suffix=".db",
                    delete=False,
                ) as tmp_out:
                    checkpointed_path = Path(tmp_out.name)
                shutil.copy2(scratch_db, checkpointed_path)
                report["checkpointed_db_path"] = str(checkpointed_path)
                report["result"] = "checkpointed"
                return checkpointed_path, report
        except (OSError, sqlite3.DatabaseError) as exc:
            report["error"] = str(exc)[:400]
            report["result"] = "failed"
            return None, report

    @staticmethod
    def _replace_file_from_path(src: Path, dst: Path) -> None:
        with tempfile.NamedTemporaryFile(
            dir=dst.parent,
            prefix=f".{dst.name}.replace-",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            shutil.copy2(src, tmp_path)
            os.replace(tmp_path, dst)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _force_rollback_journal_header(db_path: Path) -> bool:
        try:
            with db_path.open("r+b") as fh:
                header = fh.read(100)
                if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
                    return False
                if header[18:20] == b"\x01\x01":
                    return True
                if header[18:20] != b"\x02\x02":
                    return False
                # SQLite header bytes 18/19 are read/write format versions:
                # 2 means WAL, 1 means rollback journal.
                fh.seek(18)
                fh.write(b"\x01\x01")
                fh.flush()
                os.fsync(fh.fileno())
            return True
        except OSError:
            return False

    def _repair_wal_sidecars(self, exc: BaseException) -> bool:
        """Recover when WAL/SHM sidecars are corrupt but the main DB is sound."""
        if self.read_only or not self.db.exists() or not self._is_wal_sidecar_error(exc):
            return False
        sidecars = [path for path in self._sqlite_sidecars(self.db) if path.exists()]
        if not sidecars:
            return False
        repair_lock = self._acquire_repair_file_lock(self.db, "wal-sidecar")
        if not repair_lock.acquired:
            return False
        try:
            wal_evidence = self._sqlite_sidecar_evidence(self.db)
            backup_path = self._copy_db_repair_backup(self.db, "wal-sidecar")
            if backup_path is None:
                return False
            checkpointed_path: Path | None = None
            sqlite_replay: dict[str, Any] = {"attempted": False}
            try:
                checkpointed_path, sqlite_replay = self._checkpoint_wal_backup_copy(
                    backup_path,
                    live_db_name=self.db.name,
                )
                if checkpointed_path is not None:
                    deleted_sidecars: set[Path] = set()
                    for sidecar in sidecars:
                        try:
                            sidecar.unlink()
                            deleted_sidecars.add(sidecar)
                        except FileNotFoundError:
                            deleted_sidecars.add(sidecar)
                        except OSError:
                            self._write_wal_sidecar_repair_status(
                                backup_path=backup_path,
                                sidecars=sidecars,
                                trigger_error=exc,
                                result="not_repaired",
                                main_db_health="not_run",
                                failure_stage="delete_live_sidecars_after_replay",
                                deleted_sidecars=deleted_sidecars,
                                wal_evidence=wal_evidence,
                                sqlite_replay=sqlite_replay,
                                repair_lock=repair_lock,
                            )
                            return False
                    self._replace_file_from_path(checkpointed_path, self.db)
                    header_rewritten = self._force_rollback_journal_header(self.db)
                    final_health = self._db_health_error(self.db, require_sessions=True)
                    repaired = final_health is None
                    self._write_wal_sidecar_repair_status(
                        backup_path=backup_path,
                        sidecars=sidecars,
                        trigger_error=exc,
                        result=(
                            "recovered_by_sqlite_wal_checkpoint"
                            if repaired
                            else "not_repaired"
                        ),
                        main_db_health="not_run",
                        final_health=final_health,
                        failure_stage=None if repaired else "post_replay_health_probe",
                        header_rewritten=header_rewritten,
                        deleted_sidecars=deleted_sidecars,
                        wal_evidence=wal_evidence,
                        sqlite_replay=sqlite_replay,
                        repair_lock=repair_lock,
                    )
                    return repaired
            finally:
                if checkpointed_path is not None:
                    try:
                        checkpointed_path.unlink()
                    except FileNotFoundError:
                        pass

            main_db_health = self._main_db_health_without_sidecars(self.db)
            if main_db_health is not None:
                self._write_wal_sidecar_repair_status(
                    backup_path=backup_path,
                    sidecars=sidecars,
                    trigger_error=exc,
                    result="not_repaired",
                    main_db_health=main_db_health,
                    failure_stage="main_db_probe_without_sidecars",
                    wal_evidence=wal_evidence,
                    sqlite_replay=sqlite_replay,
                    repair_lock=repair_lock,
                )
                return False
            deleted_sidecars: set[Path] = set()
            for sidecar in sidecars:
                try:
                    sidecar.unlink()
                    deleted_sidecars.add(sidecar)
                except FileNotFoundError:
                    deleted_sidecars.add(sidecar)
                except OSError:
                    self._write_wal_sidecar_repair_status(
                        backup_path=backup_path,
                        sidecars=sidecars,
                        trigger_error=exc,
                        result="not_repaired",
                        main_db_health=main_db_health,
                        failure_stage="delete_live_sidecars",
                        deleted_sidecars=deleted_sidecars,
                        wal_evidence=wal_evidence,
                        sqlite_replay=sqlite_replay,
                        repair_lock=repair_lock,
                    )
                    return False
            if not self._force_rollback_journal_header(self.db):
                self._write_wal_sidecar_repair_status(
                    backup_path=backup_path,
                    sidecars=sidecars,
                    trigger_error=exc,
                    result="not_repaired",
                    main_db_health=main_db_health,
                    failure_stage="force_rollback_journal_header",
                    deleted_sidecars=deleted_sidecars,
                    wal_evidence=wal_evidence,
                    sqlite_replay=sqlite_replay,
                    repair_lock=repair_lock,
                )
                return False
            final_health = self._db_health_error(self.db, require_sessions=True)
            repaired = final_health is None
            self._write_wal_sidecar_repair_status(
                backup_path=backup_path,
                sidecars=sidecars,
                trigger_error=exc,
                result=(
                    "recovered_from_checkpointed_main_db"
                    if repaired
                    else "not_repaired"
                ),
                main_db_health=main_db_health,
                final_health=final_health,
                failure_stage=None if repaired else "post_repair_health_probe",
                header_rewritten=True,
                deleted_sidecars=deleted_sidecars,
                wal_evidence=wal_evidence,
                sqlite_replay=sqlite_replay,
                repair_lock=repair_lock,
            )
            return repaired
        finally:
            self._release_repair_file_lock(repair_lock)

    def _repair_malformed_sqlite_schema(self, exc: BaseException) -> bool:
        """Drop derived FTS schema rows when sqlite_schema itself is malformed."""
        if self.read_only or not self.db.exists() or not self._is_malformed_db_error(exc):
            return False
        repair_lock = self._acquire_repair_file_lock(self.db, "malformed-sqlite")
        if not repair_lock.acquired:
            return False
        try:
            if not _claim_malformed_repair_attempt(self.db):
                return False
            backup_path = self._copy_db_repair_backup(self.db, "malformed-schema")
            if backup_path is None:
                return False
            conn: sqlite3.Connection | None = None
            try:
                conn = sqlite3.connect(self.db)
                conn.execute("PRAGMA writable_schema=ON")
                conn.execute("DELETE FROM sqlite_master WHERE name LIKE 'messages_fts%'")
                conn.execute("PRAGMA writable_schema=OFF")
                conn.commit()
                try:
                    conn.execute("VACUUM")
                except sqlite3.DatabaseError:
                    pass
                final_health = self._db_health_error(self.db, require_sessions=True)
                repaired = final_health is None
                self._write_malformed_db_repair_status(
                    backup_path=backup_path,
                    trigger_error=exc,
                    result=(
                        "recovered_by_dropping_derived_fts_schema"
                        if repaired
                        else "not_repaired"
                    ),
                    strategy="drop_fts_schema",
                    final_health=final_health,
                    failure_stage=None if repaired else "post_repair_health_probe",
                    evidence=self._canonical_rebuild_evidence(self.db),
                    repair_lock=repair_lock,
                )
                return repaired
            except sqlite3.DatabaseError as repair_exc:
                self._write_malformed_db_repair_status(
                    backup_path=backup_path,
                    trigger_error=exc,
                    result="not_repaired",
                    strategy="drop_fts_schema",
                    final_health=str(repair_exc),
                    failure_stage="sqlite_master_surgery",
                    evidence=self._canonical_rebuild_evidence(self.db),
                    repair_lock=repair_lock,
                )
                return False
            finally:
                if conn is not None:
                    try:
                        conn.execute("PRAGMA writable_schema=OFF")
                    except sqlite3.DatabaseError:
                        pass
                    conn.close()
        finally:
            self._release_repair_file_lock(repair_lock)

    def _conn(self) -> sqlite3.Connection:
        # 30s busy timeout + WAL so concurrent gateway threads don't hit
        # "database is locked" under load.
        if self.read_only:
            conn = sqlite3.connect(f"file:{self.db}?mode=ro", timeout=30, uri=True)
        else:
            conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=1000")
            if not self.read_only:
                self._apply_wal_with_fallback(conn)
        except sqlite3.OperationalError:
            pass
        finally:
            try:
                conn.execute("PRAGMA busy_timeout=30000")
            except sqlite3.OperationalError:
                pass
        return conn

    def _probe_session_db(self) -> None:
        with sqlite3.connect(self.db, timeout=1) as c:
            c.execute("SELECT COUNT(*) FROM sessions").fetchone()

    @staticmethod
    def _table_exists(c: sqlite3.Connection, name: str) -> bool:
        row = c.execute(
            "SELECT 1 FROM sqlite_master WHERE name=? AND type IN ('table','view')",
            (name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _row_int(row: sqlite3.Row, key: str, default: int) -> int:
        try:
            value = row[key]
            if value is None:
                return default
            return int(value)
        except (KeyError, TypeError, ValueError, IndexError):
            return default

    def _ensure_message_columns(self, c: sqlite3.Connection) -> set[str]:
        columns = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
        additions = {
            "session_id": "TEXT",
            "message_index": "INTEGER",
            "role": "TEXT",
            "content": "TEXT",
            "tool_name": "TEXT",
            "tool_call_id": "TEXT",
            "created_at": "TEXT",
            "profile": "TEXT DEFAULT ''",
            "active": "INTEGER DEFAULT 1",
            "compacted": "INTEGER DEFAULT 0",
        }
        for name, ddl in additions.items():
            if name not in columns:
                c.execute(f"ALTER TABLE messages ADD COLUMN {name} {ddl}")
                columns.add(name)
        return columns

    @staticmethod
    def _message_table_has_unique_key(c: sqlite3.Connection) -> bool:
        for row in c.execute("PRAGMA index_list(messages)").fetchall():
            keys = row.keys()
            is_unique = bool(row["unique"] if "unique" in keys else row[2])
            if not is_unique:
                continue
            index_name = row["name"] if "name" in keys else row[1]
            safe_name = str(index_name).replace('"', '""')
            cols = [
                info["name"] if "name" in info.keys() else info[2]
                for info in c.execute(f'PRAGMA index_info("{safe_name}")').fetchall()
            ]
            if cols == ["session_id", "message_index"]:
                return True
        return False

    def _repair_legacy_messages_table(self, c: sqlite3.Connection) -> bool:
        columns_info = {
            r["name"]: r for r in c.execute("PRAGMA table_info(messages)").fetchall()
        }
        id_info = columns_info.get("id")
        id_is_pk = id_info is not None and self._row_int(id_info, "pk", 0) > 0
        unique_key = self._message_table_has_unique_key(c)
        null_key_row = c.execute(
            "SELECT 1 FROM messages "
            "WHERE session_id IS NULL OR message_index IS NULL LIMIT 1"
        ).fetchone()
        if id_is_pk and unique_key and null_key_row is None:
            return False

        self._copy_db_repair_backup(self.db, "legacy-messages")
        try:
            rows = c.execute(
                "SELECT rowid AS _repair_rowid, * FROM messages "
                "ORDER BY session_id, message_index, rowid"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = c.execute("SELECT rowid AS _repair_rowid, * FROM messages").fetchall()

        legacy_name = f"messages_legacy_repair_{int(time.time() * 1000)}"
        safe_legacy = legacy_name.replace('"', '""')
        c.execute(f'ALTER TABLE messages RENAME TO "{safe_legacy}"')
        c.execute(self._MESSAGES_TABLE_SQL)

        used_indexes: dict[str, set[int]] = {}
        next_index: dict[str, int] = {}
        for row in rows:
            keys = set(row.keys())
            session_id = str(row["session_id"] or "") if "session_id" in keys else ""
            if not session_id:
                continue
            used = used_indexes.setdefault(session_id, set())
            idx = None
            if "message_index" in keys:
                try:
                    idx = int(row["message_index"])
                except (TypeError, ValueError):
                    idx = None
            if idx is None or idx in used:
                idx = next_index.get(session_id, 0)
            while idx in used:
                idx += 1
            used.add(idx)
            next_index[session_id] = max(next_index.get(session_id, 0), idx + 1)

            c.execute(
                """INSERT INTO messages (
                       session_id, message_index, role, content, tool_name,
                       tool_call_id, created_at, profile, active, compacted
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    idx,
                    row["role"] if "role" in keys else None,
                    row["content"] if "content" in keys else None,
                    row["tool_name"] if "tool_name" in keys else None,
                    row["tool_call_id"] if "tool_call_id" in keys else None,
                    row["created_at"] if "created_at" in keys else None,
                    row["profile"] if "profile" in keys and row["profile"] is not None else self.profile,
                    self._row_int(row, "active", 1),
                    self._row_int(row, "compacted", 0),
                ),
            )
        c.execute(f'DROP TABLE "{safe_legacy}"')
        return True

    def _reset_fts_schema(self, c: sqlite3.Connection) -> None:
        try:
            c.execute("DROP TABLE IF EXISTS messages_fts")
        except sqlite3.DatabaseError:
            c.execute("PRAGMA writable_schema=ON")
            c.execute("DELETE FROM sqlite_master WHERE name LIKE 'messages_fts%'")
            c.execute("PRAGMA writable_schema=OFF")
        c.execute(self._FTS_TABLE_SQL)
        self._fts = True

    def _ensure_fts_schema(self, c: sqlite3.Connection) -> tuple[bool, bool]:
        try:
            existed = self._table_exists(c, "messages_fts")
            c.execute(self._FTS_TABLE_SQL)
            fts_cols = {
                r["name"] if "name" in r.keys() else r[1]
                for r in c.execute("PRAGMA table_info(messages_fts)").fetchall()
            }
            if not self._FTS_REQUIRED_COLUMNS <= fts_cols:
                self._reset_fts_schema(c)
                return True, True
            self._fts = True
            return True, not existed
        except sqlite3.OperationalError:
            self._fts = False
            return False, False

    def _insert_fts_from_message_rows(
        self,
        c: sqlite3.Connection,
        *,
        session_id: str | None = None,
    ) -> None:
        where = (
            "m.role IN ('user','assistant') "
            "AND m.content IS NOT NULL AND m.content != '' "
            "AND (COALESCE(m.active, 1)=1 OR COALESCE(m.compacted, 0)=1)"
        )
        params: tuple[Any, ...] = ()
        if session_id is not None:
            where += " AND m.session_id=?"
            params = (session_id,)
        c.execute(
            "INSERT INTO messages_fts "
            "(content, session_id, title, role, ts, message_id, profile) "
            "SELECT m.content, m.session_id, "
            "COALESCE(NULLIF(s.title, ''), m.session_id), "
            "COALESCE(m.role, ''), "
            "COALESCE(m.created_at, s.updated_at, ''), "
            "m.id, COALESCE(NULLIF(m.profile, ''), s.profile, '') "
            "FROM messages m LEFT JOIN sessions s ON s.id=m.session_id "
            f"WHERE {where} "
            "ORDER BY m.session_id, m.message_index",
            params,
        )

    def _rebuild_fts_from_message_rows(self, c: sqlite3.Connection) -> None:
        c.execute("DELETE FROM messages_fts")
        self._insert_fts_from_message_rows(c)

    def _repair_fts_after_failure(self, c: sqlite3.Connection) -> bool:
        if self.read_only:
            self._fts = False
            return False
        try:
            self._copy_db_repair_backup(self.db, "fts-repair")
            self._reset_fts_schema(c)
            self._rebuild_fts_from_message_rows(c)
            self._fts = True
            return True
        except sqlite3.DatabaseError:
            self._fts = False
            return False

    def _refresh_fts_for_session(self, c: sqlite3.Connection, session_id: str) -> None:
        if not getattr(self, "_fts", False):
            return
        try:
            c.execute("DELETE FROM messages_fts WHERE session_id=?", (session_id,))
            self._insert_fts_from_message_rows(c, session_id=session_id)
        except sqlite3.Error:
            self._repair_fts_after_failure(c)

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                       id TEXT PRIMARY KEY,
                       title TEXT,
                       created_at TEXT,
                       updated_at TEXT,
                       summary TEXT,
                       data TEXT
                   )"""
            )
            # add new columns to pre-existing tables
            cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
            if "summary" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
            if "parent_id" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN parent_id TEXT")
            if "profile" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN profile TEXT DEFAULT ''")
            if "archived" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN archived INTEGER DEFAULT 0")
            if "source" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT ''")
                # Backfill: tag the internal forked sessions already on disk so they
                # drop out of the default (user-facing) list immediately.
                c.execute(
                    "UPDATE sessions SET source='internal' WHERE title IN "
                    "('[review]','[curator]','memory review','skill review',"
                    "'combined review','curator review')"
                )
            c.execute(self._MESSAGES_TABLE_SQL)
            self._ensure_message_columns(c)
            messages_repaired = self._repair_legacy_messages_table(c)
            c.execute(
                """CREATE TABLE IF NOT EXISTS compression_locks (
                       session_id TEXT PRIMARY KEY,
                       holder TEXT NOT NULL,
                       acquired_at REAL NOT NULL,
                       expires_at REAL NOT NULL
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_compression_locks_expires "
                      "ON compression_locks(expires_at)")
            # full-text index over message content (graceful if FTS5 is unavailable)
            fts_available, rebuild_fts = self._ensure_fts_schema(c)
            if fts_available and rebuild_fts:
                self._rebuild_message_indexes(c)
            elif fts_available and messages_repaired:
                self._rebuild_fts_from_message_rows(c)

    def _rebuild_message_indexes(self, c: sqlite3.Connection) -> None:
        rows = c.execute("SELECT * FROM sessions ORDER BY updated_at").fetchall()
        for row in rows:
            try:
                session = Session.from_row(row)
            except Exception:  # noqa: BLE001
                continue
            session.profile = session.profile or self.profile
            for i, m in enumerate(session.messages):
                c.execute(
                    """INSERT OR IGNORE INTO messages (
                           session_id, message_index, role, content, tool_name,
                           tool_call_id, created_at, profile
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session.id,
                        i,
                        m.role,
                        m.content,
                        m.name,
                        m.tool_call_id,
                        session.updated_at,
                        session.profile,
                    ),
                )
        if getattr(self, "_fts", False):
            self._rebuild_fts_from_message_rows(c)

    @staticmethod
    def _message_from_message_row(row: sqlite3.Row) -> Message:
        role = str(row["role"] or "")
        content = str(row["content"] or "")
        if role == "tool":
            return Message.tool(
                str(row["tool_call_id"] or ""),
                str(row["tool_name"] or ""),
                content,
            )
        return Message(
            role=role,
            content=content,
            tool_call_id=row["tool_call_id"],
            name=row["tool_name"],
        )

    def _hydrate_appended_message_rows(self, session: Session, c: sqlite3.Connection) -> Session:
        """Recover durable tail rows appended after the last JSON snapshot."""
        rows = self._active_message_rows(session.id, c)
        for row in rows:
            try:
                index = int(row["message_index"])
            except (TypeError, ValueError):
                continue
            if index < len(session.messages):
                continue
            if index != len(session.messages):
                # A gap means the full protocol context is not reconstructable
                # from row-only storage; keep the JSON snapshot authoritative.
                continue
            session.messages.append(self._message_from_message_row(row))
        return session

    def _active_message_rows(self, session_id: str, c: sqlite3.Connection) -> list[sqlite3.Row]:
        try:
            return c.execute(
                "SELECT message_index, role, content, tool_name, tool_call_id "
                "FROM messages WHERE session_id=? AND COALESCE(active, 1)=1 "
                "ORDER BY message_index",
                (session_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return c.execute(
                "SELECT message_index, role, content, tool_name, tool_call_id "
                "FROM messages WHERE session_id=? ORDER BY message_index",
                (session_id,),
            ).fetchall()

    def _session_from_message_rows(
        self,
        row: sqlite3.Row,
        c: sqlite3.Connection,
        *,
        error: Exception,
    ) -> Session:
        """Recover a loadable session when the JSON snapshot is unreadable."""
        rows = self._active_message_rows(str(row["id"]), c)
        session = Session(
            id=row["id"],
            title=row["title"] or row["id"],
            created_at=row["created_at"] or now_iso(),
            updated_at=row["updated_at"] or now_iso(),
            parent_id=row["parent_id"] if "parent_id" in row.keys() else None,
            profile=row["profile"] if "profile" in row.keys() else self.profile,
        )
        session.messages = [self._message_from_message_row(message_row) for message_row in rows]
        session.meta["_session_repair"] = {
            "kind": "corrupt_snapshot",
            "source": "message_rows",
            "repaired_at": now_iso(),
            "row_count": len(rows),
            "error": str(error)[:200],
            "policy": {
                "rebuilt_fields": ["messages"],
                "not_rebuilt": ["todos", "non_repair_meta"],
                "snapshot_json": "unreadable",
            },
        }
        if not self.read_only:
            repaired = session.to_row()
            repaired["summary"] = ""
            repaired["source"] = _session_source(session.meta)
            c.execute(
                """UPDATE sessions SET
                       title=:title, updated_at=:updated_at, summary=:summary,
                       parent_id=:parent_id, profile=:profile, data=:data,
                       source=:source
                   WHERE id=:id""",
                repaired,
            )
        return session

    def _session_from_row(self, row: sqlite3.Row, c: sqlite3.Connection) -> Session:
        try:
            session = Session.from_row(row)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            return self._session_from_message_rows(row, c, error=exc)
        return self._hydrate_appended_message_rows(session, c)

    def save(self, session: Session) -> None:
        session.profile = session.profile or self.profile
        incoming_generation = _gateway_generation(session.meta)
        with self._conn() as c:
            if incoming_generation is not None:
                current = c.execute("SELECT data FROM sessions WHERE id=?", (session.id,)).fetchone()
                current_generation = _row_gateway_generation(current)
                if current_generation is not None and incoming_generation < current_generation:
                    return
            row = session.to_row()
            session.updated_at = row["updated_at"]
            row["summary"] = session.meta.get("summary", "")
            row["source"] = _session_source(session.meta)
            c.execute(
                """INSERT INTO sessions (id, title, created_at, updated_at, summary, parent_id, profile, data, source)
                   VALUES (:id, :title, :created_at, :updated_at, :summary, :parent_id, :profile, :data, :source)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, updated_at=excluded.updated_at,
                     summary=excluded.summary, parent_id=excluded.parent_id,
                     profile=excluded.profile, data=excluded.data, source=excluded.source""",
                row,
            )
            c.execute("DELETE FROM messages WHERE session_id=? AND COALESCE(active, 1)=1 AND message_index>=?",
                      (session.id, len(session.messages)))
            for i, m in enumerate(session.messages):
                c.execute(
                    """INSERT INTO messages (
                           session_id, message_index, role, content, tool_name,
                           tool_call_id, created_at, profile, active, compacted
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                       ON CONFLICT(session_id, message_index) DO UPDATE SET
                           role=excluded.role, content=excluded.content,
                           tool_name=excluded.tool_name, tool_call_id=excluded.tool_call_id,
                           created_at=excluded.created_at, profile=excluded.profile,
                           active=1, compacted=0""",
                    (
                        session.id,
                        i,
                        m.role,
                        m.content,
                        m.name,
                        m.tool_call_id,
                        session.updated_at,
                        session.profile,
                    ),
                )
            self._refresh_fts_for_session(c, session.id)

    def archive_and_compact(self, session: Session) -> int:
        """Persist an in-place compaction while preserving old rows for recovery.

        The session snapshot and active message rows become the compacted live
        transcript. The previous active rows are retained under the same session
        id as inactive ``compacted`` rows so a restart loads the compacted context
        without losing the pre-compaction audit trail.
        """
        if self.read_only:
            return 0
        session.profile = session.profile or self.profile
        with self._conn() as c:
            row = session.to_row()
            session.updated_at = row["updated_at"]
            row["summary"] = session.meta.get("summary", "")
            row["source"] = _session_source(session.meta)
            c.execute(
                """INSERT INTO sessions (id, title, created_at, updated_at, summary, parent_id, profile, data, source)
                   VALUES (:id, :title, :created_at, :updated_at, :summary, :parent_id, :profile, :data, :source)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, updated_at=excluded.updated_at,
                     summary=excluded.summary, parent_id=excluded.parent_id,
                     profile=excluded.profile, data=excluded.data, source=excluded.source""",
                row,
            )
            active_rows = c.execute(
                "SELECT id FROM messages WHERE session_id=? AND COALESCE(active, 1)=1 "
                "ORDER BY message_index",
                (session.id,),
            ).fetchall()
            if active_rows:
                min_row = c.execute(
                    "SELECT MIN(message_index) AS min_idx FROM messages WHERE session_id=?",
                    (session.id,),
                ).fetchone()
                min_index = int(min_row["min_idx"] if min_row and min_row["min_idx"] is not None else 0)
                archive_start = min(min_index, 0) - len(active_rows)
                for offset, old in enumerate(active_rows):
                    c.execute(
                        "UPDATE messages SET message_index=?, active=0, compacted=1 WHERE id=?",
                        (archive_start + offset, old["id"]),
                    )
            for i, m in enumerate(session.messages):
                c.execute(
                    """INSERT INTO messages (
                           session_id, message_index, role, content, tool_name,
                           tool_call_id, created_at, profile, active, compacted
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                       ON CONFLICT(session_id, message_index) DO UPDATE SET
                           role=excluded.role, content=excluded.content,
                           tool_name=excluded.tool_name, tool_call_id=excluded.tool_call_id,
                           created_at=excluded.created_at, profile=excluded.profile,
                           active=1, compacted=0""",
                    (
                        session.id,
                        i,
                        m.role,
                        m.content,
                        m.name,
                        m.tool_call_id,
                        session.updated_at,
                        session.profile,
                    ),
                )
            self._refresh_fts_for_session(c, session.id)
        return len(session.messages)

    def append_messages(
        self,
        session: Session,
        messages: list[Message],
        *,
        start_index: int | None = None,
    ) -> list[int]:
        """Append/update durable message rows without rewriting the session JSON.

        This is the crash-evidence path for in-progress tool results. The later
        whole-session save can rewrite the snapshot normally, but each tool
        result has already reached SQLite as an independent row.
        """
        if self.read_only or not messages:
            return []
        session.profile = session.profile or self.profile
        index = len(session.messages) if start_index is None else int(start_index)
        updated_at = now_iso()
        row_ids: list[int] = []
        with self._conn() as c:
            existing = c.execute("SELECT id, data FROM sessions WHERE id=?", (session.id,)).fetchone()
            if existing is None:
                row = session.to_row()
                session.updated_at = row["updated_at"]
                row["summary"] = session.meta.get("summary", "")
                row["source"] = _session_source(session.meta)
                c.execute(
                    """INSERT INTO sessions (
                           id, title, created_at, updated_at, summary,
                           parent_id, profile, data, source
                       )
                       VALUES (
                           :id, :title, :created_at, :updated_at, :summary,
                           :parent_id, :profile, :data, :source
                       )""",
                    row,
                )
                updated_at = session.updated_at
            else:
                session.updated_at = updated_at
                snapshot_messages = 0
                try:
                    snapshot = json.loads(existing["data"] or "{}")
                    snapshot_messages = len(snapshot.get("messages", []) or [])
                except Exception:  # noqa: BLE001
                    snapshot_messages = 0
                if snapshot_messages < index:
                    row = session.to_row()
                    session.updated_at = row["updated_at"]
                    row["summary"] = session.meta.get("summary", "")
                    row["source"] = _session_source(session.meta)
                    c.execute(
                        """UPDATE sessions SET
                               title=:title, updated_at=:updated_at,
                               summary=:summary, parent_id=:parent_id,
                               profile=:profile, data=:data, source=:source
                           WHERE id=:id""",
                        row,
                    )
                    updated_at = session.updated_at
                else:
                    c.execute(
                        """UPDATE sessions SET
                               title=?, updated_at=?, summary=?, parent_id=?,
                               profile=?, source=?
                           WHERE id=?""",
                        (
                            session.title,
                            updated_at,
                            session.meta.get("summary", ""),
                            session.parent_id,
                            session.profile,
                            _session_source(session.meta),
                            session.id,
                        ),
                    )
            for offset, message in enumerate(messages):
                message_index = index + offset
                c.execute(
                    """INSERT INTO messages (
                           session_id, message_index, role, content, tool_name,
                           tool_call_id, created_at, profile, active, compacted
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                       ON CONFLICT(session_id, message_index) DO UPDATE SET
                           role=excluded.role, content=excluded.content,
                           tool_name=excluded.tool_name,
                           tool_call_id=excluded.tool_call_id,
                           created_at=excluded.created_at, profile=excluded.profile,
                           active=1, compacted=0""",
                    (
                        session.id,
                        message_index,
                        message.role,
                        message.content,
                        message.name,
                        message.tool_call_id,
                        updated_at,
                        session.profile,
                    ),
                )
                row = c.execute(
                    "SELECT id FROM messages WHERE session_id=? AND message_index=?",
                    (session.id, message_index),
                ).fetchone()
                if row is not None:
                    row_ids.append(int(row["id"]))
            if any(message.role in ("user", "assistant") and message.content for message in messages):
                self._refresh_fts_for_session(c, session.id)
        return row_ids

    def try_acquire_compression_lock(self, session_id: str, holder: str,
                                     ttl_seconds: float = 300.0) -> bool:
        """Atomically acquire a per-session compression lock.

        Expired locks are reclaimed, and a current holder may reacquire its own lock.
        """
        if not session_id or not holder:
            return False
        now = time.time()
        expires_at = now + max(0.001, float(ttl_seconds or 300.0))
        try:
            with self._conn() as c:
                c.execute(
                    "DELETE FROM compression_locks WHERE session_id=? AND expires_at < ?",
                    (session_id, now),
                )
                row = c.execute(
                    "SELECT holder FROM compression_locks WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row and row["holder"] != holder:
                    return False
                if row:
                    c.execute(
                        "UPDATE compression_locks SET acquired_at=?, expires_at=? "
                        "WHERE session_id=? AND holder=?",
                        (now, expires_at, session_id, holder),
                    )
                else:
                    c.execute(
                        "INSERT INTO compression_locks "
                        "(session_id, holder, acquired_at, expires_at) VALUES (?,?,?,?)",
                        (session_id, holder, now, expires_at),
                    )
                return True
        except sqlite3.Error:
            return False

    def release_compression_lock(self, session_id: str, holder: str) -> None:
        """Release a compression lock only if owned by ``holder``."""
        if not session_id or not holder:
            return
        try:
            with self._conn() as c:
                c.execute(
                    "DELETE FROM compression_locks WHERE session_id=? AND holder=?",
                    (session_id, holder),
                )
        except sqlite3.Error:
            pass

    def get_compression_lock_holder(self, session_id: str) -> str | None:
        """Return the current non-expired compression lock holder, if any."""
        if not session_id:
            return None
        try:
            with self._conn() as c:
                row = c.execute(
                    "SELECT holder FROM compression_locks WHERE session_id=? AND expires_at >= ?",
                    (session_id, time.time()),
                ).fetchone()
                return row["holder"] if row else None
        except sqlite3.Error:
            return None

    def load(self, sid: str) -> Session | None:
        with self._conn() as c:
            # exact id, then title match, then prefix
            for q, arg in (
                ("SELECT * FROM sessions WHERE id=?", sid),
                ("SELECT * FROM sessions WHERE title=?", sid),
                ("SELECT * FROM sessions WHERE id LIKE ? ORDER BY updated_at DESC", sid + "%"),
            ):
                row = c.execute(q, (arg,)).fetchone()
                if row:
                    return self._session_from_row(row, c)
        return None

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _title_base(value: str) -> tuple[str, bool]:
        import re
        match = re.match(r"^(.*?) #(\d+)$", value)
        if match:
            return match.group(1), True
        return value, False

    def resolve_title_to_tip(self, title: str) -> Session | None:
        """Resolve a human title, preferring latest numbered continuations and compression tips."""
        title = (title or "").strip()
        if not title:
            return None
        base, numbered = self._title_base(title)
        with self._conn() as c:
            if numbered:
                rows = c.execute(
                    "SELECT * FROM sessions WHERE title=? ORDER BY updated_at DESC, created_at DESC",
                    (title,),
                ).fetchall()
            else:
                escaped = self._escape_like(base)
                rows = c.execute(
                    "SELECT * FROM sessions WHERE title=? OR title LIKE ? ESCAPE '\\' "
                    "ORDER BY updated_at DESC, created_at DESC",
                    (base, f"{escaped} #%"),
                ).fetchall()
        for row in rows:
            sess = Session.from_row(row)
            return self.compression_tip(sess.id) or sess
        return None

    def latest(self) -> Session | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 1").fetchone()
            return self._session_from_row(row, c) if row else None

    def list(
        self,
        limit: int = 50,
        *,
        include_internal: bool = False,
        source: str | None = None,
        include_children: bool = True,
    ) -> list[dict]:
        """Recent sessions, newest first. Internal forked sessions (memory/skill review,
        curator) are hidden by default so the user-facing list shows real conversations
        only; pass ``include_internal=True`` to see everything."""
        filters: list[str] = []
        params: list[Any] = []
        source_name = str(source or "").strip().lower()
        if source_name and source_name not in {"all", "*"}:
            if source_name in {"user", "default", "public"}:
                filters.append("COALESCE(source,'')=''")
            else:
                filters.append("COALESCE(source,'')=?")
                params.append(source_name)
        elif not include_internal:
            filters.append("COALESCE(source,'') NOT IN ('internal','review','curator')")
        if not include_children:
            filters.append("(parent_id IS NULL OR parent_id='')")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, created_at, updated_at, parent_id, profile, source "
                f"FROM sessions {where} ORDER BY updated_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def _load_exact(self, sid: str) -> Session | None:
        if not sid:
            return None
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            return self._session_from_row(row, c) if row else None

    def mark_resume_pending(self, sid: str, reason: str = "restart_timeout") -> bool:
        """Mark a gateway session as interrupted but resumable after a restart.

        This mirrors AEGIS' ``resume_pending`` flag while preserving the current
        AEGIS transcript/session id.  The flag is cleared after the next
        successful gateway turn.
        """
        if self.read_only:
            return False
        session = self._load_exact(sid)
        if session is None:
            return False
        session.meta["resume_pending"] = True
        session.meta["resume_reason"] = str(reason or "restart_timeout")
        session.meta["last_resume_marked_at"] = now_iso()
        self.save(session)
        return True

    def clear_resume_pending(self, sid: str) -> bool:
        """Clear a session's resume-pending recovery flag."""
        if self.read_only:
            return False
        session = self._load_exact(sid)
        if session is None or not session.meta.get("resume_pending"):
            return False
        for key in ("resume_pending", "resume_reason", "last_resume_marked_at"):
            session.meta.pop(key, None)
        self.save(session)
        return True

    def list_resume_pending(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return sessions marked for gateway restart recovery, newest first."""
        pending: list[dict[str, Any]] = []
        max_rows = max(0, int(limit or 0))
        if max_rows == 0:
            return pending
        with self._conn() as c:
            rows = c.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
        for row in rows:
            session = Session.from_row(row)
            if not session.meta.get("resume_pending"):
                continue
            pending.append({
                "id": session.id,
                "title": session.title,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "resume_reason": session.meta.get("resume_reason"),
                "last_resume_marked_at": session.meta.get("last_resume_marked_at"),
            })
            if len(pending) >= max_rows:
                break
        return pending

    def children(self, parent_id: str) -> list[dict]:
        """Sessions forked from ``parent_id`` (lineage chain), oldest first."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, created_at, updated_at, profile FROM sessions WHERE parent_id=? ORDER BY created_at",
                (parent_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def fork(self, parent: Session, *, carry_summary: bool = True) -> Session:
        """Create a child session linked to ``parent`` (e.g. when compaction splits a long
        session). The child keeps the system prompt + a summary breadcrumb of the parent."""
        child = Session.create(title=parent.title, parent_id=parent.id)
        system = [m for m in parent.messages if m.role == "system"][:1]
        child.messages = list(system)
        if carry_summary:
            child.meta["forked_from"] = parent.id
            child.meta["summary"] = parent.meta.get("summary", "")
        child.meta["_rebuild_system_prompt"] = True
        for key in ("runtime", "runtime_controls", "model", "provider"):
            if key in parent.meta:
                value = parent.meta[key]
                child.meta[key] = dict(value) if isinstance(value, dict) else value
        self.save(parent)
        self.save(child)
        return child

    def branch(
        self,
        parent: Session,
        *,
        title: str | None = None,
        reason: str = "manual_branch",
        surface: str = "",
    ) -> Session:
        """Create a user-visible branch without making resume follow it as a tip."""
        child = self.fork(parent)
        if title:
            child.title = title
        root = parent.meta.get("lineage_root") or parent.parent_id or parent.id
        depth = int(parent.meta.get("lineage_depth", 0) or 0) + 1
        child.meta["_branched_from"] = parent.id
        child.meta["branch_reason"] = str(reason or "manual_branch")
        child.meta["creator_kind"] = child.meta.get("creator_kind") or "branch"
        child.meta["lineage_root"] = root
        child.meta["lineage_depth"] = depth
        if surface:
            child.meta["branch_surface"] = surface
        parent.meta.setdefault("child_sessions", [])
        if child.id not in parent.meta["child_sessions"]:
            parent.meta["child_sessions"].append(child.id)
        self.save(parent)
        self.save(child)
        return child

    def delete(self, sid: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM sessions WHERE id=?", (sid,))
            c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            if getattr(self, "_fts", False):
                c.execute("DELETE FROM messages_fts WHERE session_id=?", (sid,))  # no orphan rows
            return cur.rowcount > 0

    def session_stamp(self, sid: str) -> dict[str, Any] | None:
        """Cheap invalidation stamp for warm agent/session caches."""
        with self._conn() as c:
            row = c.execute("SELECT id, updated_at FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                return None
            count = c.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=? AND COALESCE(active, 1)=1",
                (sid,),
            ).fetchone()[0]
        return {"id": row["id"], "updated_at": row["updated_at"], "message_count": int(count or 0)}

    def message_count(self, sid: str) -> int:
        stamp = self.session_stamp(sid)
        return int((stamp or {}).get("message_count", 0) or 0)

    def set_archived(self, sid: str, archived: bool = True) -> bool:
        """Archive (or unarchive) a session — archived sessions are kept but excluded
        from pruning and from the default session list (AEGIS set_session_archived)."""
        with self._conn() as c:
            cur = c.execute("UPDATE sessions SET archived=? WHERE id=?",
                            (1 if archived else 0, sid))
            return cur.rowcount > 0

    def prune_empty(self, *, older_than_days: float = 0.0, dry_run: bool = True,
                    protect: "tuple[str, ...]" = ()) -> list[str]:
        """Delete 'ghost' sessions — ones with no user/assistant turns (only a system
        prompt, or nothing) — that aren't archived or protected. Lifecycle cleanup so the
        store doesn't accumulate empty sessions (AEGIS prune_empty_ghost_sessions). With
        ``older_than_days`` only prunes sessions untouched for that long. Returns the ids
        pruned (or that would be pruned, when ``dry_run``)."""
        from datetime import datetime, timedelta, timezone
        guard = set(protect or ())
        cutoff = ((datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
                  if older_than_days > 0 else None)
        victims: list[str] = []
        with self._conn() as c:
            for r in c.execute("SELECT id, updated_at, archived FROM sessions").fetchall():
                sid = r["id"]
                if sid in guard or (r["archived"] or 0):
                    continue
                if cutoff and (r["updated_at"] or "") > cutoff:
                    continue
                substantive = c.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id=? AND role IN ('user','assistant')",
                    (sid,),
                ).fetchone()[0]
                if substantive == 0:
                    victims.append(sid)
        if not dry_run:
            for sid in victims:
                self.delete(sid)
        return victims

    def optimize(self) -> dict[str, float | int]:
        """Compact the session store without changing session data."""
        if self.read_only:
            raise RuntimeError("cannot optimize a read-only session store")
        before = self.db.stat().st_size if self.db.exists() else 0
        optimized = 0
        conn = self._conn()
        try:
            conn.isolation_level = None
            if getattr(self, "_fts", False):
                try:
                    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('optimize')")
                    optimized = 1
                except sqlite3.OperationalError:
                    optimized = 0
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass
            conn.execute("VACUUM")
        finally:
            conn.close()
        after = self.db.stat().st_size if self.db.exists() else 0
        return {"fts_indexes": optimized, "before_bytes": before, "after_bytes": after}

    def search(self, query: str, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, title, updated_at FROM sessions WHERE data LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        import re
        stop = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "we", "i",
                "you", "it", "is", "are", "was", "were", "do", "did", "does", "done",
                "have", "has", "had", "what", "when", "how", "who", "our", "my", "your",
                "this", "that", "about", "with", "like", "so", "far", "me", "us"}
        return [t for t in re.findall(r"[A-Za-z0-9_]{2,}", query)
                if t.lower() not in stop][:12]

    @classmethod
    def _fts_query(cls, query: str) -> str:
        """Natural-language query -> FTS5 OR-of-terms (ranked). A whole-query phrase
        match meant 'what did we decide about X' could never hit anything."""
        toks = cls._query_terms(query)
        if not toks:
            return '"' + query.replace('"', "") + '"'
        return " OR ".join(f'"{t}"' for t in toks)

    def _message_row_id(self, session_id: str, index: int) -> int | None:
        try:
            with self._conn() as c:
                row = c.execute(
                    "SELECT id FROM messages WHERE session_id=? AND message_index=?",
                    (session_id, index),
                ).fetchone()
            return int(row["id"]) if row else None
        except (sqlite3.OperationalError, TypeError, ValueError):
            return None

    def _message_index_for_row_id(self, row_id: int | None, *, session_id: str | None = None) -> int | None:
        if row_id is None:
            return None
        try:
            with self._conn() as c:
                if session_id:
                    row = c.execute(
                        "SELECT message_index FROM messages WHERE id=? AND session_id=?",
                        (int(row_id), session_id),
                    ).fetchone()
                else:
                    row = c.execute("SELECT message_index FROM messages WHERE id=?", (int(row_id),)).fetchone()
            return int(row["message_index"]) if row else None
        except (sqlite3.OperationalError, TypeError, ValueError):
            return None

    def _message_owner_for_row_id(self, row_id: int | None) -> tuple[str, int] | None:
        if row_id is None:
            return None
        try:
            with self._conn() as c:
                row = c.execute(
                    "SELECT session_id, message_index FROM messages WHERE id=?",
                    (int(row_id),),
                ).fetchone()
            if not row:
                return None
            return str(row["session_id"]), int(row["message_index"])
        except (sqlite3.OperationalError, TypeError, ValueError):
            return None

    def _message_payload(self, session_id: str, index: int, message: Message,
                         *, anchor_id: int | None = None) -> dict:
        payload: dict[str, Any] = {"id": index, "role": message.role, "content": message.content}
        row_id = self._message_row_id(session_id, index)
        if row_id is not None:
            payload["message_row_id"] = row_id
        if message.name:
            payload["tool_name"] = message.name
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            payload["tool_calls"] = [tc.to_dict() for tc in message.tool_calls]
        if anchor_id is not None and index == anchor_id:
            payload["anchor"] = True
        return payload

    def _visible_messages(self, sess: Session, *, roles: set[str] | None = None) -> list[dict]:
        roles = roles or {"user", "assistant", "tool"}
        return [
            self._message_payload(sess.id, i, message)
            for i, message in enumerate(sess.messages)
            if message.role in roles
        ]

    @classmethod
    def _first_matching_message_id(cls, sess: Session, query: str,
                                   roles: set[str] | None = None) -> int | None:
        roles = roles or {"user", "assistant"}
        terms = [t.lower() for t in cls._query_terms(query)] or [query.lower()]
        for i, message in enumerate(sess.messages):
            if message.role not in roles:
                continue
            content = (message.content or "").lower()
            if any(term in content for term in terms):
                return i
        return None

    def search_messages(self, query: str, limit: int = 8) -> list[dict]:
        """Cross-session recall: ranked message snippets across past sessions (FTS5)."""
        if getattr(self, "_fts", False):
            try:
                match = self._fts_query(query)
                with self._conn() as c:
                    fts_cols = {r[1] for r in c.execute("PRAGMA table_info(messages_fts)").fetchall()}
                    if {"message_id", "profile"} <= fts_cols:
                        rows = c.execute(
                            "SELECT session_id, title, role, ts, message_id, profile, "
                            "snippet(messages_fts, 0, '[', ']', '…', 12) AS snip "
                            "FROM messages_fts WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
                            (match, limit),
                        ).fetchall()
                    else:
                        rows = c.execute(
                            "SELECT session_id, title, role, ts, NULL AS message_id, '' AS profile, "
                            "snippet(messages_fts, 0, '[', ']', '…', 12) AS snip "
                            "FROM messages_fts WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
                            (match, limit),
                        ).fetchall()
                out = []
                for r in rows:
                    sess = self.load(r["session_id"])
                    row_message_id = r["message_id"]
                    msg_index = self._message_index_for_row_id(row_message_id)
                    if msg_index is None:
                        msg_index = self._first_matching_message_id(sess, query) if sess else None
                    try:
                        stable_row_id = int(row_message_id) if row_message_id not in (None, "") else None
                    except (TypeError, ValueError):
                        stable_row_id = None
                    out.append({
                        "session": r["session_id"],
                        "title": r["title"],
                        "when": r["ts"],
                        "role": r["role"],
                        "snippet": r["snip"].replace("\n", " "),
                        "message_id": msg_index,
                        "message_row_id": stable_row_id,
                        "profile": r["profile"] or self.profile,
                    })
                return out
            except sqlite3.OperationalError:
                if not self.read_only:
                    try:
                        with self._conn() as c:
                            self._repair_fts_after_failure(c)
                    except sqlite3.Error:
                        pass
        # Fallback without FTS: scan recent sessions for ANY query term.
        toks = [t.lower() for t in self._query_terms(query)] or [query.lower()]
        out: list[dict] = []
        with self._conn() as c:
            rows = c.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                             (limit * 5,)).fetchall()
        for row in rows:
            sess = Session.from_row(row)
            for msg_index, m in enumerate(sess.messages):
                low = m.content.lower() if m.content else ""
                hit = next((t for t in toks if t in low), None)
                if m.role in ("user", "assistant") and hit:
                    char_index = low.find(hit)
                    snippet = m.content[max(0, char_index - 80):char_index + 160].strip().replace("\n", " ")
                    out.append({"session": sess.id, "title": sess.title,
                                "when": sess.updated_at, "role": m.role,
                                "snippet": snippet, "message_id": msg_index,
                                "message_row_id": self._message_row_id(sess.id, msg_index),
                                "profile": sess.profile})
                    break
            if len(out) >= limit:
                break
        return out

    def _resolve_session(self, sid: str) -> Session | None:
        if not (sid or "").strip():
            return None
        sess = self.load(sid)
        if sess and sess.id == sid:
            return sess
        title_match = self.resolve_title_to_tip(sid)
        if title_match:
            return title_match
        if sess:
            return sess
        needle = (sid or "").strip().lower()
        if not needle:
            return None
        for row in self.list(200):
            row_id = str(row.get("id", ""))
            title = str(row.get("title", ""))
            if row_id.startswith(sid) or title.lower() == needle:
                return self.load(row_id)
        return None

    def _lineage_root(self, sid: str | None) -> str | None:
        cur = sid
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            sess = self.load(cur)
            if not sess or not sess.parent_id:
                return cur
            cur = sess.parent_id
        return cur

    @staticmethod
    def _is_branch_or_delegate_child(sess: Session) -> bool:
        meta = sess.meta if isinstance(sess.meta, dict) else {}
        creator = str(meta.get("creator_kind") or "").strip()
        source = str(meta.get("source") or meta.get("surface") or "").strip()
        return bool(
            meta.get("_branched_from")
            or meta.get("branch_reason")
            or meta.get("branch_surface")
            or "branch" in creator
            or meta.get("_delegate_from")
            or meta.get("subagent_id")
            or creator == "subagent"
            or source == "tool"
        )

    @classmethod
    def _is_compression_continuation(cls, parent: Session, child: Session) -> bool:
        if cls._is_branch_or_delegate_child(child):
            return False
        parent_meta = parent.meta if isinstance(parent.meta, dict) else {}
        child_meta = child.meta if isinstance(child.meta, dict) else {}
        compression_reasons = {"compression", "manual_compression"}
        return (
            str(parent_meta.get("end_reason") or "") in compression_reasons
            or str(child_meta.get("creator_kind") or "") in compression_reasons
            or str(child_meta.get("parent_end_reason") or "") in compression_reasons
            or str(child_meta.get("reason") or "") == "context_compaction"
        )

    @staticmethod
    def _compression_child_sort_key(child: Session) -> tuple[int, str, str, str]:
        meta = child.meta if isinstance(child.meta, dict) else {}
        end_reason = str(meta.get("end_reason") or "")
        if end_reason in {"compression", "manual_compression"}:
            state_rank = 0
        elif not end_reason:
            state_rank = 1
        else:
            state_rank = 2
        return (state_rank, child.updated_at or "", child.created_at or "", child.id)

    @classmethod
    def _prefer_compression_child(cls, child: Session, current: Session | None) -> bool:
        if current is None:
            return True
        child_key = cls._compression_child_sort_key(child)
        current_key = cls._compression_child_sort_key(current)
        if child_key[0] != current_key[0]:
            return child_key[0] < current_key[0]
        return child_key[1:] > current_key[1:]

    def compression_tip(self, sid: str | None) -> Session | None:
        """Walk compression-created child sessions from ``sid`` to the live tip."""
        if not sid:
            return None
        cur = self._resolve_session(sid)
        seen: set[str] = set()
        while cur and cur.id not in seen:
            seen.add(cur.id)
            next_child: Session | None = None
            for child_row in self.children(cur.id):
                child = self.load(child_row["id"])
                if child and self._is_compression_continuation(cur, child):
                    if self._prefer_compression_child(child, next_child):
                        next_child = child
            if next_child is None:
                return cur
            cur = next_child
        return cur

    def resolve_resume_session_id(self, sid: str | None) -> str | None:
        """Resolve a resume target to the live transcript-bearing session.

        Mirrors the reference resume contract: context compression may end a parent
        and continue in a child, so resuming the parent id/title/prefix should
        land on the live compression tip. Branch, delegate, tool, and subagent
        children remain separate conversations and never hijack resume.
        """
        if not sid:
            return sid
        base = self._resolve_session(str(sid))
        if base is None:
            return sid
        tip = self.compression_tip(base.id) or base
        current = tip
        best = current if len([m for m in current.messages if m.role != "system"]) else None
        seen: set[str] = {current.id}
        for _ in range(32):
            candidates: list[Session] = []
            for child_row in self.children(current.id):
                child = self.load(child_row["id"])
                if child is None or child.id in seen:
                    continue
                if self._is_branch_or_delegate_child(child):
                    continue
                candidates.append(child)
            if not candidates:
                break
            candidates.sort(key=lambda child: (child.updated_at or "", child.created_at or "", child.id), reverse=True)
            current = candidates[0]
            seen.add(current.id)
            if len([m for m in current.messages if m.role != "system"]):
                best = current
        return (best or tip).id

    @staticmethod
    def _lineage_origin(sess: Session) -> dict[str, Any]:
        meta = sess.meta if isinstance(sess.meta, dict) else {}
        kind = str(meta.get("creator_kind") or "").strip()
        surface = str(meta.get("surface") or "").strip()
        if meta.get("cron_job_id"):
            kind = "cron"
        elif meta.get("background_task_id"):
            kind = "background"
        elif meta.get("subagent_id"):
            kind = "subagent"
        elif meta.get("platform") or meta.get("gateway"):
            kind = "gateway"
        elif not kind and surface:
            kind = surface
        elif not kind:
            kind = "local"
        allowed = (
            "surface",
            "creator_kind",
            "platform",
            "gateway",
            "chat_id",
            "thread_id",
            "user_id",
            "message_id",
            "cron_job_id",
            "cron_schedule",
            "background_task_id",
            "subagent_id",
            "agent_type",
            "parent_session_id",
            "parent_end_reason",
            "lineage_root",
            "lineage_depth",
            "last_run_id",
            "last_trace_id",
            "trace_id",
        )
        fields = {key: _jsonable_meta_value(meta[key]) for key in allowed if key in meta}
        fields["kind"] = kind
        return fields

    @classmethod
    def _lineage_node(cls, sess: Session, *, depth: int = 0, relation: str = "") -> dict[str, Any]:
        return {
            "id": sess.id,
            "title": sess.title,
            "created_at": sess.created_at,
            "updated_at": sess.updated_at,
            "parent_id": sess.parent_id,
            "profile": sess.profile,
            "relation": relation,
            "depth": depth,
            "message_count": len([m for m in sess.messages if m.role != "system"]),
            "origin": cls._lineage_origin(sess),
        }

    def lineage(self, sid: str) -> dict[str, Any]:
        """Return a dashboard/API-friendly lineage graph for one session.

        The graph is derived from existing ``parent_id`` rows and safe metadata,
        so it works for old stores without a migration.
        """
        current = self._resolve_session(sid)
        if current is None:
            return {"found": False, "id": sid, "ok": False, "error": f"session_id not found: {sid}"}

        with self._conn() as c:
            rows = c.execute("SELECT * FROM sessions ORDER BY created_at, updated_at").fetchall()
        sessions = {Session.from_row(row).id: Session.from_row(row) for row in rows}
        sessions[current.id] = current
        children_by_parent: dict[str, list[Session]] = {}
        for sess in sessions.values():
            if sess.parent_id:
                children_by_parent.setdefault(sess.parent_id, []).append(sess)
        for children in children_by_parent.values():
            children.sort(key=lambda child: (child.created_at, child.updated_at, child.id))

        warnings: list[dict[str, Any]] = []
        ancestors_reversed: list[Session] = []
        seen: set[str] = {current.id}
        parent_id = current.parent_id
        while parent_id:
            parent = sessions.get(parent_id) or self.load(parent_id)
            if parent is None:
                warnings.append({
                    "code": "missing_parent_session",
                    "session_id": current.id,
                    "parent_id": parent_id,
                })
                break
            if parent.id in seen:
                warnings.append({
                    "code": "lineage_cycle",
                    "session_id": current.id,
                    "at": parent.id,
                })
                break
            ancestors_reversed.append(parent)
            seen.add(parent.id)
            parent_id = parent.parent_id
        ancestors = list(reversed(ancestors_reversed))
        root = ancestors[0] if ancestors else current

        descendants: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        visited_descendants: set[str] = {current.id}

        def walk(parent: Session, depth: int) -> None:
            for child in children_by_parent.get(parent.id, []):
                edges.append({"from": parent.id, "to": child.id, "kind": "parent_child"})
                if child.id in visited_descendants:
                    warnings.append({
                        "code": "lineage_cycle",
                        "session_id": parent.id,
                        "at": child.id,
                    })
                    continue
                visited_descendants.add(child.id)
                descendants.append(self._lineage_node(child, depth=depth, relation="descendant"))
                walk(child, depth + 1)

        walk(current, 1)
        for parent, child in zip(ancestors, ancestors[1:] + [current], strict=False):
            edges.append({"from": parent.id, "to": child.id, "kind": "parent_child"})

        direct_children = [
            self._lineage_node(child, depth=1, relation="child")
            for child in children_by_parent.get(current.id, [])
        ]
        nodes = [
            *[self._lineage_node(sess, depth=i - len(ancestors), relation="ancestor")
              for i, sess in enumerate(ancestors)],
            self._lineage_node(current, depth=0, relation="current"),
            *descendants,
        ]
        return {
            "found": True,
            "ok": not warnings,
            "id": current.id,
            "root_id": root.id,
            "current": self._lineage_node(current, depth=0, relation="current"),
            "ancestors": [
                self._lineage_node(sess, depth=i - len(ancestors), relation="ancestor")
                for i, sess in enumerate(ancestors)
            ],
            "parent": self._lineage_node(ancestors[-1], depth=-1, relation="parent") if ancestors else None,
            "children": direct_children,
            "descendants": descendants,
            "nodes": nodes,
            "edges": edges,
            "warnings": warnings,
            "summary": {
                "ancestor_count": len(ancestors),
                "child_count": len(direct_children),
                "descendant_count": len(descendants),
                "edge_count": len(edges),
                "warning_count": len(warnings),
            },
        }

    def browse_sessions(self, limit: int = 10, *, current_session_id: str | None = None) -> dict:
        """Browse shape: recent sessions without needing a query."""
        limit = max(1, min(int(limit or 10), 50))
        current_root = self._lineage_root(current_session_id)
        results = []
        for row in self.list(max(limit * 10, 100)):
            sess = self.load(row["id"])
            if not sess:
                continue
            if sess.parent_id:
                continue
            display = self.compression_tip(sess.id) or sess
            root = self._lineage_root(display.id)
            if current_root and root == current_root:
                continue
            preview = next((m.content for m in display.messages
                            if m.role in ("user", "assistant") and m.content), "")
            row_out = {
                "session_id": display.id,
                "title": display.title,
                "profile": display.profile,
                "created_at": sess.created_at,
                "updated_at": display.updated_at,
                "message_count": len([m for m in display.messages if m.role != "system"]),
                "preview": preview[:240],
            }
            if display.id != sess.id:
                row_out["parent_session_id"] = sess.id
                row_out["lineage_root_id"] = sess.id
            results.append(row_out)
            if len(results) >= limit:
                break
        return {"success": True, "mode": "browse", "results": results, "count": len(results)}

    def read_session(self, sid: str, *, head: int = 20, tail: int = 10) -> dict:
        """Read shape: bounded transcript dump by session id/title/prefix."""
        sess = self._resolve_session(sid)
        if not sess:
            return {"success": False, "mode": "read", "error": f"session_id not found: {sid}"}
        shaped = self._visible_messages(sess)
        total = len(shaped)
        truncated = total > head + tail
        messages = shaped[:head] + shaped[-tail:] if truncated else shaped
        out = {
            "success": True,
            "mode": "read",
            "session_id": sess.id,
            "session_meta": _session_meta_view(sess),
            "message_count": total,
            "truncated": truncated,
            "messages": messages,
        }
        if truncated:
            out["message"] = (
                f"Session has {total} messages; showing first {head} + last {tail}. "
                "Use around_message_id with any shown id to scroll."
            )
        return out

    def messages_around(self, sid: str, around_message_id: int, *, window: int = 5,
                        current_session_id: str | None = None,
                        anchor_is_row_id: bool = False) -> dict:
        """Scroll shape: a bounded message window centered on an anchor id."""
        sess = self._resolve_session(sid)
        if not sess:
            return {"success": False, "mode": "scroll", "error": f"session_id not found: {sid}"}
        requested_session_id = sess.id
        current_root = self._lineage_root(current_session_id)
        if current_root and self._lineage_root(sess.id) == current_root:
            return {
                "success": False,
                "mode": "scroll",
                "error": "anchor lives in the current session lineage (already in active context)",
            }
        try:
            anchor = int(around_message_id)
        except (TypeError, ValueError):
            return {"success": False, "mode": "scroll", "error": "around_message_id must be an integer"}
        window = max(1, min(int(window or 5), 20))
        rebind_message = ""
        if anchor_is_row_id:
            owner = self._message_owner_for_row_id(anchor)
            if owner and self._lineage_root(owner[0]) == self._lineage_root(sess.id):
                owner_session_id, owner_index = owner
                if owner_session_id != sess.id:
                    rebound = self.load(owner_session_id)
                    if rebound:
                        sess = rebound
                        rebind_message = (
                            f"around_message_row_id {around_message_id} lives in {owner_session_id}; "
                            "rebound transparently"
                        )
                anchor = owner_index
        shaped = self._visible_messages(sess)
        positions = {m["id"]: i for i, m in enumerate(shaped)}
        if not anchor_is_row_id and anchor not in positions:
            owner = self._message_owner_for_row_id(anchor)
            if owner and self._lineage_root(owner[0]) == self._lineage_root(sess.id):
                owner_session_id, owner_index = owner
                rebound = self.load(owner_session_id)
                if rebound:
                    sess = rebound
                    anchor = owner_index
                    shaped = self._visible_messages(sess)
                    positions = {m["id"]: i for i, m in enumerate(shaped)}
                    if owner_session_id != requested_session_id:
                        rebind_message = (
                            f"around_message_id {around_message_id} lives in {owner_session_id}; "
                            "rebound transparently"
                        )
        if current_root and self._lineage_root(sess.id) == current_root:
            return {
                "success": False,
                "mode": "scroll",
                "error": "anchor lives in the current session lineage (already in active context)",
            }
        if anchor not in positions:
            return {
                "success": False,
                "mode": "scroll",
                "session_id": sess.id,
                "error": f"around_message_id {anchor} not in session",
            }
        pos = positions[anchor]
        start = max(0, pos - window)
        end = min(len(shaped), pos + window + 1)
        messages = [
            {**m, **({"anchor": True} if m["id"] == anchor else {})}
            for m in shaped[start:end]
        ]
        out = {
            "success": True,
            "mode": "scroll",
            "session_id": sess.id,
            "around_message_id": anchor,
            "session_meta": _session_meta_view(sess),
            "window": window,
            "messages": messages,
            "messages_before": start,
            "messages_after": len(shaped) - end,
        }
        if sess.id != requested_session_id:
            out["rebound_from_session_id"] = requested_session_id
        if rebind_message:
            out["message"] = rebind_message
        return out

    def discover_sessions(self, query: str, limit: int = 3, *,
                          role_filter: list[str] | None = None,
                          sort: str | None = None,
                          current_session_id: str | None = None) -> dict:
        """Discovery shape: search plus message windows and bookends."""
        limit = max(1, min(int(limit or 3), 10))
        roles = {r for r in (role_filter or ["user", "assistant"]) if r}
        hits = self.search_messages(query, limit=limit * 8)
        if sort == "newest":
            hits.sort(key=lambda h: h.get("when") or "", reverse=True)
        elif sort == "oldest":
            hits.sort(key=lambda h: h.get("when") or "")
        current_root = self._lineage_root(current_session_id)
        results = []
        seen_roots: set[str] = set()
        for hit in hits:
            if roles and hit.get("role") not in roles:
                continue
            sid = hit["session"]
            root = self._lineage_root(sid) or sid
            if current_root and root == current_root:
                continue
            if root in seen_roots:
                continue
            sess = self.load(sid)
            if not sess:
                continue
            seen_roots.add(root)
            msg_id = hit.get("message_id")
            if msg_id is None:
                msg_id = self._first_matching_message_id(sess, query, roles=roles)
            view = (
                self.messages_around(sid, int(msg_id), window=5, current_session_id=None)
                if msg_id is not None else {"messages": [], "messages_before": 0, "messages_after": 0}
            )
            bookend_roles = {"user", "assistant"}
            visible = self._visible_messages(sess, roles=bookend_roles)
            entry = {
                "session_id": sid,
                "title": sess.title,
                "profile": sess.profile,
                "when": sess.updated_at,
                "matched_role": hit.get("role"),
                "match_message_id": msg_id,
                "match_message_row_id": hit.get("message_row_id"),
                "snippet": hit.get("snippet", ""),
                "bookend_start": visible[:3],
                "messages": view.get("messages", []),
                "bookend_end": visible[-3:] if len(visible) > 3 else visible,
                "messages_before": view.get("messages_before", 0),
                "messages_after": view.get("messages_after", 0),
            }
            if root != sid:
                entry["parent_session_id"] = root
            results.append(entry)
            if len(results) >= limit:
                break
        return {
            "success": True,
            "mode": "discover",
            "query": query,
            "results": results,
            "count": len(results),
        }

    def summarize(self, sid: str, provider=None, config=None) -> str:
        """Generate + store a 1-2 sentence summary of a session via the provider."""
        sess = self.load(sid)
        if not sess:
            return ""
        from .types import Message
        transcript = "\n".join(f"{m.role}: {m.content}" for m in sess.messages
                               if m.role in ("user", "assistant") and m.content)[:12_000]
        if not transcript.strip():
            return ""
        try:
            if provider is None:
                from .auxiliary import AuxRouter
                provider = AuxRouter(config).provider_for("session_summary")
            resp = provider.complete([
                Message.system("Summarize this conversation in 1-2 sentences: what the user wanted "
                               "and what was decided/done. Be specific and factual."),
                Message.user(transcript),
            ], tools=None, stream=False)
            summary = resp.text.strip()
        except Exception:  # noqa: BLE001
            return ""
        sess.meta["summary"] = summary
        self.save(sess)
        return summary
