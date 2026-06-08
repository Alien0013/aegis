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
