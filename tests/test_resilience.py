"""Resilience + operational maturity: interrupts, tool availability, compaction metadata,
and real failure modes (crashed tool, unknown tool, session-save crash)."""

from __future__ import annotations

from conftest import FakeProvider


def _agent(provider, tmp_path, exec_mode="full", store=None):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    cfg = Config.load()
    cfg.data["tools"]["exec_mode"] = exec_mode
    return Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path, store=store)


# --- #1 interruptible loop -------------------------------------------------
def test_run_is_interruptible_midturn(tmp_path):
    from aegis.types import LLMResponse, ToolCall

    class CancelMid:
        context_length = 200_000
        name = "f"
        model = "m"
        api_mode = None
        auth = None
        def __init__(self):
            self.agent = None
            self.n = 0
        def describe(self): return "f"
        def complete(self, messages, **k):
            self.n += 1
            if self.n == 1:
                self.agent.cancel_event.set()
                return LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."})])
            return LLMResponse(text="unreached")

    p = CancelMid()
    a = _agent(p, tmp_path)
    p.agent = a
    events = []
    out = a.run("go", events.append)
    assert out.content == "[interrupted by user]"
    assert any(e["type"] == "cancelled" for e in events)
    assert p.n == 1                                   # stopped before the next model call


def test_cancel_during_provider_call_drops_late_assistant(tmp_path):
    from aegis.types import LLMResponse

    class CancelLate:
        context_length = 200_000
        name = "f"
        model = "m"
        api_mode = None
        auth = None
        def __init__(self):
            self.agent = None
        def describe(self): return "f"
        def complete(self, messages, **k):
            self.agent.cancel_event.set()
            return LLMResponse(text="late")

    p = CancelLate()
    a = _agent(p, tmp_path)
    p.agent = a
    events = []
    out = a.run("go", events.append)

    assert out.content == "[interrupted by user]"
    assert any(e["type"] == "cancelled" for e in events)
    assert [m.content for m in a.session.messages if m.role == "assistant"] == [
        "[interrupted by user]"
    ]


def test_cancel_during_budget_grace_call_drops_late_summary(tmp_path):
    from aegis.types import LLMResponse

    class CancelLateGrace:
        context_length = 200_000
        name = "f"
        model = "m"
        api_mode = None
        auth = None
        def __init__(self):
            self.agent = None
        def describe(self): return "f"
        def complete(self, messages, **k):
            self.agent.cancel_event.set()
            return LLMResponse(text="late grace")

    p = CancelLateGrace()
    a = _agent(p, tmp_path)
    a.budget.max_iterations = 0
    p.agent = a
    events = []
    out = a.run("go", events.append)

    assert out.content == "[interrupted by user]"
    assert any(e["type"] == "cancelled" for e in events)
    assert [m.content for m in a.session.messages if m.role == "assistant"] == [
        "[interrupted by user]"
    ]


def test_pre_llm_call_in_place_mutation_is_wire_only(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import LLMResponse

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake-model"
        api_mode = None
        auth = None

        def __init__(self):
            self.user_messages = []

        def describe(self):
            return "fake"

        def complete(self, messages, tools=None, **_kwargs):
            self.user_messages.append(next(m.content for m in reversed(messages) if m.role == "user"))
            return LLMResponse(text="done")

    def fire_hook(event, *args, **_kwargs):
        if event == "pre_llm_call":
            messages = args[0]
            messages[-1].content += "\n\nPLUGIN MUTATION"
        return None

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = Provider()
    monkeypatch.setattr("aegis.plugins.fire_hook", fire_hook)

    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)
    agent.run("hello")

    assert "PLUGIN MUTATION" in provider.user_messages[-1]
    user_message = next(m.content for m in agent.session.messages if m.role == "user")
    assert user_message == "hello"


