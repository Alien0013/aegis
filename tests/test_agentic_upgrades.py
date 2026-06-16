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


def test_background_spawn_inherits_parent_runtime_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.surface as surface
    from aegis.background import BackgroundManager
    from aegis.config import Config
    from aegis.session import Session

    seen = {}

    class FakeRunner:
        def __init__(self, config, cwd=None, include_mcp=True):
            pass

        def load_or_create_session(self, session_id=None, title=None, surface="", meta=None, **_kwargs):
            seen["session_id"] = session_id
            seen["meta"] = meta or {}
            return type("S", (), {"id": session_id, "title": title, "meta": meta or {}})()

        def make_agent(self, **kwargs):
            seen["include_mcp"] = kwargs.get("include_mcp")
            seen["registry"] = kwargs.get("registry")
            return object()

        def run_prompt(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["meta"] = kwargs.get("meta") or seen.get("meta") or {}
            seen["session_id"] = getattr(kwargs.get("session"), "id", "") or seen.get("session_id", "")
            return type("R", (), {"text": "ok", "run_id": "run_bg"})()

        def close(self):
            seen["closed"] = seen.get("closed", 0) + 1

    parent = Session.create()
    parent.meta["runtime_controls"] = {
        "provider": "openai",
        "model": "gpt-5.5-pro",
        "reasoning_effort": "high",
        "reasoning_display": "live",
        "busy_mode": "steer",
    }
    monkeypatch.setattr(surface, "SurfaceRunner", FakeRunner)

    mgr = BackgroundManager()
    tid = mgr.spawn(Config.load(), "do it later", parent_session=parent)
    for _ in range(50):
        task = mgr.get(tid)
        if task and task.status != "running":
            break
        time.sleep(0.01)

    assert seen["session_id"] == f"background:{tid}"
    controls = seen["meta"]["runtime_controls"]
    assert controls["provider"] == "openai"
    assert controls["model"] == "gpt-5.5-pro"
    assert controls["reasoning_effort"] == "high"
    assert seen["meta"]["runtime"]["reasoning_display"] == "live"
    assert seen["meta"]["runtime"]["busy_mode"] == "steer"
    assert seen["closed"] == 1


def test_background_spawn_registers_subagent_terminal_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.surface as surface
    from aegis.background import BackgroundManager
    from aegis.config import Config
    from aegis.tools import backends

    class FakeRunner:
        def __init__(self, config, cwd=None, include_mcp=True):
            pass

        def load_or_create_session(self, session_id=None, title=None, surface="", meta=None, **_kwargs):
            return type("S", (), {"id": session_id, "title": title, "meta": meta or {}})()

        def make_agent(self, **_kwargs):
            return object()

        def run_prompt(self, prompt, **kwargs):
            return type("R", (), {"text": "ok", "run_id": "run_bg"})()

        def close(self):
            pass

    config = Config.load()
    config.data["tools"]["subagent_terminal_backend"] = "docker"
    monkeypatch.setattr(surface, "SurfaceRunner", FakeRunner)

    mgr = BackgroundManager()
    tid = mgr.spawn(config, "do it later", cwd=tmp_path)
    try:
        for _ in range(50):
            task = mgr.get(tid)
            if task and task.status != "running":
                break
            time.sleep(0.01)
        assert task is not None and task.status == "done"
        assert backends.effective_backend("local", tid) == "local"
    finally:
        backends.clear_task_env_overrides(tid)


def test_background_manager_rejects_at_capacity_and_records_completions(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import pytest
    import threading
    import time
    import aegis.surface as surface
    from aegis.background import BackgroundCapacityError, BackgroundManager
    from aegis.config import Config
    from aegis.tools.process_registry import process_registry

    active = 0
    max_seen = 0
    lock = threading.Lock()
    both_running = threading.Event()
    release = threading.Event()

    class FakeRunner:
        def __init__(self, config, cwd=None, include_mcp=True):
            pass

        def load_or_create_session(self, session_id=None, title=None, surface="", meta=None, **_kwargs):
            return type("S", (), {"id": session_id, "title": title, "meta": meta or {}})()

        def make_agent(self, **_kwargs):
            return object()

        def run_prompt(self, prompt, **kwargs):
            nonlocal active, max_seen
            with lock:
                active += 1
                max_seen = max(max_seen, active)
                if active >= 2:
                    both_running.set()
            try:
                release.wait(3)
                return type("R", (), {"text": f"ok {prompt}", "run_id": f"run_{prompt}"})()
            finally:
                with lock:
                    active -= 1

        def close(self):
            pass

    config = Config.load()
    config.data.setdefault("delegation", {})["max_background_children"] = 2
    monkeypatch.setattr(surface, "SurfaceRunner", FakeRunner)
    process_registry.drain_notifications()

    mgr = BackgroundManager()
    ids = [mgr.spawn(config, f"task{i}", session_meta={"agent_type": "review"}) for i in range(2)]
    assert both_running.wait(2)
    with pytest.raises(BackgroundCapacityError):
        mgr.spawn(config, "task2", session_meta={"agent_type": "review"})
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline:
        rows = mgr.list()
        if len(rows) == 2 and all(row["status"] != "running" for row in rows):
            break
        time.sleep(0.02)

    assert {row["id"] for row in mgr.list()} == set(ids)
    assert max_seen <= 2
    late_id = mgr.spawn(config, "task3", session_meta={"agent_type": "review"})
    deadline = time.time() + 5
    while time.time() < deadline:
        task = mgr.get(late_id)
        if task and task.status != "running":
            break
        time.sleep(0.02)
    events = mgr.completions()
    assert len(events) == 3
    assert {event["status"] for event in events} == {"done"}
    assert {event["agent_type"] for event in events} == {"review"}
    assert all(event["background"] is True for event in events)
    notifications = process_registry.drain_notifications()
    async_events = [event for event, text in notifications if event.get("type") == "async_delegation"]
    assert len(async_events) == 3
    assert any("ASYNC DELEGATION COMPLETE" in text and "ok task3" in text for _event, text in notifications)


def test_background_manager_batch_capacity_preflight():
    import pytest
    from aegis.background import BackgroundCapacityError, BackgroundManager
    from aegis.config import Config

    config = Config({"delegation": {"max_async_children": 2}})
    mgr = BackgroundManager()

    assert mgr.capacity(config) == {"max": 2, "running": 0, "available": 2}
    mgr.require_capacity(config, 2)
    with pytest.raises(BackgroundCapacityError) as exc:
        mgr.require_capacity(config, 3)
    assert "3 requested" in str(exc.value)


def test_background_manager_prunes_completed_records(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_HOME", str(tmp_path))
    import time
    import aegis.surface as surface
    from aegis.background import BackgroundManager
    from aegis.config import Config

    class FakeRunner:
        def __init__(self, config, cwd=None, include_mcp=True):
            pass

        def load_or_create_session(self, session_id=None, title=None, surface="", meta=None, **_kwargs):
            return type("S", (), {"id": session_id, "title": title, "meta": meta or {}})()

        def make_agent(self, **_kwargs):
            return object()

        def run_prompt(self, prompt, **kwargs):
            return type("R", (), {"text": f"ok {prompt}", "run_id": f"run_{prompt}"})()

        def close(self):
            pass

    config = Config.load()
    config.data.setdefault("delegation", {})["max_async_children"] = 10
    config.data.setdefault("delegation", {})["retain_completed_background_tasks"] = 2
    monkeypatch.setattr(surface, "SurfaceRunner", FakeRunner)

    mgr = BackgroundManager()
    ids = [mgr.spawn(config, f"task{i}") for i in range(5)]
    deadline = time.time() + 5
    while time.time() < deadline:
        if len(mgr.completions()) == 5:
            break
        time.sleep(0.02)

    rows = mgr.list()
    assert len(rows) == 2
    assert all(row["status"] == "done" for row in rows)
    assert {row["id"] for row in rows}.issubset(set(ids))
    assert len(mgr.completions()) == 5


def test_background_subagent_dispatches_bounded_multiple_tasks(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.tools.agentic import SubagentTool
    from aegis.tools.base import ToolContext

    class Manager:
        def __init__(self):
            self.prompts = []
            self.requested = 0

        def require_capacity(self, _config, requested):
            self.requested = requested

        def spawn(self, _config, prompt, **_kwargs):
            self.prompts.append(prompt)
            return f"bg_{len(self.prompts)}"

    manager = Manager()
    monkeypatch.setattr("aegis.background.get_manager", lambda: manager)
    ctx = ToolContext(cwd=tmp_path, config=Config.load())
    result = SubagentTool().run({"tasks": ["a", "b"], "background": True}, ctx)

    assert not result.is_error
    assert manager.requested == 2
    assert manager.prompts == ["a", "b"]
    assert "bg_1" in result.content
    assert "bg_2" in result.content


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
