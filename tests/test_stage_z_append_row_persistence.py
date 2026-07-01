"""Stage Z append-row persistence contracts for tool results.

These tests keep the whole-session snapshot path deliberately unavailable.
Tool results must still reach SQLite as message rows, and load back as the
durable tail of the transcript.
"""

from __future__ import annotations

import copy
import threading
from types import SimpleNamespace


def _config():
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data.setdefault("agent", {})["stream"] = False
    cfg.data.setdefault("memory", {})["enabled"] = False
    cfg.data.setdefault("skills", {})["auto_load"] = False
    cfg.data.setdefault("tools", {})["toolsets"] = ["core"]
    cfg.data.setdefault("tools", {})["exec_mode"] = "full"
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}
    cfg.data.setdefault("checkpoints", {})["enabled"] = False
    return cfg


class _AppendOnlyStore:
    def __init__(self, inner):
        self.inner = inner
        self.save_attempts = 0

    def append_messages(self, *args, **kwargs):
        return self.inner.append_messages(*args, **kwargs)

    def save(self, _session):
        self.save_attempts += 1
        raise RuntimeError("whole-session snapshots disabled for this test")

    def load(self, session_id):
        return self.inner.load(session_id)


class _OkTool:
    description = "Return a deterministic append-row result."
    parameters = {"type": "object", "properties": {}}
    groups = []
    toolset = "core"

    def __init__(self, name: str, content: str):
        self.name = name
        self.content = content
        self.runs = 0

    def available(self):
        return True, ""

    def schema(self):
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def run(self, _args, _ctx):
        from aegis.tools.base import ToolResult

        self.runs += 1
        return ToolResult.ok(self.content)


class _CancelTool(_OkTool):
    def run(self, _args, ctx):
        from aegis.tools.base import ToolResult

        self.runs += 1
        ctx.agent.cancel_event.set()
        return ToolResult.ok(self.content)


def _registry(*tools):
    from aegis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _executor(tmp_path, store, session, *tools):
    from aegis.agent.loop import ToolExecutor
    from aegis.tools.base import ToolContext
    from aegis.tools.permissions import PermissionEngine

    cfg = _config()
    agent = SimpleNamespace(
        store=store,
        cancel_event=threading.Event(),
        _trace_context={},
        _trace_store=None,
    )
    ctx = ToolContext(cwd=tmp_path, config=cfg, session=session, agent=agent)
    return ToolExecutor(
        _registry(*tools),
        PermissionEngine(cfg),
        ctx,
        lambda _event: None,
    )


def _seed_session_with_tool_turn(session, calls):
    from aegis.types import Message

    session.messages = [
        Message.user("run the requested tools"),
        Message.assistant(tool_calls=calls),
    ]


def _tool_rows(store, session_id):
    with store._conn() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT message_index, role, content, tool_name, tool_call_id "
                "FROM messages WHERE session_id=? AND role='tool' "
                "ORDER BY message_index",
                (session_id,),
            ).fetchall()
        ]


def test_multiple_tool_results_append_rows_without_snapshot_save(tmp_path):
    from aegis.session import Session, SessionStore
    from aegis.types import ToolCall

    inner = SessionStore()
    store = _AppendOnlyStore(inner)
    session = Session.create(title="append-row tools")
    first = _OkTool("stage_z_append_first", "first append result")
    second = _OkTool("stage_z_append_second", "second append result")
    calls = [
        ToolCall("call_stage_z_append_first", first.name, {}),
        ToolCall("call_stage_z_append_second", second.name, {}),
    ]
    _seed_session_with_tool_turn(session, calls)

    results = _executor(tmp_path, store, session, first, second).execute(calls)

    assert [message.tool_call_id for message in results] == [
        "call_stage_z_append_first",
        "call_stage_z_append_second",
    ]
    assert store.save_attempts >= 2
    rows = _tool_rows(inner, session.id)
    assert [
        (row["message_index"], row["tool_call_id"], row["tool_name"], row["content"])
        for row in rows
    ] == [
        (2, "call_stage_z_append_first", first.name, "first append result"),
        (3, "call_stage_z_append_second", second.name, "second append result"),
    ]
    loaded = inner.load(session.id)
    assert loaded is not None
    assert [message.tool_call_id for message in loaded.messages if message.role == "tool"] == [
        "call_stage_z_append_first",
        "call_stage_z_append_second",
    ]


def test_existing_session_base_is_refreshed_before_append_rows(tmp_path):
    from aegis.session import Session, SessionStore
    from aegis.types import Message, ToolCall

    inner = SessionStore()
    session = Session.create(title="existing append-row session")
    session.messages = [
        Message.user("previous turn"),
        Message.assistant("previous answer"),
    ]
    inner.save(session)
    store = _AppendOnlyStore(inner)
    tool = _OkTool("stage_z_existing_append", "existing session result")
    calls = [ToolCall("call_stage_z_existing_append", tool.name, {})]
    session.messages.extend([
        Message.user("run one more tool"),
        Message.assistant(tool_calls=calls),
    ])

    _executor(tmp_path, store, session, tool).execute(calls)

    rows = _tool_rows(inner, session.id)
    assert [(row["message_index"], row["tool_call_id"]) for row in rows] == [
        (4, "call_stage_z_existing_append"),
    ]
    loaded = inner.load(session.id)
    assert loaded is not None
    assert [message.role for message in loaded.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "tool",
    ]
    assert loaded.messages[-1].tool_call_id == "call_stage_z_existing_append"