def test_pre_llm_call_returned_list_is_wire_only(tmp_path, monkeypatch):
    import dataclasses

    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import LLMResponse

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake-model"
        api_mode = None
        auth = None

        def __init__(self):
            self.user_messages = []

        def describe(self):
            return "fake"

        def complete(self, messages, tools=None, **_kwargs):
            self.user_messages.append(next(m.content for m in reversed(messages) if m.role == "user"))
            return LLMResponse(text="done")

    def fire_hook(event, *args, **_kwargs):
        if event == "pre_llm_call":
            messages = list(args[0])
            messages[-1] = dataclasses.replace(messages[-1], content="rewritten only for provider")
            return messages
        return None

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = Provider()
    monkeypatch.setattr("aegis.plugins.fire_hook", fire_hook)

    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)
    agent.run("hello")

    assert provider.user_messages[-1] == "rewritten only for provider"
    user_message = next(m.content for m in agent.session.messages if m.role == "user")
    assert user_message == "hello"


def test_pre_llm_call_context_return_is_appended_wire_only(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import LLMResponse

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake-model"
        api_mode = None
        auth = None

        def __init__(self):
            self.user_messages = []

        def describe(self):
            return "fake"

        def complete(self, messages, tools=None, **_kwargs):
            self.user_messages.append(next(m.content for m in reversed(messages) if m.role == "user"))
            return LLMResponse(text="done")

    def fire_hook(event, *args, **_kwargs):
        if event == "pre_llm_call":
            return {"context": "PLUGIN CONTEXT"}
        return None

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = Provider()
    monkeypatch.setattr("aegis.plugins.fire_hook", fire_hook)

    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)
    agent.run("hello")

    assert provider.user_messages[-1] == "hello\n\nPLUGIN CONTEXT"
    user_message = next(m.content for m in agent.session.messages if m.role == "user")
    assert user_message == "hello"


def test_llm_middleware_message_mutation_is_wire_only(tmp_path, monkeypatch):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import LLMResponse

    class Provider:
        context_length = 200_000
        name = "fake"
        model = "fake-model"
        api_mode = None
        auth = None

        def __init__(self):
            self.user_messages = []

        def describe(self):
            return "fake"

        def complete(self, messages, tools=None, **_kwargs):
            self.user_messages.append(next(m.content for m in reversed(messages) if m.role == "user"))
            return LLMResponse(text="done")

    def fire_middleware(kind, payload, next_call, agent):
        if kind == "llm_request":
            payload["messages"][-1].content += "\n\nREQUEST MIDDLEWARE"
        if kind == "llm_execution":
            payload["messages"][-1].content += "\n\nEXECUTION MIDDLEWARE"
        return next_call(payload)

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    provider = Provider()
    monkeypatch.setattr("aegis.plugins.fire_middleware", fire_middleware)

    agent = Agent(config=cfg, provider=provider, session=Session.create(), cwd=tmp_path)
    agent.run("hello")

    assert "REQUEST MIDDLEWARE" in provider.user_messages[-1]
    assert "EXECUTION MIDDLEWARE" in provider.user_messages[-1]
    user_message = next(m.content for m in agent.session.messages if m.role == "user")
    assert user_message == "hello"


# --- #6 tool availability --------------------------------------------------
def test_unusable_tools_hidden_from_model():
    from aegis.tools.base import Tool
    from aegis.tools.registry import ToolRegistry

    class Gated(Tool):
        name = "gated"
        toolset = "core"
        def available(self): return False, "missing dep"

    reg = ToolRegistry()
    reg.register(Gated())
    assert "gated" not in {t.name for t in reg.available(["core"])}          # filtered by default
    assert "gated" in {t.name for t in reg.available(["core"], only_usable=False)}


def test_tools_doctor_flags_unavailable(capsys):
    from aegis.cli.main import cmd_tools
    from aegis.config import Config

    class Args:
        action = "doctor"
    cmd_tools(Args(), Config.load())
    out = capsys.readouterr().out
    assert "usable in this environment" in out


