"""Stage N compaction-runner durability parity tests."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from aegis.agent.compaction_runner import _force_compact, _maybe_compact
from aegis.session import Session, SessionStore
from aegis.types import Message


class FakeConfig:
    def __init__(self, compression):
        self.compression = compression

    def get(self, key, default=None):
        if key == "agent.compression":
            return self.compression
        return default


class FakeProvider:
    context_length = 1000
    max_tokens = 0
    name = "fake"
    model = "fake-model"

    def __init__(self, store):
        self.store = store

    def complete(self, messages, **_kwargs):
        assert self.store.saves, "compacted session was not saved before provider call"
        assert [m.content for m in self.store.saves[-1]["messages"]] == [m.content for m in messages]
        return SimpleNamespace(text="ok")


class FakeEngine:
    def __init__(self, compressed):
        self.compressed = compressed
        self.compress_calls = []

    def threshold_fraction(self):
        return 0.5

    def should_compress(self, messages, *_args, **_kwargs):
        return messages is not self.compressed

    def compress(self, messages, summarizer, **kwargs):
        self.compress_calls.append((messages, summarizer, kwargs))
        return self.compressed


class NoProgressEngine:
    def __init__(self):
        self.compress_calls = []

    def threshold_fraction(self):
        return 0.5

    def should_compress(self, *_args, **_kwargs):
        return True

    def compress(self, messages, summarizer, **kwargs):
        self.compress_calls.append((messages, summarizer, kwargs))
        return list(messages)


class MultiPassRecoveryEngine:
    def __init__(self):
        self.compress_calls = []

    def threshold_fraction(self):
        return 0.5

    def should_compress(self, messages, *_args, **_kwargs):
        return len(messages) > 3

    def compress(self, messages, summarizer, **kwargs):
        self.compress_calls.append((messages, summarizer, kwargs))
        if len(self.compress_calls) == 1:
            return [
                Message.system("system"),
                Message(
                    role="assistant",
                    content="[Earlier conversation summarized]\nfirst pass",
                    meta={"fallback_used": True},
                ),
                Message.user("latest request"),
                Message.assistant("still too large " * 30),
            ]
        return [
            Message.system("system"),
            Message.assistant("[Earlier conversation summarized]\nsecond pass"),
            Message.user("latest request"),
        ]


class TokenOnlyProgressEngine:
    def __init__(self):
        self.compress_calls = []

    def threshold_fraction(self):
        return 0.5

    def should_compress(self, messages, *_args, **_kwargs):
        from aegis.agent import compaction

        return compaction.estimated_tokens(messages) > 120

    def compress(self, messages, summarizer, **kwargs):
        self.compress_calls.append((messages, summarizer, kwargs))
        if len(self.compress_calls) == 1:
            return [
                Message.system("system"),
                Message.user("x" * 360),
                Message.assistant("y" * 360),
                Message.user("latest request"),
            ]
        return [
            Message.system("system"),
            Message.user("x" * 80),
            Message.assistant("y" * 80),
            Message.user("latest request"),
        ]


class RecordingStore:
    def __init__(self):
        self.saves = []
        self.locks = {}

    def save(self, session):
        self.saves.append({
            "id": session.id,
            "messages": deepcopy(session.messages),
            "meta": deepcopy(session.meta),
        })

    def try_acquire_compression_lock(self, session_id, holder):
        self.locks[session_id] = holder
        return True

    def get_compression_lock_holder(self, session_id):
        return self.locks.get(session_id)

    def release_compression_lock(self, session_id, holder):
        if self.locks.get(session_id) == holder:
            del self.locks[session_id]


def _session():
    session = Session.create("stage n")
    session.messages = [
        Message.system("system"),
        Message.user("old context " * 80),
        Message.assistant("old answer " * 80),
        Message.user("latest request"),
    ]
    return session


def _agent(session, engine, store, *, split_sessions):
    provider = FakeProvider(store)
    agent = SimpleNamespace(
        config=FakeConfig({"split_sessions": split_sessions, "preserve_first": 1}),
        provider=provider,
        session=session,
        store=store,
        memory=None,
        budget=SimpleNamespace(api_call_count=3),
        _context_engine=engine,
        _compact_stuck=False,
        _compression_feasibility_checked=None,
        event_callback=None,
        platform="test",
    )
    agent.refresh_volatile = lambda: None
    agent.switch_session = lambda child, reason="": setattr(agent, "session", child)
    return agent


def test_maybe_compact_in_place_records_metadata_and_saves_before_provider_call():
    store = RecordingStore()
    session = _session()
    compressed = [
        Message.system("system"),
        Message.assistant("[Earlier conversation summarized]\nsummary"),
        Message.user("latest request"),
    ]
    engine = FakeEngine(compressed)
    agent = _agent(session, engine, store, split_sessions=False)
    events = []

    out = _maybe_compact(agent, session, schema_tokens=0, budget=agent.budget, emit=events.append)
    agent.provider.complete(out.messages)

    assert out is session
    assert [m.content for m in session.messages] == [m.content for m in compressed]
    compactions = session.meta.get("compactions")
    assert compactions and compactions[0]["messages_before"] == 4
    assert compactions[0]["messages_after"] == 3
    assert compactions[0]["reason"] == "context exceeded 50% of the window"
    assert store.saves[-1]["id"] == session.id
    assert store.saves[-1]["meta"]["compactions"] == compactions
    assert [m.content for m in store.saves[-1]["messages"]] == [m.content for m in compressed]


def test_force_compact_records_recovery_metadata_and_saves_compacted_session():
    store = RecordingStore()
    session = _session()
    compressed = [
        Message.system("system"),
        Message.assistant("[Earlier conversation summarized]\nrecovery summary"),
        Message.user("latest request"),
    ]
    engine = FakeEngine(compressed)
    agent = _agent(session, engine, store, split_sessions=False)

    out = _force_compact(agent, session)

    assert out is session
    assert [m.content for m in session.messages] == [m.content for m in compressed]
    compactions = session.meta.get("compactions")
    assert compactions and compactions[0]["recovery"] is True
    assert compactions[0]["reason"] == "context_overflow"
    assert compactions[0]["messages_before"] == 4
    assert compactions[0]["messages_after"] == 3
    assert store.saves[-1]["id"] == session.id
    assert store.saves[-1]["meta"]["compactions"] == compactions
    assert [m.content for m in store.saves[-1]["messages"]] == [m.content for m in compressed]


def test_force_compact_runs_multi_pass_recovery_and_persists_depth():
    store = SessionStore()
    session = _session()
    store.save(session)
    engine = MultiPassRecoveryEngine()
    agent = _agent(session, engine, store, split_sessions=False)
    agent.provider.context_length = 10_000
    agent.config.compression["recovery_max_passes"] = 3

    out = _force_compact(agent, session)
    loaded = store.load(out.id)

    assert loaded is not None
    assert len(engine.compress_calls) == 2
    assert engine.compress_calls[0][2]["tail_tokens"] > engine.compress_calls[1][2]["tail_tokens"]
    compaction = loaded.meta["compactions"][0]
    assert compaction["recovery"] is True
    assert compaction["pass_count"] == 2
    assert compaction["stop_reason"] == "below_threshold"
    assert compaction["fallback_used"] is True
    assert compaction["passes"][0]["fallback_used"] is True
    assert compaction["passes"][1]["progress_kind"] == "messages"
    recovery = loaded.meta["compression_recovery"]
    assert recovery["depth"] == 2
    assert recovery["last_depth_delta"] == 2
    assert recovery["last_pass_count"] == 2
    assert recovery["last_stop_reason"] == "below_threshold"


def test_maybe_compact_in_place_archives_prior_rows_and_loads_live_context():
    store = SessionStore()
    session = _session()
    store.save(session)
    compressed = [
        Message.system("system"),
        Message.assistant("[Earlier conversation summarized]\ndurable summary"),
        Message.user("latest request"),
    ]
    engine = FakeEngine(compressed)
    agent = _agent(session, engine, store, split_sessions=False)

    out = _maybe_compact(agent, session, schema_tokens=0, budget=agent.budget, emit=lambda _e: None)

    assert out is session
    loaded = store.load(session.id)
    assert loaded is not None
    assert [m.content for m in loaded.messages] == [m.content for m in compressed]
    assert loaded.meta.get("compactions")
    with store._conn() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT message_index, role, content, active, compacted "
                "FROM messages WHERE session_id=? ORDER BY active DESC, message_index",
                (session.id,),
            ).fetchall()
        ]
    active_rows = [row for row in rows if row["active"]]
    archived_rows = [row for row in rows if not row["active"]]
    assert [row["content"] for row in active_rows] == [m.content for m in compressed]
    assert [row["message_index"] for row in active_rows] == [0, 1, 2]
    assert archived_rows
    assert all(row["compacted"] == 1 for row in archived_rows)
    assert any(row["content"].startswith("old context") for row in archived_rows)


def test_maybe_compact_multi_pass_counts_token_only_progress():
    store = RecordingStore()
    session = _session()
    session.messages = [
        Message.system("system"),
        Message.user("x" * 1200),
        Message.assistant("y" * 1200),
        Message.user("latest request"),
    ]
    engine = TokenOnlyProgressEngine()
    agent = _agent(session, engine, store, split_sessions=False)
    agent.config.compression["max_passes"] = 3

    out = _maybe_compact(agent, session, schema_tokens=0, budget=agent.budget, emit=lambda _e: None)

    assert out is session
    assert len(engine.compress_calls) == 2
    compaction = session.meta["compactions"][0]
    assert compaction["pass_count"] == 2
    assert compaction["stop_reason"] == "below_threshold"
    assert compaction["passes"][0]["progress_kind"] == "tokens"
    assert compaction["passes"][0]["messages_before"] == compaction["passes"][0]["messages_after"]
    assert compaction["progress_made"] is True
    assert "compression_no_progress" not in session.meta
    assert session.meta["compression_recovery"]["last_pass_count"] == 2


def test_maybe_compact_persists_no_progress_guard_across_reloaded_turns():
    store = SessionStore()
    session = _session()
    store.save(session)
    engine = NoProgressEngine()

    first_agent = _agent(session, engine, store, split_sessions=False)
    _maybe_compact(first_agent, session, schema_tokens=0, budget=first_agent.budget, emit=lambda _e: None)
    first_reload = store.load(session.id)
    assert first_reload is not None
    first_state = first_reload.meta.get("compression_no_progress")
    assert first_state["count"] == 1
    assert first_state["blocked"] is False

    second_agent = _agent(first_reload, engine, store, split_sessions=False)
    _maybe_compact(
        second_agent,
        first_reload,
        schema_tokens=0,
        budget=second_agent.budget,
        emit=lambda _e: None,
    )
    second_reload = store.load(session.id)
    assert second_reload is not None
    second_state = second_reload.meta.get("compression_no_progress")
    assert second_state["count"] == 2
    assert second_state["blocked"] is True
    calls_after_second = len(engine.compress_calls)

    third_events = []
    third_agent = _agent(second_reload, engine, store, split_sessions=False)
    out = _maybe_compact(
        third_agent,
        second_reload,
        schema_tokens=0,
        budget=third_agent.budget,
        emit=third_events.append,
    )

    assert out is second_reload
    assert len(engine.compress_calls) == calls_after_second
    assert third_agent._compact_stuck is True
    assert len(third_events) == 1
    skipped = third_events[0]
    assert skipped["type"] == "compaction_skipped"
    assert skipped["reason"] == "compression_no_progress"
    assert skipped["session_id"] == second_reload.id
    assert skipped["ineffective_count"] == 2
    assert skipped["blocked"] is True
    assert skipped["last_stop_reason"] == "no_progress"
    assert skipped["last_plan_id"]


def test_compaction_summary_input_rehydrates_persisted_tool_output(tmp_path):
    from aegis.agent import compaction
    from aegis.tools.tool_result_storage import maybe_persist_tool_result

    class Summarizer:
        context_length = 1000

        def __init__(self):
            self.user_content = ""

        def complete(self, messages, **_kwargs):
            self.user_content = messages[-1].content
            return SimpleNamespace(text="summary")

    full = ("compaction persisted line\n" * 40) + "FULL_COMPACTION_TAIL"
    persisted = maybe_persist_tool_result(
        full,
        "bash",
        "call_compaction_rehydrate",
        threshold_chars=0,
        preview_chars=32,
        local_dir=tmp_path,
    )
    assert "FULL_COMPACTION_TAIL" not in persisted
    summarizer = Summarizer()
    messages = [
        Message.system("system"),
        Message.user("old request"),
        Message.tool("call_compaction_rehydrate", "bash", persisted),
        Message.user("latest request"),
    ]

    compressed = compaction.compress(
        messages,
        summarizer,
        preserve_first=0,
        preserve_last=1,
        max_tool_tokens=1000,
    )

    assert "FULL_COMPACTION_TAIL" in summarizer.user_content
    assert compressed[-2].content.startswith("[Earlier conversation summarized]")
