"""Stage Z session DB repair regressions."""

from __future__ import annotations

import fcntl
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest


def _supports_fts5() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._fts_probe USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def _message_table_has_unique_key(conn: sqlite3.Connection) -> bool:
    conn.row_factory = sqlite3.Row
    for row in conn.execute("PRAGMA index_list(messages)").fetchall():
        if not row["unique"]:
            continue
        cols = [
            info["name"]
            for info in conn.execute(f"PRAGMA index_info('{row['name']}')").fetchall()
        ]
        if cols == ["session_id", "message_index"]:
            return True
    return False


def _assert_sqlite_only_wal_repair_policy(status: dict[str, Any]) -> None:
    """Hermes parity: WAL repair uses SQLite checkpointing or preserved backups."""
    policy = status["policy"]
    assert policy["manual_wal_frame_decode"] == "not_attempted"
    assert policy["manual_restore"] == "available_via_restore_repair_backup"
    assert policy["safe_replay"] in {
        "attempted_with_sqlite_checkpoint_on_backup_copy",
        "not_attempted_no_complete_wal_frames",
    }
    assert policy["uncheckpointed_wal_frames"] in {
        "applied_by_sqlite_checkpoint",
        "preserved_in_backup_only",
    }


def test_raw_repair_backup_copies_db_wal_and_shm_sidecars(tmp_path):
    from aegis.session import SessionStore

    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"main-db")
    db_path.with_name("state.db-wal").write_bytes(b"wal-bytes")
    db_path.with_name("state.db-shm").write_bytes(b"shm-bytes")

    backup = SessionStore._copy_db_repair_backup(db_path, "Malformed Schema")

    assert backup is not None
    assert backup.name.startswith("state.db.malformed-schema-backup-")
    assert backup.read_bytes() == b"main-db"
    assert backup.with_name(backup.name + "-wal").read_bytes() == b"wal-bytes"
    assert backup.with_name(backup.name + "-shm").read_bytes() == b"shm-bytes"


def test_manual_repair_backup_restore_preserves_current_db_and_restores_sidecars(tmp_path):
    from aegis.session import SessionStore

    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"current-db")
    db_path.with_name("state.db-wal").write_bytes(b"stale-wal")

    backup = db_path.with_name("state.db.wal-sidecar-backup-123")
    backup.write_bytes(b"backup-db")
    backup.with_name(backup.name + "-wal").write_bytes(b"backup-wal")
    backup.with_name(backup.name + "-shm").write_bytes(b"backup-shm")
    backup.with_name(backup.name + ".repair.json").write_text(
        json.dumps({"kind": "wal_sidecar_repair", "result": "not_repaired"}),
        encoding="utf-8",
    )

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = ""
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = db_path

    listed = repair_store.list_repair_backups()
    assert listed == [
        {
            "backup_path": str(backup),
            "bytes": len(b"backup-db"),
            "kind": "wal_sidecar_repair",
            "result": "not_repaired",
            "status_path": str(backup.with_name(backup.name + ".repair.json")),
            "wal_path": str(backup.with_name(backup.name + "-wal")),
            "shm_path": str(backup.with_name(backup.name + "-shm")),
        }
    ]

    result = repair_store.restore_repair_backup(backup)

    assert result["restored"] is True
    assert result["repair_lock"]["acquired"] is True
    assert db_path.read_bytes() == b"backup-db"
    assert db_path.with_name("state.db-wal").read_bytes() == b"backup-wal"
    assert db_path.with_name("state.db-shm").read_bytes() == b"backup-shm"
    pre_restore = result["pre_restore_backup_path"]
    assert pre_restore
    assert Path(pre_restore).read_bytes() == b"current-db"
    status = json.loads(Path(result["status_path"]).read_text(encoding="utf-8"))
    assert status["kind"] == "manual_repair_backup_restore"
    assert status["result"] == "restored"


def test_manual_repair_backup_restore_fails_closed_when_lock_is_held(tmp_path):
    from aegis.session import SessionStore

    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"current-db")
    backup = db_path.with_name("state.db.malformed-schema-backup-123")
    backup.write_bytes(b"backup-db")
    lock_path = SessionStore._repair_lock_path(db_path)

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = ""
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = db_path

    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = repair_store.restore_repair_backup(backup)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert result["restored"] is False
    assert result["repair_lock"]["acquired"] is False
    assert result["error"] == "locked_by_another_process"
    assert db_path.read_bytes() == b"current-db"


