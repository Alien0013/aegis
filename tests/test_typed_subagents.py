"""Typed subagents: read-only specialist types + continuation."""

from __future__ import annotations

import re

import pytest

from aegis.agent.agent import Agent
from aegis.config import Config
from aegis.tools.agentic import _READONLY_TOOLS, SubagentTool
from aegis.tools.base import ToolContext
from aegis.types import Message


@pytest.fixture
def capture(monkeypatch):
    seen = {}

    def fake_run(self, task, on_event=None):
        self.ensure_system_prompt(force=True)
        seen["tools"] = {t.name for t in self.registry.all()}
        seen.setdefault("tasks", []).append(task if isinstance(task, str) else task.content)
        seen["reasoning"] = getattr(self, "reasoning", "")
        seen["system_prompt"] = self.session.messages[0].content
        seen["prompt_parts"] = list(self.session.meta.get("prompt_parts") or [])
        seen["session_meta"] = dict(self.session.meta)
        seen.setdefault("task_ids", []).append(getattr(self.tool_context, "task_id", ""))
        return Message.assistant("child answer")

    monkeypatch.setattr(Agent, "run", fake_run)
    return seen


def _ctx(tmp_path):
    return ToolContext(cwd=tmp_path, config=Config.load())


def test_explore_type_is_readonly(tmp_path, capture):
    r = SubagentTool().run({"task": "find the config loader", "agent_type": "explore"},
                           _ctx(tmp_path))
    assert not r.is_error
    assert capture["tools"] <= _READONLY_TOOLS          # whitelist enforced
    assert "write_file" not in capture["tools"] and "bash" not in capture["tools"]
    assert capture["tasks"][0] == "find the config loader"
    assert not capture["tasks"][0].startswith("You are a READ-ONLY explore agent")
    assert "You are a READ-ONLY explore agent" in capture["system_prompt"]
    assert any(p["name"] == "subagent_role:explore" for p in capture["prompt_parts"])


def test_general_type_keeps_full_tools(tmp_path, capture):
    r = SubagentTool().run({"task": "do it"}, _ctx(tmp_path))
    assert not r.is_error
    assert "write_file" in capture["tools"] and "bash" in capture["tools"]


def test_general_subagent_is_leaf_by_default(tmp_path, capture):
    r = SubagentTool().run({"task": "do it"}, _ctx(tmp_path))

    assert not r.is_error
    assert "spawn_subagent" not in capture["tools"]


def test_orchestrator_role_keeps_delegation_when_depth_allows(tmp_path, capture):
    config = Config.load()
    config.data["agent"]["max_spawn_depth"] = 2
    ctx = ToolContext(cwd=tmp_path, config=config)

    r = SubagentTool().run({"task": "coordinate workers", "role": "orchestrator"}, ctx)

    assert not r.is_error
    assert "spawn_subagent" in capture["tools"]


def test_subagent_depth_limit_blocks_grandchildren_by_default(tmp_path):
    class Parent:
        _depth = 1

    ctx = _ctx(tmp_path)
    ctx.agent = Parent()

    r = SubagentTool().run({"task": "spawn lower"}, ctx)

    assert r.is_error
    assert "depth limit" in r.content


def test_requested_toolsets_are_child_only(tmp_path, monkeypatch):
    seen = {}

    def fake_run(self, task, on_event=None):
        seen["child_toolsets"] = self.config.get("tools.toolsets")
        return Message.assistant("ok")

    monkeypatch.setattr(Agent, "run", fake_run)
    config = Config.load()
    config.data["tools"]["toolsets"] = ["core", "browser"]
    ctx = ToolContext(cwd=tmp_path, config=config)

    r = SubagentTool().run({"task": "use browser only", "toolsets": ["browser", "lsp"]}, ctx)

    assert not r.is_error
    assert seen["child_toolsets"] == ["browser"]
    assert config.data["tools"]["toolsets"] == ["core", "browser"]


def test_requested_toolsets_cannot_widen_child(tmp_path, monkeypatch):
    from aegis.tools.agentic import _NO_TOOLSETS

    seen = {}

    def fake_run(self, task, on_event=None):
        seen["child_toolsets"] = self.config.get("tools.toolsets")
        return Message.assistant("ok")

    monkeypatch.setattr(Agent, "run", fake_run)
    config = Config.load()
    config.data["tools"]["toolsets"] = ["core"]
    ctx = ToolContext(cwd=tmp_path, config=config)

    r = SubagentTool().run({"task": "use browser", "toolsets": ["browser"]}, ctx)

    assert not r.is_error
    assert seen["child_toolsets"] == _NO_TOOLSETS
    assert config.data["tools"]["toolsets"] == ["core"]


def test_unknown_type_rejected(tmp_path):
    r = SubagentTool().run({"task": "x", "agent_type": "ninja"}, _ctx(tmp_path))
    assert r.is_error and "unknown agent_type" in r.content


def test_continuation_reuses_child(tmp_path, capture):
    tool = SubagentTool()
    r1 = tool.run({"task": "step one", "agent_type": "plan"}, _ctx(tmp_path))
    sid = re.search(r"subagent id: (\S+) ", r1.content).group(1)
    r2 = tool.run({"task": "refine step 3", "continue_id": sid}, _ctx(tmp_path))
    assert not r2.is_error and "child answer" in r2.content
    assert capture["task_ids"][0] == sid
    assert capture["task_ids"][-1] == sid
    assert capture["tasks"][-1] == "refine step 3"      # follow-up went to the same child
    assert capture["session_meta"]["agent_type"] == "plan"
    assert "READ-ONLY planning architect" in capture["system_prompt"]
    assert any(p["name"] == "subagent_role:plan" for p in capture["prompt_parts"])
    r3 = tool.run({"task": "x", "continue_id": "sub_nope"}, _ctx(tmp_path))
    assert r3.is_error


