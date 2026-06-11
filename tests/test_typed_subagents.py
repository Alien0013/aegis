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
        seen["tools"] = {t.name for t in self.registry.all()}
        seen.setdefault("tasks", []).append(task if isinstance(task, str) else task.content)
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
    assert capture["tasks"][0].startswith("You are a READ-ONLY explore agent")


def test_general_type_keeps_full_tools(tmp_path, capture):
    r = SubagentTool().run({"task": "do it"}, _ctx(tmp_path))
    assert not r.is_error
    assert "write_file" in capture["tools"] and "bash" in capture["tools"]


def test_unknown_type_rejected(tmp_path):
    r = SubagentTool().run({"task": "x", "agent_type": "ninja"}, _ctx(tmp_path))
    assert r.is_error and "unknown agent_type" in r.content


def test_continuation_reuses_child(tmp_path, capture):
    tool = SubagentTool()
    r1 = tool.run({"task": "step one", "agent_type": "plan"}, _ctx(tmp_path))
    sid = re.search(r"subagent id: (\S+) ", r1.content).group(1)
    r2 = tool.run({"task": "refine step 3", "continue_id": sid}, _ctx(tmp_path))
    assert not r2.is_error and "child answer" in r2.content
    assert capture["tasks"][-1] == "refine step 3"      # follow-up went to the same child
    r3 = tool.run({"task": "x", "continue_id": "sub_nope"}, _ctx(tmp_path))
    assert r3.is_error