# --- #3 compaction metadata ------------------------------------------------
def test_compaction_records_metadata(tmp_path):
    from aegis.types import LLMResponse, ToolCall

    class Tiny(FakeProvider):
        context_length = 1                              # force should_compress immediately
        def complete(self, messages, **k):
            if not getattr(self, "_done", False):
                self._done = True
                return LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."})])
            return LLMResponse(text="final")

    a = _agent(Tiny(), tmp_path)
    a.run("work")
    comps = a.session.meta.get("compactions")
    assert comps and comps[0]["tokens_before"] >= comps[0]["tokens_after"]
    assert "reason" in comps[0]


def test_compaction_split_records_session_provenance(tmp_path):
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message

    class Windowed(FakeProvider):
        context_length = 100_000

    store = SessionStore()
    session = Session.create("long task")
    session.messages = [Message.user(("prior context " * 2000) + str(i)) for i in range(80)]
    provider = Windowed([LLMResponse(text="summary"), LLMResponse(text="final")])
    a = _agent(provider, tmp_path, store=store)
    # Exercise the split path: this fixture is sized so the post-compaction window drops
    # below 0.75 (but not below the default 0.50) — pin the threshold to keep it deterministic.
    a.config.data["agent"]["compression"]["threshold"] = 0.75
    a._context_engine = None   # rebuild the engine so it picks up the pinned threshold
    a.session = session
    a.tool_context.session = session

    out = a.run("continue")

    assert out.content == "final"
    assert a.session.parent_id == session.id
    parent = store.load(session.id)
    child = store.load(a.session.id)
    assert parent.meta["end_reason"] == "compression"
    assert child.meta["creator_kind"] == "compression"
    assert child.meta["reason"] == "context_compaction"
    assert child.meta["lineage_root"] == session.id
    assert child.meta["lineage_depth"] == 1
    assert child.id in parent.meta["child_sessions"]
    compaction = child.meta["compactions"][0]
    assert compaction["split"] is True
    assert compaction["parent_session"] == parent.id
    assert compaction["child_session"] == child.id


def test_manual_compaction_split_records_session_provenance(tmp_path):
    from aegis.agent.loop import compact_now
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message

    class Windowed(FakeProvider):
        context_length = 100_000

    store = SessionStore()
    session = Session.create("manual long task")
    session.messages = [Message.user(("manual context " * 2000) + str(i)) for i in range(40)]
    provider = Windowed([LLMResponse(text="manual summary")])
    a = _agent(provider, tmp_path, store=store)
    a.session = session
    a.tool_context.session = session

    compact_now(a, preserve_last=1)

    parent = store.load(session.id)
    child = store.load(a.session.id)
    assert parent.meta["end_reason"] == "manual_compression"
    assert child.meta["creator_kind"] == "manual_compression"
    compaction = child.meta["compactions"][0]
    assert compaction["manual"] is True
    assert compaction["split"] is True
    assert compaction["parent_session"] == parent.id
    assert compaction["child_session"] == child.id


def test_manual_compaction_skips_when_session_lock_held(tmp_path):
    from aegis.agent.loop import compact_now
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message

    class Windowed(FakeProvider):
        context_length = 100_000

    store = SessionStore()
    session = Session.create("locked manual task")
    session.messages = [Message.user(("locked context " * 2000) + str(i)) for i in range(40)]
    store.save(session)
    assert store.try_acquire_compression_lock(session.id, "other-holder") is True

    provider = Windowed([LLMResponse(text="manual summary")])
    a = _agent(provider, tmp_path, store=store)
    a.session = session
    a.tool_context.session = session
    events = []

    result = compact_now(a, session, emit=events.append, preserve_last=1)

    assert result.id == session.id
    assert any(event.get("type") == "compaction_skipped" for event in events)
    assert store.children(session.id) == []
    assert store.get_compression_lock_holder(session.id) == "other-holder"