def test_cancelled_skipped_tool_result_appends_durable_row(tmp_path):
    from aegis.session import Session, SessionStore
    from aegis.types import ToolCall

    inner = SessionStore()
    store = _AppendOnlyStore(inner)
    session = Session.create(title="append-row cancelled tools")
    first = _CancelTool("stage_z_append_cancel", "cancel requested")
    skipped = _OkTool("stage_z_append_should_skip", "should not run")
    calls = [
        ToolCall("call_stage_z_append_cancel", first.name, {}),
        ToolCall("call_stage_z_append_skipped", skipped.name, {}),
    ]
    _seed_session_with_tool_turn(session, calls)

    results = _executor(tmp_path, store, session, first, skipped).execute(calls)

    assert first.runs == 1
    assert skipped.runs == 0
    assert [message.tool_call_id for message in results] == [
        "call_stage_z_append_cancel",
        "call_stage_z_append_skipped",
    ]
    rows = _tool_rows(inner, session.id)
    assert [row["tool_call_id"] for row in rows] == [
        "call_stage_z_append_cancel",
        "call_stage_z_append_skipped",
    ]
    assert "skipped" in rows[1]["content"].lower() or "cancel" in rows[1]["content"].lower()
    loaded = inner.load(session.id)
    assert loaded is not None
    loaded_tools = [message for message in loaded.messages if message.role == "tool"]
    assert [message.tool_call_id for message in loaded_tools] == [
        "call_stage_z_append_cancel",
        "call_stage_z_append_skipped",
    ]
    assert "skipped" in loaded_tools[1].content.lower() or "cancel" in loaded_tools[1].content.lower()


def test_append_rows_after_in_place_compaction_hydrate_from_live_context():
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    session = Session.create(title="compacted append-row session")
    session.messages = [
        Message.system("system"),
        Message.user("old request before compact"),
        Message.assistant("old answer before compact"),
        Message.user("latest request"),
    ]
    store.save(session)
    session.messages = [
        Message.system("system"),
        Message.assistant("[Earlier conversation summarized]\nold request was handled"),
        Message.user("latest request"),
    ]
    session.meta.setdefault("compactions", []).append({"reason": "stage_z_test"})
    store.archive_and_compact(session)
    loaded = store.load(session.id)
    assert loaded is not None

    store.append_messages(
        loaded,
        [Message.tool("call_after_compact", "stage_z_after_compact", "post-compact result")],
        start_index=len(loaded.messages),
    )

    reloaded = store.load(session.id)
    assert reloaded is not None
    assert [message.role for message in reloaded.messages] == [
        "system",
        "assistant",
        "user",
        "tool",
    ]
    assert reloaded.messages[-1].tool_call_id == "call_after_compact"
    assert reloaded.messages[-1].content == "post-compact result"
    with store._conn() as conn:
        archived = conn.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE session_id=? AND active=0 AND compacted=1",
            (session.id,),
        ).fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id=? AND active=1",
            (session.id,),
        ).fetchone()[0]
    assert archived == 4
    assert active == 4


def test_corrupt_session_snapshot_recovers_from_active_message_rows():
    import json

    from aegis.session import Session, SessionStore
    from aegis.types import Message

    store = SessionStore()
    session = Session.create(title="corrupt snapshot recovery")
    session.messages = [
        Message.user("persisted user turn"),
        Message.assistant("persisted assistant reply"),
        Message.tool("call_recovered", "stage_z_repair_tool", "persisted tool result"),
    ]
    session.meta["runtime_controls"] = {"provider": "anthropic", "model": "claude-test"}
    store.save(session)

    with store._conn() as conn:
        conn.execute(
            "UPDATE sessions SET data=? WHERE id=?",
            ("{not valid json", session.id),
        )

    recovered = store.load(session.id)

    assert recovered is not None
    assert [message.role for message in recovered.messages] == ["user", "assistant", "tool"]
    assert [message.content for message in recovered.messages] == [
        "persisted user turn",
        "persisted assistant reply",
        "persisted tool result",
    ]
    assert recovered.messages[-1].tool_call_id == "call_recovered"
    assert recovered.messages[-1].name == "stage_z_repair_tool"
    repair = recovered.meta.get("_session_repair")
    assert repair["kind"] == "corrupt_snapshot"
    assert repair["source"] == "message_rows"
    assert repair["row_count"] == 3

    with store._conn() as conn:
        repaired_data = conn.execute(
            "SELECT data FROM sessions WHERE id=?",
            (session.id,),
        ).fetchone()["data"]
    persisted = json.loads(repaired_data)
    assert persisted["messages"][-1]["tool_call_id"] == "call_recovered"
    assert persisted["meta"]["_session_repair"]["source"] == "message_rows"


def test_read_only_corrupt_snapshot_recovery_does_not_attempt_repair_write():
    from aegis.session import Session, SessionStore
    from aegis.types import Message

    writable = SessionStore()
    session = Session.create(title="read only corrupt snapshot")
    session.messages = [Message.user("row survives")]
    writable.save(session)
    with writable._conn() as conn:
        conn.execute(
            "UPDATE sessions SET data=? WHERE id=?",
            ("{broken", session.id),
        )

    read_only = SessionStore(read_only=True)
    recovered = read_only.load(session.id)

    assert recovered is not None
    assert [message.content for message in recovered.messages] == ["row survives"]
    assert recovered.meta["_session_repair"]["kind"] == "corrupt_snapshot"
