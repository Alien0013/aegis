"""Parallel subagents + registry, iteration-budget refund, pluggable context engine,
tool-call XML trajectory export."""

from __future__ import annotations


# --- #1 parallel subagents + status registry -------------------------------
def test_subagent_parallel_and_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.agent.agent as am
    from aegis.config import Config
    from aegis.runs import RunStore
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
    first = next(v for v in _REGISTRY.values() if v.get("task") == "alpha")
    assert first["run_id"].startswith("run_")
    assert first["session_id"].startswith("sess_")
    assert RunStore().get(first["run_id"])["surface"] == "subagent"
    # parallel tasks -> all run, each labelled, registry records them
    r = SubagentTool().run({"tasks": ["a", "b", "c"]}, ctx)
    assert all(f"did:{t}" in r.content for t in ("a", "b", "c"))
    assert r.content.count("## subagent") == 3
    assert len([v for v in _REGISTRY.values() if v.get("status") == "done"]) >= 4
    # neither task nor tasks -> error
    assert SubagentTool().run({}, ctx).is_error


def test_subagent_tasks_expand_context_references(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import aegis.agent.agent as am
    from aegis.config import Config
    from aegis.tools.agentic import SubagentTool, _REGISTRY
    from aegis.tools.base import ToolContext

    (tmp_path / "brief.md").write_text("subagent attached context", encoding="utf-8")
    seen = {}

    class Child:
        def __init__(self):
            self._depth = 0

        def run(self, task, on_event=None):
            seen["task"] = task
            return type("R", (), {"content": "done"})()

    monkeypatch.setattr(am.Agent, "create", staticmethod(lambda cfg, session=None, cwd=None: Child()))

    _REGISTRY.clear()
    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    result = SubagentTool().run({"task": "review @file:brief.md"}, ctx)

    assert not result.is_error
    assert "subagent attached context" in seen["task"]
    assert "<file path=" in seen["task"]


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


def test_deferred_tool_selectors_cover_dynamic_sources():
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.tools.base import Tool, ToolContext
    from aegis.tools.devtools import ToolSearchTool
    from aegis.tools.registry import ToolRegistry

    class FakeTool(Tool):
        def __init__(self, name, *, source="", toolset="core"):
            self.name = name
            self.description = f"{name} tool"
            self.source = source
            self.toolset = toolset
            self.parameters = {"type": "object", "properties": {"value": {"type": "string"}}}

    reg = ToolRegistry()
    reg.register_all([
        FakeTool("mcp__fs__read", source="mcp", toolset="mcp"),
        FakeTool("plugin_do", source="plugin"),
        FakeTool("local_big"),
        FakeTool("small"),
        ToolSearchTool(),
    ])
    cfg = Config.load()
    cfg.data.setdefault("tools", {})["defer_schemas"] = True
    cfg.data["tools"]["deferred"] = ["source:mcp", "plugin:*", "glob:local_*"]

    class FakeAgent:
        def __init__(self):
            self.config = cfg
            self.registry = reg
            self.activated_tools = set()

        def deferred_tool_names(self, available=None):
            return Agent.deferred_tool_names(self, available)

    agent = FakeAgent()
    assert agent.deferred_tool_names(reg.all()) == {"mcp__fs__read", "plugin_do", "local_big"}
    block = Agent._deferred_index_block(agent)
    assert "mcp__fs__read" in block and "plugin_do" in block and "small" not in block

    result = ToolSearchTool().run({"query": "read"}, ToolContext(agent=agent))

    assert not result.is_error
    assert "activated `mcp__fs__read`" in result.content
    assert "mcp__fs__read" in agent.activated_tools
    assert "mcp__fs__read" not in agent.deferred_tool_names(reg.all())


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


def test_context_engine_lifecycle_hooks(tmp_path):
    from aegis.agent import context_engine as ce
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message

    events = []

    class HookedEngine:
        name = "hooked"

        def __init__(self):
            self.done = False

        def should_compress(self, messages, context_length, overhead_tokens=0):
            return not self.done and len(messages) > 4

        def compress(self, messages, provider, **kw):
            self.done = True
            return [messages[0], Message.assistant("compressed"), messages[-1]]

        def tools(self):
            return []

        def on_session_start(self, agent):
            events.append(("start", agent.session.id))

        def on_pre_compress(self, agent, session):
            events.append(("pre", session.id))

        def on_session_switch(self, agent, old_session, new_session, reason=""):
            events.append(("switch", old_session.id, new_session.id, reason))

    class Provider:
        context_length = 100_000
        name = "fake"
        model = "fake"
        api_mode = None
        auth = None

        def complete(self, messages, **_kwargs):
            return LLMResponse(text="done")

    ce.register("hooked", HookedEngine)
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["agent"]["context_engine"] = "hooked"
    store = SessionStore()
    session = Session.create("hook test")
    session.messages = [Message.user(f"old {i}") for i in range(8)]
    agent = Agent(config=cfg, provider=Provider(), session=session, cwd=tmp_path, store=store)

    agent.run("go")

    assert events[0] == ("start", session.id)
    assert ("pre", session.id) in events
    switch = next(e for e in events if e[0] == "switch")
    assert switch[1] == session.id and switch[3] == "compression"
    assert agent.session.parent_id == session.id


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