def test_legacy_message_table_is_repaired_and_corrupt_snapshot_still_recovers():
    from aegis import config as cfg
    from aegis.session import SessionStore
    from aegis.types import Message

    db_path = cfg.sessions_db()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE sessions (
                   id TEXT PRIMARY KEY,
                   title TEXT,
                   created_at TEXT,
                   updated_at TEXT,
                   summary TEXT,
                   data TEXT,
                   parent_id TEXT,
                   profile TEXT,
                   archived INTEGER DEFAULT 0,
                   source TEXT DEFAULT ''
               )"""
        )
        conn.execute(
            """INSERT INTO sessions
               (id, title, created_at, updated_at, summary, data, parent_id, profile, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy_session",
                "legacy repair",
                "2026-06-30T00:00:00+00:00",
                "2026-06-30T00:00:01+00:00",
                "",
                "{broken snapshot",
                None,
                "",
                "",
            ),
        )
        conn.execute(
            """CREATE TABLE messages (
                   session_id TEXT NOT NULL,
                   message_index INTEGER NOT NULL,
                   role TEXT,
                   content TEXT,
                   tool_name TEXT,
                   tool_call_id TEXT,
                   created_at TEXT
               )"""
        )
        conn.executemany(
            """INSERT INTO messages
               (session_id, message_index, role, content, tool_name, tool_call_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                ("legacy_session", 0, "user", "legacy user row", None, None, "t0"),
                ("legacy_session", 1, "assistant", "legacy assistant row", None, None, "t1"),
                ("legacy_session", 2, "tool", "legacy tool row", "legacy_tool", "call_legacy", "t2"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    store = SessionStore()
    recovered = store.load("legacy_session")

    assert recovered is not None
    assert [message.role for message in recovered.messages] == ["user", "assistant", "tool"]
    assert recovered.messages[-1].tool_call_id == "call_legacy"
    assert recovered.meta["_session_repair"]["kind"] == "corrupt_snapshot"

    row_ids = store.append_messages(
        recovered,
        [Message.tool("call_after_repair", "after_repair", "append after repair")],
        start_index=len(recovered.messages),
    )
    assert len(row_ids) == 1

    reloaded = store.load("legacy_session")
    assert reloaded is not None
    assert [message.content for message in reloaded.messages] == [
        "legacy user row",
        "legacy assistant row",
        "legacy tool row",
        "append after repair",
    ]

    with store._conn() as repaired:
        cols = {
            row["name"]
            for row in repaired.execute("PRAGMA table_info(messages)").fetchall()
        }
        assert {"id", "profile", "active", "compacted"} <= cols
        assert _message_table_has_unique_key(repaired)
        persisted = repaired.execute(
            "SELECT data FROM sessions WHERE id=?",
            ("legacy_session",),
        ).fetchone()["data"]
    assert json.loads(persisted)["meta"]["_session_repair"]["source"] == "message_rows"


def test_fts_write_failure_repairs_and_rebuilds_from_message_rows():
    if not _supports_fts5():
        pytest.skip("SQLite FTS5 unavailable")

    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    session = Session.create(title="fts repair")
    session.messages = [
        Message.user("before fts damage"),
        Message.assistant("before fts answer"),
    ]
    store.save(session)

    with store._conn() as conn:
        conn.execute("DROP TABLE messages_fts")
        conn.execute("CREATE TABLE messages_fts (content TEXT)")

    session.messages.append(Message.user("needle after fts repair"))
    store.save(session)

    backups = list(store.db.parent.glob(f"{store.db.name}.fts-repair-backup-*"))
    assert backups

    loaded = store.load(session.id)
    assert loaded is not None
    assert loaded.messages[-1].content == "needle after fts repair"

    hits = store.search_messages("needle", limit=5)
    assert any(hit["session"] == session.id for hit in hits)
    with store._conn() as conn:
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(messages_fts)").fetchall()
        }
    assert {"message_id", "profile"} <= cols


def test_wal_locking_protocol_falls_back_to_delete_journal_mode():
    from aegis.session import SessionStore

    class FakeConn:
        def __init__(self):
            self.calls: list[str] = []

        def execute(self, sql: str):
            self.calls.append(sql)
            if sql == "PRAGMA journal_mode=WAL":
                raise sqlite3.OperationalError("locking protocol")
            return None

    fake = FakeConn()
    SessionStore._apply_wal_with_fallback(fake)  # type: ignore[arg-type]

    assert fake.calls == ["PRAGMA journal_mode=WAL", "PRAGMA journal_mode=DELETE"]


def test_corrupt_wal_sidecar_is_backed_up_and_recovered_from_checkpointed_main_db():
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    session = Session(id="wal_baseline", title="wal baseline")
    session.messages = [Message.user("checkpointed message")]
    store.save(session)

    db_path = store.db
    writer = sqlite3.connect(db_path)
    reader = None
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        reader = sqlite3.connect(db_path)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM sessions").fetchone()
        writer.execute(
            """INSERT INTO sessions
               (id, title, created_at, updated_at, summary, data, parent_id, profile, archived, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "wal_uncheckpointed",
                "wal only",
                "2026-06-30T00:00:00+00:00",
                "2026-06-30T00:00:01+00:00",
                "",
                json.dumps({"messages": [], "todos": [], "meta": {}}),
                None,
                "",
                0,
                "",
            ),
        )
        writer.commit()
        wal_path = db_path.with_name(db_path.name + "-wal")
        assert wal_path.exists()
        writer.close()
        writer = None
        wal_path.write_bytes(wal_path.read_bytes()[:100])
    finally:
        if writer is not None:
            writer.close()
        if reader is not None:
            reader.close()

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = store.profile
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = db_path
    assert repair_store._repair_wal_sidecars(sqlite3.OperationalError("disk I/O error"))

    recovered = SessionStore()

    loaded = recovered.load(session.id)
    assert loaded is not None
    assert loaded.messages[0].content == "checkpointed message"
    assert recovered.load("wal_uncheckpointed") is None

    backups = sorted(
        path for path in recovered.db.parent.glob(f"{recovered.db.name}.wal-sidecar-backup-*")
        if not path.name.endswith(("-wal", "-shm", ".repair.json"))
    )
    assert backups
    assert backups[-1].with_name(backups[-1].name + "-wal").stat().st_size == 100
    status = json.loads(backups[-1].with_name(backups[-1].name + ".repair.json").read_text())
    assert status["kind"] == "wal_sidecar_repair"
    assert status["schema_version"] == 1
    assert status["result"] == "recovered_from_checkpointed_main_db"
    assert status["failure_stage"] is None
    assert status["main_db_probe_without_sidecars"] == "ok"
    assert status["final_health"] == "ok"
    assert status["header_action"] == "forced_rollback_journal"
    assert status["repair_lock"]["acquired"] is True
    assert status["repair_lock"]["path"].endswith("state.db.repair.lock")
    _assert_sqlite_only_wal_repair_policy(status)
    assert status["policy"]["recovery_source"] == "checkpointed_main_db"
    assert status["policy"]["safe_replay"] == "not_attempted_no_complete_wal_frames"
    assert status["policy"]["safe_replay_possible"] is False
    assert status["policy"]["uncheckpointed_wal_frames"] == "preserved_in_backup_only"
    assert status["sqlite_replay"]["attempted"] is False
    assert status["sqlite_replay"]["result"] == "no_complete_wal_frames"
    wal_accounting = status["wal_evidence"]["wal"]["frame_accounting"]
    assert wal_accounting["bytes"] == 100
    assert wal_accounting["complete_frame_count"] == 0
    wal_status = next(
        sidecar for sidecar in status["sidecars"]
        if sidecar["live_path"].endswith("state.db-wal")
    )
    assert wal_status["action"] == "deleted_from_live_db"
    assert wal_status["backup_path"].endswith("-wal")
    with recovered._conn() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_valid_wal_sidecar_is_replayed_by_sqlite_checkpoint_before_fallback():
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    session = Session(id="wal_replay_baseline", title="wal replay baseline")
    session.messages = [Message.user("checkpointed message")]
    store.save(session)

    db_path = store.db
    writer = sqlite3.connect(db_path)
    reader = None
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        reader = sqlite3.connect(db_path)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM sessions").fetchone()
        writer.execute(
            """INSERT INTO sessions
               (id, title, created_at, updated_at, summary, data, parent_id, profile, archived, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "wal_replayed",
                "wal replayed",
                "2026-06-30T00:00:00+00:00",
                "2026-06-30T00:00:01+00:00",
                "",
                json.dumps({"messages": [], "todos": [], "meta": {}}),
                None,
                "",
                0,
                "",
            ),
        )
        writer.commit()
        assert db_path.with_name(db_path.name + "-wal").exists()
    finally:
        if writer is not None:
            writer.close()
        if reader is not None:
            reader.close()

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = store.profile
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = db_path
    assert repair_store._repair_wal_sidecars(sqlite3.OperationalError("disk I/O error"))

    recovered = SessionStore()
    assert recovered.load("wal_replayed") is not None

    backups = sorted(
        path for path in recovered.db.parent.glob(f"{recovered.db.name}.wal-sidecar-backup-*")
        if not path.name.endswith(("-wal", "-shm", ".repair.json"))
    )
    assert backups
    status = json.loads(backups[-1].with_name(backups[-1].name + ".repair.json").read_text())
    assert status["result"] == "recovered_by_sqlite_wal_checkpoint"
    assert status["main_db_probe_without_sidecars"] == "not_run"
    assert status["policy"]["recovery_source"] == "sqlite_checkpointed_wal_backup"
    assert status["policy"]["safe_replay"] == (
        "attempted_with_sqlite_checkpoint_on_backup_copy"
    )
    assert status["policy"]["safe_replay_possible"] is True
    _assert_sqlite_only_wal_repair_policy(status)
    assert status["policy"]["uncheckpointed_wal_frames"] == "applied_by_sqlite_checkpoint"
    assert status["sqlite_replay"]["attempted"] is True
    assert status["sqlite_replay"]["result"] == "checkpointed"
    assert status["sqlite_replay"]["final_health"] == "ok"
    assert status["wal_evidence"]["wal"]["frame_accounting"]["complete_frame_count"] > 0


def test_wal_sidecar_repair_refusal_writes_inspectable_policy(monkeypatch, tmp_path):
    from aegis.session import SessionStore

    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"main-db")
    wal_path = db_path.with_name("state.db-wal")
    shm_path = db_path.with_name("state.db-shm")
    wal_path.write_bytes(b"wal")
    shm_path.write_bytes(b"shm")

    def unhealthy_without_sidecars(_db_path):
        return "main database is not healthy without WAL frames"

    monkeypatch.setattr(
        SessionStore,
        "_main_db_health_without_sidecars",
        classmethod(lambda cls, path: unhealthy_without_sidecars(path)),
    )

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = ""
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = db_path

    repaired = repair_store._repair_wal_sidecars(
        sqlite3.DatabaseError("database disk image is malformed")
    )

    assert repaired is False
    assert wal_path.read_bytes() == b"wal"
    assert shm_path.read_bytes() == b"shm"

    backups = sorted(
        path for path in db_path.parent.glob(f"{db_path.name}.wal-sidecar-backup-*")
        if not path.name.endswith(("-wal", "-shm", ".repair.json"))
    )
    assert backups
    status = json.loads(backups[-1].with_name(backups[-1].name + ".repair.json").read_text())
    assert status["result"] == "not_repaired"
    assert status["failure_stage"] == "main_db_probe_without_sidecars"
    assert status["main_db_probe_without_sidecars"] == (
        "main database is not healthy without WAL frames"
    )
    assert status["final_health"] == "not_run"
    assert status["header_action"] == "not_changed"
    assert status["repair_lock"]["acquired"] is True
    assert status["policy"]["safe_replay"] == "not_attempted_no_complete_wal_frames"
    assert status["sqlite_replay"]["result"] == "no_complete_wal_frames"
    assert status["wal_evidence"]["wal"]["frame_accounting"]["format"] == "too_short"
    assert {sidecar["action"] for sidecar in status["sidecars"]} == {"left_in_live_db"}
    assert backups[-1].with_name(backups[-1].name + "-wal").read_bytes() == b"wal"


def test_malformed_repair_lock_does_not_consume_one_shot(tmp_path):
    from aegis import session as session_module
    from aegis.session import SessionStore

    session_module._MALFORMED_REPAIR_ATTEMPTED_PATHS.clear()
    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"SQLite format 3\x00" + b"\x00\xde\xad\xbe\xef" * 200)
    lock_path = SessionStore._repair_lock_path(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = ""
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = db_path

    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert repair_store._repair_malformed_sqlite_schema(
            sqlite3.DatabaseError("database disk image is malformed")
        ) is False
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert not list(db_path.parent.glob(f"{db_path.name}.malformed-schema-backup-*"))
    assert repair_store._repair_malformed_sqlite_schema(
        sqlite3.DatabaseError("database disk image is malformed")
    ) is False

    backups = [
        path for path in db_path.parent.glob(f"{db_path.name}.malformed-schema-backup-*")
        if not path.name.endswith(("-wal", "-shm", ".repair.json"))
    ]
    assert len(backups) == 1
    status = json.loads(backups[0].with_name(backups[0].name + ".repair.json").read_text())
    assert status["repair_lock"]["acquired"] is True


def test_malformed_schema_repair_requires_clean_post_repair_health_probe(monkeypatch):
    if not _supports_fts5():
        pytest.skip("SQLite FTS5 unavailable")

    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    session = Session(id="malformed_health", title="malformed health")
    session.messages = [Message.user("kept in canonical rows")]
    store.save(session)

    with store._conn() as conn:
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) "
            "SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master "
            "WHERE name='messages_fts'"
        )
        conn.execute("PRAGMA writable_schema=OFF")

    def still_unhealthy(_db_path, *, require_sessions=False):
        return "still malformed"

    monkeypatch.setattr(
        SessionStore,
        "_db_health_error",
        staticmethod(still_unhealthy),
    )

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = store.profile
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = store.db

    repaired = repair_store._repair_malformed_sqlite_schema(
        sqlite3.DatabaseError("malformed database schema (messages_fts)")
    )

    assert repaired is False
    assert list(store.db.parent.glob(f"{store.db.name}.malformed-schema-backup-*"))


def test_malformed_schema_repair_writes_policy_status_and_evidence():
    if not _supports_fts5():
        pytest.skip("SQLite FTS5 unavailable")

    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    session = Session(id="malformed_policy", title="malformed policy")
    session.messages = [Message.user("canonical row survives")]
    store.save(session)

    with store._conn() as conn:
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "INSERT INTO sqlite_master (type, name, tbl_name, rootpage, sql) "
            "SELECT type, name, tbl_name, rootpage, sql FROM sqlite_master "
            "WHERE name='messages_fts'"
        )
        conn.execute("PRAGMA writable_schema=OFF")

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = store.profile
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = store.db

    repaired = repair_store._repair_malformed_sqlite_schema(
        sqlite3.DatabaseError("malformed database schema (messages_fts)")
    )

    assert repaired is True
    backups = sorted(
        path for path in store.db.parent.glob(f"{store.db.name}.malformed-schema-backup-*")
        if not path.name.endswith(("-wal", "-shm", ".repair.json"))
    )
    assert backups
    status = json.loads(backups[-1].with_name(backups[-1].name + ".repair.json").read_text())
    assert status["kind"] == "malformed_sqlite_repair"
    assert status["result"] == "recovered_by_dropping_derived_fts_schema"
    assert status["strategy"] == "drop_fts_schema"
    assert status["failure_stage"] is None
    assert status["final_health"] == "ok"
    assert status["policy"]["automatic_rebuild"]["messages_fts"] == (
        "dropped_and_recreated_from_messages"
    )
    assert status["policy"]["not_rebuilt"] == {
        "main_db_pages": "not_attempted",
        "wal_frames": "not_replayed",
    }
    assert status["repair_lock"]["acquired"] is True
    assert status["evidence"]["sessions_table"] == "present"
    assert status["evidence"]["messages_table"] == "present"
    assert status["evidence"]["session_rows"] == 1
    assert status["evidence"]["message_rows"] == 1

    recovered = SessionStore()
    loaded = recovered.load(session.id)
    assert loaded is not None
    assert loaded.messages[0].content == "canonical row survives"


def test_unrecoverable_malformed_main_db_writes_policy_and_is_one_shot(tmp_path):
    from aegis import session as session_module
    from aegis.session import SessionStore

    session_module._MALFORMED_REPAIR_ATTEMPTED_PATHS.clear()
    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"SQLite format 3\x00" + b"\x00\xde\xad\xbe\xef" * 200)

    repair_store = SessionStore.__new__(SessionStore)
    repair_store.profile = ""
    repair_store.read_only = False
    repair_store._fts = False
    repair_store.db = db_path

    repaired = repair_store._repair_malformed_sqlite_schema(
        sqlite3.DatabaseError("database disk image is malformed")
    )

    assert repaired is False
    backups = sorted(
        path for path in db_path.parent.glob(f"{db_path.name}.malformed-schema-backup-*")
        if not path.name.endswith(("-wal", "-shm", ".repair.json"))
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == db_path.read_bytes()
    status = json.loads(backups[0].with_name(backups[0].name + ".repair.json").read_text())
    assert status["result"] == "not_repaired"
    assert status["failure_stage"] == "sqlite_master_surgery"
    assert status["final_health"]
    assert status["policy"]["not_rebuilt"]["main_db_pages"] == "not_attempted"
    assert status["policy"]["not_rebuilt"]["wal_frames"] == "not_replayed"
    assert status["repair_lock"]["acquired"] is True
    assert "error" in status["evidence"]

    second = repair_store._repair_malformed_sqlite_schema(
        sqlite3.DatabaseError("database disk image is malformed")
    )
    assert second is False
    backups_after = [
        path for path in db_path.parent.glob(f"{db_path.name}.malformed-schema-backup-*")
        if not path.name.endswith(("-wal", "-shm", ".repair.json"))
    ]
    assert backups_after == backups
