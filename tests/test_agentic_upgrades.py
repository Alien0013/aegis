"""Parallel subagents + registry, iteration-budget refund, pluggable context engine,
tool-call XML trajectory export."""

from __future__ import annotations


# --- #1 parallel subagents + status registry -------------------------------
def test_subagent_parallel_and_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.agent.agent as am
    from aegis.config import Config
    from aegis.tools.agentic import SubagentTool, _REGISTRY
    from aegis.tools.base import ToolContext

    class Child:
        def __init__(self): self._depth = 0
        def run(self, task):
            return type("R", (), {"content": f"did:{task}"})()
    monkeypatch.setattr(am.Agent, "create", staticmethod(lambda cfg, session=None, cwd=None: Child()))

    _REGISTRY.clear()
    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    # single task
    r1 = SubagentTool().run({"task": "alpha"}, ctx)
    assert not r1.is_error and "did:alpha" in r1.content
    # parallel tasks -> all run, each labelled, registry records them
    r = SubagentTool().run({"tasks": ["a", "b", "c"]}, ctx)
    assert all(f"did:{t}" in r.content for t in ("a", "b", "c"))
    assert r.content.count("## subagent") == 3
    assert len([v for v in _REGISTRY.values() if v.get("status") == "done"]) >= 4
    # neither task nor tasks -> error
    assert SubagentTool().run({}, ctx).is_error


# --- iteration-budget refund ------------------------------------------------
def test_iteration_budget_refund():
    from aegis.agent.agent import IterationBudget
    b = IterationBudget(max_iterations=10)
    b.api_call_count = 3
    b.refund()
    assert b.api_call_count == 2
    b.api_call_count = 0
    b.refund()
    assert b.api_call_count == 0          # never goes negative


# --- #2 pluggable context engine -------------------------------------------
def test_context_engine_default_and_register():
    from aegis.agent import context_engine as ce
    from aegis.config import Config

    eng = ce.get_engine(Config.load())
    assert eng.name == "default" and eng.tools() == []

    class FakeEngine:
        name = "fake"
        def should_compress(self, m, c, o=0): return True
        def compress(self, m, p, **kw): return m[:1]
        def tools(self): return []
    ce.register("fake", FakeEngine)

    class Cfg:
        def get(self, k, d=None): return "fake" if k == "agent.context_engine" else d
    assert ce.get_engine(Cfg()).name == "fake"
    # default delegates to compaction
    from aegis.types import Message
    msgs = [Message.system("s")] + [Message.user("x")] * 50
    assert isinstance(eng.should_compress(msgs, 100, 0), bool)


# --- #3 tool-call XML trajectory format ------------------------------------
def test_trajectory_toolxml_format():
    from aegis.trajectory import _toolxml, _FORMATTERS
    assert "toolxml" in _FORMATTERS
    traj = {"messages": [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": "ok", "tool_calls": [{"name": "bash", "arguments": {"cmd": "ls"}}]},
        {"role": "tool", "content": "a\nb"},
        {"role": "assistant", "content": "done"},
    ]}
    out = _toolxml(traj)["messages"]
    assert '<tool_call>{"name": "bash"' in out[1]["content"]
    assert out[2]["content"].startswith("<tool_response>") and out[2]["content"].endswith("</tool_response>")
    assert out[3]["content"] == "done"