# --- #8 failure modes ------------------------------------------------------
def test_crashed_tool_does_not_break_run(tmp_path):
    from aegis.tools.base import Tool, ToolResult  # noqa: F401
    from aegis.types import LLMResponse, ToolCall

    class Boom(Tool):
        name = "boom"
        toolset = "core"
        parameters = {"type": "object", "properties": {}}
        def run(self, args, ctx):
            raise RuntimeError("kaboom")

    script = [LLMResponse(text="", tool_calls=[ToolCall("c1", "boom", {})]),
              LLMResponse(text="recovered")]
    a = _agent(FakeProvider(script), tmp_path)
    a.registry.register(Boom())
    out = a.run("trigger")
    assert out.content == "recovered"                 # crash became an error result, run continued


def test_loop_guard_hashes_equivalent_json_results_as_no_progress():
    from aegis.agent.guardrails import ToolLoopGuard

    guard = ToolLoopGuard(warn_after=2)
    args = {"path": "state.json"}

    assert guard.record("read_file", args, '{"b": 2, "a": 1}', False) is None
    warning = guard.record("read_file", args, '{\n  "a": 1,\n  "b": 2\n}', False)

    assert warning is not None
    assert "identical result 2" in warning


def test_loop_guard_landed_file_mutation_json_clears_failure_streak():
    from aegis.agent.guardrails import ToolLoopGuard, file_mutation_result_landed

    guard = ToolLoopGuard(
        warn_after=10,
        block_after=10,
        same_tool_warn_after=3,
    )
    args = {"path": "out.txt"}

    assert guard.record("write_file", args, '{"error": "disk full"}', True) is None
    assert guard.record("write_file", args, '{"bytes_written": 12, "path": "out.txt"}', True) is None
    assert guard.record("write_file", args, '{"error": "disk full"}', True) is None
    assert guard.check("write_file", args) is None
    assert file_mutation_result_landed("patch", '{"success": true}') is True
    assert file_mutation_result_landed("apply_patch", '{"files_modified": ["a.py"]}') is True


def test_hard_stop_guard_halts_repeated_tool_failures(tmp_path):
    from aegis.tools.base import Tool
    from aegis.types import LLMResponse, ToolCall

    class Boom(Tool):
        name = "boom"
        description = "Synthetic failing tool for loop-guard tests."
        toolset = "core"
        parameters = {"type": "object", "properties": {"n": {"type": "integer"}}}

        def run(self, args, ctx):
            raise RuntimeError(f"kaboom {args.get('n')}")

    script = [
        LLMResponse(text="", tool_calls=[ToolCall("c1", "boom", {"n": 1})]),
        LLMResponse(text="", tool_calls=[ToolCall("c2", "boom", {"n": 2})]),
        LLMResponse(text="unreached"),
    ]
    provider = FakeProvider(script)
    a = _agent(provider, tmp_path)
    a.config.data["tools"]["loop_hard_stop"] = True
    a.config.data["tools"]["loop_warn_after"] = 5
    a.config.data["tools"]["loop_block_after"] = 10
    a.config.data["tools"]["loop_same_tool_warn_after"] = 2
    a.config.data["tools"]["loop_same_tool_halt_after"] = 2
    a.registry.register(Boom())

    events = []
    out = a.run("trigger", events.append)

    assert provider.calls == 2
    assert "[tool loop halted]" in out.content
    assert "boom has failed 2 times this turn" in out.content
    assert any(event["type"] == "tool_loop_halted" for event in events)


def test_hard_stop_guard_halts_exact_repeated_failure_before_execution(tmp_path):
    from aegis.tools.base import Tool
    from aegis.types import LLMResponse, ToolCall

    calls = {"boom": 0}

    class Boom(Tool):
        name = "boom"
        description = "Synthetic failing tool for loop-guard tests."
        toolset = "core"
        parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

        def run(self, args, ctx):
            calls["boom"] += 1
            raise RuntimeError("same kaboom")

    script = [
        LLMResponse(text="", tool_calls=[ToolCall("c1", "boom", {"path": "same"})]),
        LLMResponse(text="", tool_calls=[ToolCall("c2", "boom", {"path": "same"})]),
        LLMResponse(text="", tool_calls=[ToolCall("c3", "boom", {"path": "same"})]),
        LLMResponse(text="unreached"),
    ]
    provider = FakeProvider(script)
    a = _agent(provider, tmp_path)
    a.config.data["tools"]["loop_hard_stop"] = True
    a.config.data["tools"]["loop_warn_after"] = 5
    a.config.data["tools"]["loop_block_after"] = 2
    a.config.data["tools"]["loop_same_tool_halt_after"] = 10
    a.registry.register(Boom())

    out = a.run("trigger")

    assert provider.calls == 3
    assert calls["boom"] == 2
    assert "[tool loop halted]" in out.content
    assert "this exact boom call has failed identically 2 times" in out.content


