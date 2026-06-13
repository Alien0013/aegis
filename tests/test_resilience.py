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
        context_length = 200_000; name = "f"; model = "m"; api_mode = None; auth = None
        def __init__(self): self.agent = None; self.n = 0
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


# --- #6 tool availability --------------------------------------------------
def test_unusable_tools_hidden_from_model():
    from aegis.tools.base import Tool
    from aegis.tools.registry import ToolRegistry

    class Gated(Tool):
        name = "gated"; toolset = "core"
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
        name = "boom"; toolset = "core"
        parameters = {"type": "object", "properties": {}}
        def run(self, args, ctx):
            raise RuntimeError("kaboom")

    script = [LLMResponse(text="", tool_calls=[ToolCall("c1", "boom", {})]),
              LLMResponse(text="recovered")]
    a = _agent(FakeProvider(script), tmp_path)
    a.registry.register(Boom())
    out = a.run("trigger")
    assert out.content == "recovered"                 # crash became an error result, run continued


def test_unknown_tool_call_is_handled(tmp_path):
    from aegis.types import LLMResponse, ToolCall
    script = [LLMResponse(text="", tool_calls=[ToolCall("c1", "no_such_tool", {})]),
              LLMResponse(text="ok")]
    a = _agent(FakeProvider(script), tmp_path)
    out = a.run("x")
    assert out.content == "ok"
    assert any(m.role == "tool" and "unknown tool" in (m.content or "") for m in a.session.messages)


def test_session_save_crash_does_not_break_run(tmp_path):
    from aegis.types import LLMResponse, ToolCall

    class BadStore:
        def save(self, session): raise OSError("disk full")

    script = [LLMResponse(text="", tool_calls=[ToolCall("c1", "list_dir", {"path": "."})]),
              LLMResponse(text="done anyway")]
    a = _agent(FakeProvider(script), tmp_path, store=BadStore())
    out = a.run("x")
    assert out.content == "done anyway"               # save failures are swallowed mid-loop


def test_provider_error_surfaces_without_crashing(tmp_path):
    class Exploding:
        context_length = 200_000; name = "f"; model = "m"; api_mode = None; auth = None
        def describe(self): return "f"
        def complete(self, messages, **k): raise ConnectionError("network down")

    a = _agent(Exploding(), tmp_path)
    out = a.run("x")
    assert "provider error" in out.content.lower()