def test_continuation_notifies_parent_memory(tmp_path, capture):
    from aegis.session import Session

    calls = []

    class Memory:
        def on_delegation(self, task, result):
            calls.append((task, result))

    class Parent:
        session = Session.create()
        memory = Memory()

    ctx = ToolContext(cwd=tmp_path, config=Config.load(), agent=Parent())
    tool = SubagentTool()
    r1 = tool.run({"task": "step one", "agent_type": "plan"}, ctx)
    sid = re.search(r"subagent id: (\S+) ", r1.content).group(1)
    calls.clear()

    r2 = tool.run({"task": "refine step 3", "continue_id": sid}, ctx)

    assert not r2.is_error
    assert calls == [("refine step 3", "child answer")]


def test_subagent_error_notifies_parent_memory(tmp_path, monkeypatch):
    from aegis.session import Session

    calls = []

    class Memory:
        def on_delegation(self, task, result):
            calls.append((task, result))

    class Parent:
        session = Session.create()
        memory = Memory()

    def boom(self, task, on_event=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(Agent, "run", boom)
    ctx = ToolContext(cwd=tmp_path, config=Config.load(), agent=Parent())

    r = SubagentTool().run({"task": "explode"}, ctx)

    assert not r.is_error
    assert "[subagent error] boom" in r.content
    assert calls == [("explode", "[subagent error] boom")]


def test_background_subagent_notifies_parent_memory(tmp_path, monkeypatch):
    from aegis.session import Session

    calls = []

    class Memory:
        def on_delegation(self, task, result):
            calls.append((task, result))

    class Parent:
        session = Session.create()
        memory = Memory()
        platform = None
        chat_id = None

    class Manager:
        def spawn(self, config, prompt, *, cwd=None, on_done=None, parent_session=None):
            task = type("Task", (), {
                "id": "bg_test",
                "prompt": prompt,
                "status": "done",
                "result": "done bg",
                "error": "",
                "run_id": "run_bg",
            })()
            if on_done is not None:
                on_done(task)
            return task.id

    monkeypatch.setattr("aegis.background.get_manager", lambda: Manager())
    ctx = ToolContext(cwd=tmp_path, config=Config.load(), agent=Parent())

    r = SubagentTool().run({"task": "bg task", "background": True}, ctx)

    assert not r.is_error
    assert calls == [("bg task", "done bg")]


def test_subagent_inherits_parent_runtime_controls(tmp_path, capture):
    from aegis.session import Session

    config = Config.load()
    parent_session = Session.create()
    parent_session.meta["runtime_controls"] = {
        "provider": "openai",
        "model": "gpt-5.5-pro",
        "reasoning_effort": "high",
        "reasoning_display": "live",
        "busy_mode": "steer",
    }

    class Parent:
        pass

    parent = Parent()
    parent.session = parent_session
    parent.config = config
    parent.memory = None

    ctx = ToolContext(cwd=tmp_path, config=config, agent=parent)
    r = SubagentTool().run({"task": "plan this", "agent_type": "plan"}, ctx)

    assert not r.is_error
    controls = capture["session_meta"]["runtime_controls"]
    assert controls["provider"] == "openai"
    assert controls["model"] == "gpt-5.5-pro"
    assert controls["reasoning_effort"] == "high"
    assert capture["session_meta"]["runtime"]["reasoning_display"] == "live"
    assert capture["session_meta"]["runtime"]["busy_mode"] == "steer"
    assert capture["reasoning"] == "high"


def test_subagent_terminal_backend_override_registered(tmp_path, capture):
    from aegis.tools import backends

    config = Config.load()
    config.data["tools"]["subagent_terminal_backend"] = "docker"
    ctx = ToolContext(cwd=tmp_path, config=config)

    r = SubagentTool().run({"task": "use isolated shell"}, ctx)
    sid = re.search(r"subagent id: (\S+) ", r.content).group(1)
    try:
        assert not r.is_error
        assert capture["task_ids"][0] == sid
        assert backends.effective_backend("local", sid) == "docker"
    finally:
        backends.clear_task_env_overrides(sid)


def test_subagent_registry_eviction_closes_child_lifecycle():
    from types import SimpleNamespace

    from aegis.tools import agentic

    closed = []

    class Memory:
        def shutdown(self):
            closed.append("memory")

    class Transport:
        def close(self):
            closed.append("transport")

    class Child:
        memory = Memory()
        provider = SimpleNamespace(transport=Transport())

        def end_session(self):
            closed.append("end_session")

    with agentic._REG_LOCK:
        agentic._REGISTRY.clear()
    try:
        agentic._register("old", status="done", agent=Child())
        for i in range(199):
            agentic._register(f"keep-{i}", status="done")
        agentic._register("new", status="done")

        assert "old" not in agentic._REGISTRY
        assert closed == ["end_session", "memory", "transport"]
    finally:
        with agentic._REG_LOCK:
            agentic._REGISTRY.clear()