def test_hard_stop_guard_halts_no_progress_reads_before_execution(tmp_path):
    from aegis.types import LLMResponse, ToolCall

    script = [
        LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."})]),
        LLMResponse(text="", tool_calls=[ToolCall("c2", "list_dir", {"path": "."})]),
        LLMResponse(text="", tool_calls=[ToolCall("c3", "list_dir", {"path": "."})]),
        LLMResponse(text="unreached"),
    ]
    provider = FakeProvider(script)
    a = _agent(provider, tmp_path)
    a.config.data["tools"]["loop_hard_stop"] = True
    a.config.data["tools"]["loop_no_progress_block_after"] = 2

    out = a.run("trigger")

    assert provider.calls == 3
    assert "[tool loop halted]" in out.content
    assert "list_dir call returned the same result 2 times" in out.content


def test_unknown_tool_call_is_handled(tmp_path):
    from aegis.types import LLMResponse, ToolCall
    script = [LLMResponse(text="", tool_calls=[ToolCall("c1", "no_such_tool", {})]),
              LLMResponse(text="ok")]
    provider = FakeProvider(script)
    a = _agent(provider, tmp_path)
    out = a.run("x")
    assert out.content == "ok"
    assert provider.calls == 2
    assert any(m.role == "tool" and "unknown tool" in (m.content or "") for m in a.session.messages)


def test_empty_tool_call_name_gets_recovery_result(tmp_path):
    from aegis.agent.invalid_tool_calls import INVALID_TOOL_CALL_NAME
    from aegis.types import LLMResponse, ToolCall

    script = [LLMResponse(text="", tool_calls=[ToolCall("c1", "", {})]),
              LLMResponse(text="ok")]
    provider = FakeProvider(script)
    a = _agent(provider, tmp_path)
    events = []

    out = a.run("x", events.append)

    assert out.content == "ok"
    assert provider.calls == 2
    assistant_tools = [m for m in a.session.messages if m.role == "assistant" and m.tool_calls]
    assert assistant_tools[0].tool_calls[0].name == INVALID_TOOL_CALL_NAME
    tool_results = [m for m in a.session.messages if m.role == "tool" and m.tool_call_id == "c1"]
    assert tool_results[0].name == INVALID_TOOL_CALL_NAME
    assert "tool name was empty" in tool_results[0].content
    assert any(e["type"] == "invalid_tool_call_recovery" for e in events)


def test_invalid_tool_call_recovery_is_bounded(tmp_path):
    from aegis.types import LLMResponse, ToolCall

    script = [
        LLMResponse(text="", tool_calls=[ToolCall("c1", "", {})]),
        LLMResponse(text="", tool_calls=[ToolCall("c2", "", {})]),
        LLMResponse(text="", tool_calls=[ToolCall("c3", "", {})]),
        LLMResponse(text="unreached"),
    ]
    provider = FakeProvider(script)
    a = _agent(provider, tmp_path)

    out = a.run("x")

    assert provider.calls == 3
    assert "[invalid tool call halted]" in out.content
    assert "unreached" not in out.content
    assert sum(
        1
        for m in a.session.messages
        if m.role == "tool" and "tool name was empty" in (m.content or "")
    ) == 3


