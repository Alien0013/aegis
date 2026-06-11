"""MemoryProvider lifecycle hooks, subdirectory hints, .cursorrules, credit telemetry."""

from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    from aegis.config import Config
    return Config.load()


class RecordingProvider:
    """A memory provider that records every lifecycle hook it receives."""
    name = "recording"

    def __init__(self):
        self.calls = []

    def initialize(self, session_id="", **kw): self.calls.append(("initialize", session_id))
    def system_prompt_block(self): self.calls.append(("system_prompt_block",)); return "PROVIDER MEM"
    def prefetch(self, query, *, session_id=""): self.calls.append(("prefetch", query)); return "FETCHED:" + query[:10]
    def queue_prefetch(self, query, *, session_id=""): self.calls.append(("queue_prefetch",))
    def sync_turn(self, messages): self.calls.append(("sync_turn", len(messages)))
    def tools(self): self.calls.append(("tools",)); return []
    def on_session_end(self, messages): self.calls.append(("on_session_end",))
    def on_pre_compress(self, messages): self.calls.append(("on_pre_compress",)); return "keep this"
    def on_session_switch(self, *, old_session_id, new_session_id, **kw): self.calls.append(("on_session_switch", old_session_id, new_session_id))
    def on_delegation(self, task, result, **kw): self.calls.append(("on_delegation", task))
    def shutdown(self): self.calls.append(("shutdown",))


def test_manager_fans_out_every_hook(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.memory import MemoryManager
    p = RecordingProvider()
    mm = MemoryManager(config, external=p)

    mm.initialize("sess-1")
    assert mm.prefetch("hello world query") == "FETCHED:hello worl"
    mm.queue_prefetch("q")
    mm.sync_turn([1, 2, 3])
    assert mm.provider_tools() == []
    assert mm.on_pre_compress([]) == "keep this"
    mm.on_session_switch("old", "new")
    mm.on_delegation("do a thing", "did it")
    mm.on_session_end([])
    mm.shutdown()
    # provider system_prompt_block flows through build_context_block
    assert "PROVIDER MEM" in mm.build_context_block()

    kinds = [c[0] for c in p.calls]
    for hook in ("initialize", "prefetch", "queue_prefetch", "sync_turn", "tools",
                 "on_pre_compress", "on_session_switch", "on_delegation",
                 "on_session_end", "shutdown", "system_prompt_block"):
        assert hook in kinds, f"{hook} was never fanned out"


def test_hooks_are_fail_soft(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.memory import MemoryManager

    class Boom:
        def initialize(self, session_id="", **kw): raise RuntimeError("x")
        def prefetch(self, q, *, session_id=""): raise RuntimeError("x")
        def on_pre_compress(self, m): raise RuntimeError("x")
    mm = MemoryManager(config, external=Boom())
    mm.initialize("s")               # must not raise
    assert mm.prefetch("q") == ""    # swallowed -> empty
    assert mm.on_pre_compress([]) == ""


def test_provider_tools_registered_on_agent(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.tools.base import Tool, ToolResult

    class PMemTool(Tool):
        name = "provider_recall"
        description = "x"
        parameters = {"type": "object", "properties": {}}
        def run(self, args, ctx): return ToolResult.ok("ok")

    class P:
        def tools(self): return [PMemTool()]
    mm = MemoryManager(config, external=P())
    agent = Agent.create(config, memory=mm)
    assert agent.registry.get("provider_recall") is not None   # registered at construction


def test_prefetch_injected_into_user_message(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager

    class P:
        def prefetch(self, q, *, session_id=""): return "RELEVANT PAST FACT"
    agent = Agent.create(config, memory=MemoryManager(config, external=P()))

    captured = {}
    def fake_run_conversation(a, on_event=None):
        captured["first_user"] = next(m.content for m in a.session.messages if m.role == "user")
        from aegis.types import Message
        return Message.assistant("done")
    import aegis.agent.agent as am
    monkeypatch.setattr(am, "run_conversation", fake_run_conversation)
    agent.run("what did we decide?")
    assert "<retrieved_memory>" in captured["first_user"]
    assert "RELEVANT PAST FACT" in captured["first_user"]


def test_switch_and_end_fire_hooks(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.session import Session
    p = RecordingProvider()
    agent = Agent.create(config, memory=MemoryManager(config, external=p))
    new = Session.create(title="next")
    agent.switch_session(new)
    agent.end_session()
    kinds = [c[0] for c in p.calls]
    assert "on_session_switch" in kinds and "on_session_end" in kinds


# --- .cursorrules + subdirectory hints --------------------------------------
def test_cursorrules_recognized(tmp_path):
    from aegis.config import Workspace
    assert ".cursorrules" in Workspace.RULE_FILES
    (tmp_path / ".cursorrules").write_text("CURSOR PROJECT RULES")
    assert "CURSOR PROJECT RULES" in Workspace(cwd=tmp_path).rules()


def test_subdir_hints_inject_on_first_entry(tmp_path):
    from aegis.agent.subdir_hints import SubdirHintTracker
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "AGENTS.md").write_text("PACKAGE-LOCAL RULES")
    t = SubdirHintTracker(tmp_path)
    # first read inside pkg/ -> hint injected
    h1 = t.hints_for("read_file", {"path": "pkg/module.py"})
    assert "PACKAGE-LOCAL RULES" in h1 and "subdir_context" in h1
    # second time -> already seen, no repeat
    assert t.hints_for("read_file", {"path": "pkg/other.py"}) == ""
    # cwd itself is never hinted (loaded at startup)
    assert t.hints_for("read_file", {"path": "top.py"}) == ""


def test_subdir_hints_disabled_via_config(tmp_path, monkeypatch):
    config = _cfg(tmp_path, monkeypatch)
    config.data["agent"]["subdir_hints"] = False
    from aegis.agent.subdir_hints import hints_for_call
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "AGENTS.md").write_text("RULES")

    class FakeAgent:
        pass
    a = FakeAgent(); a.config = config
    assert hints_for_call(a, "read_file", {"path": "pkg/x.py"}, tmp_path) == ""


# --- credit/balance telemetry -----------------------------------------------
def test_credit_balance_capture():
    from aegis import ratelimit
    ratelimit.record({"x-ratelimit-remaining-credits": "12.50"}, "openrouter")
    bal = ratelimit.balance()
    assert bal.get("provider") == "openrouter" and bal.get("credits left") == "12.50"
    assert "credits left: 12.50" in ratelimit.summary()