def test_session_save_crash_does_not_break_run(tmp_path):
    from aegis.types import LLMResponse, ToolCall

    class BadStore:
        def save(self, session): raise OSError("disk full")

    script = [LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."})]),
              LLMResponse(text="done anyway")]
    a = _agent(FakeProvider(script), tmp_path, store=BadStore())
    out = a.run("x")
    assert out.content == "done anyway"               # save failures are swallowed mid-loop


def test_turn_start_is_persisted_before_provider_call(tmp_path):
    from aegis.types import LLMResponse

    class SpyStore:
        def __init__(self):
            self.snapshots = []
            self.metas = []

        def save(self, session):
            self.snapshots.append([
                (m.role, m.content)
                for m in session.messages
            ])
            self.metas.append(dict(session.meta))

    class ChecksEarlySave:
        context_length = 200_000
        name = "f"
        model = "m"
        api_mode = None
        auth = None

        def __init__(self, store):
            self.store = store

        def describe(self): return "f"

        def complete(self, messages, **k):
            assert self.store.snapshots
            first = self.store.snapshots[0]
            assert first[0][0] == "system"
            assert first[-1] == ("user", "persist me")
            meta = self.store.metas[0]
            assert meta["turn_id"].startswith("turn_")
            assert meta["last_turn_id"] == meta["turn_id"]
            assert meta["trace_id"].startswith("trace_")
            assert meta["last_trace_id"] == meta["trace_id"]
            return LLMResponse(text="saved")

    store = SpyStore()
    a = _agent(ChecksEarlySave(store), tmp_path, store=store)

    out = a.run("persist me")

    assert out.content == "saved"
    assert len(store.snapshots) >= 2


def test_turn_prologue_resets_identity_and_api_request_state_each_run(tmp_path):
    from aegis.types import LLMResponse, Usage

    class CapturingProvider:
        context_length = 200_000
        name = "f"
        model = "m"
        api_mode = None
        auth = None

        def __init__(self):
            self.agent = None
            self.calls = []

        def describe(self): return "f"

        def complete(self, messages, **kwargs):
            agent = self.agent
            self.calls.append({
                "turn_id": agent._current_turn_id,
                "api_request_id": agent._current_api_request_id,
                "last_api_request_id": agent._last_api_request_id,
                "api_request_count": agent._turn_api_request_count,
                "active_response_id": agent._active_response_id,
                "active_response_cancelled": agent._active_response_cancelled,
                "metadata": dict(kwargs.get("metadata") or {}),
            })
            return LLMResponse(text=f"done {len(self.calls)}")

    provider = CapturingProvider()
    a = _agent(provider, tmp_path)
    provider.agent = a
    a._current_api_request_id = "stale_api"
    a._last_api_request_id = "stale_last"
    a._turn_api_request_count = 99
    a._active_response_id = "stale_response"
    a._active_response_cancelled = "stale_cancelled"
    a._strip_thinking = True
    a._overflow_retried = True
    a._compact_stuck = True
    a._last_turn_usage = Usage(input_tokens=42)

    first = a.run("one")
    first_call = provider.calls[-1]
    first_turn_id = first_call["turn_id"]
    first_api_id = first_call["api_request_id"]

    a._current_api_request_id = "leaked_api"
    a._last_api_request_id = "leaked_last"
    a._turn_api_request_count = 77
    a._active_response_id = "leaked_response"
    a._active_response_cancelled = "leaked_cancelled"
    a.cancel_event.set()
    second = a.run("two")
    second_call = provider.calls[-1]

    assert first.content == "done 1"
    assert second.content == "done 2"
    assert first_turn_id.startswith("turn_")
    assert second_call["turn_id"].startswith("turn_")
    assert second_call["turn_id"] != first_turn_id
    assert first_api_id.startswith("api_")
    assert second_call["api_request_id"].startswith("api_")
    assert second_call["api_request_id"] != first_api_id
    assert first_call["api_request_count"] == 1
    assert second_call["api_request_count"] == 1
    assert first_call["api_request_id"] == first_call["last_api_request_id"]
    assert second_call["api_request_id"] == second_call["last_api_request_id"]
    assert first_call["active_response_id"] == ""
    assert first_call["active_response_cancelled"] == ""
    assert second_call["active_response_id"] == ""
    assert second_call["active_response_cancelled"] == ""
    assert second_call["metadata"]["turn_id"] == second_call["turn_id"]
    assert a._current_api_request_id == ""
    assert a._last_api_request_id == second_call["api_request_id"]
    assert a._turn_api_request_count == 1
    assert a.session.meta["turn_id"] == second_call["turn_id"]
    assert a.session.meta["last_turn_id"] == second_call["turn_id"]
    assert a.session.meta["last_api_request_id"] == second_call["api_request_id"]
    assert a.session.meta["last_turn_api_request_count"] == 1
    assert not a.cancel_event.is_set()


def test_direct_run_conversation_starts_turn_prologue(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.agent import loop
    from aegis.config import Config
    from aegis.session import Session
    from aegis.types import LLMResponse, Message, Usage

    class CapturingProvider:
        context_length = 200_000
        name = "f"
        model = "m"
        api_mode = None
        auth = None

        def __init__(self):
            self.agent = None
            self.seen = {}

        def describe(self): return "f"

        def complete(self, messages, **kwargs):
            agent = self.agent
            self.seen = {
                "cancelled": agent.cancel_event.is_set(),
                "strip_thinking": getattr(agent, "_strip_thinking", None),
                "overflow_retried": getattr(agent, "_overflow_retried", None),
                "compact_stuck": getattr(agent, "_compact_stuck", None),
                "turn_id": getattr(agent, "_current_turn_id", ""),
                "api_request_id": getattr(agent, "_current_api_request_id", ""),
                "metadata": dict(kwargs.get("metadata") or {}),
            }
            return LLMResponse(text="direct ok")

    provider = CapturingProvider()
    session = Session.create()
    session.messages = [Message.system("s"), Message.user("direct")]
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    agent = Agent(config=cfg, provider=provider, session=session, cwd=tmp_path)
    provider.agent = agent
    agent.cancel_event.set()
    agent._strip_thinking = True
    agent._overflow_retried = True
    agent._compact_stuck = True
    agent._current_api_request_id = "stale_api"
    agent._last_turn_usage = Usage(input_tokens=99)

    out = loop.run_conversation(agent)

    assert out.content == "direct ok"
    assert provider.seen["cancelled"] is False
    assert provider.seen["strip_thinking"] is False
    assert provider.seen["overflow_retried"] is False
    assert provider.seen["compact_stuck"] is False
    assert provider.seen["turn_id"].startswith("turn_")
    assert provider.seen["api_request_id"].startswith("api_")
    assert provider.seen["metadata"]["turn_id"] == provider.seen["turn_id"]
    assert agent._current_api_request_id == ""


def test_prior_turn_tool_use_does_not_trigger_empty_nudge(tmp_path):
    from aegis.types import LLMResponse, ToolCall

    script = [
        LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."})]),
        LLMResponse(text="first done"),
        LLMResponse(text=""),
        LLMResponse(text=""),
        LLMResponse(text=""),
        LLMResponse(text=""),
    ]
    provider = FakeProvider(script)
    a = _agent(provider, tmp_path)
    events = []

    first = a.run("use a tool", events.append)
    second = a.run("no tool empty", events.append)

    assert first.content == "first done"
    assert second.content == ""
    assert provider.calls == 6
    assert not [event for event in events if event["type"] == "empty_nudge"]
    assert [event["n"] for event in events if event["type"] == "empty_retry"] == [1, 2, 3]


def test_provider_error_surfaces_without_crashing(tmp_path):
    class Exploding:
        context_length = 200_000
        name = "f"
        model = "m"
        api_mode = None
        auth = None
        def describe(self): return "f"
        def complete(self, messages, **k): raise ConnectionError("network down")

    a = _agent(Exploding(), tmp_path)
    out = a.run("x")
    assert "provider error" in out.content.lower()
