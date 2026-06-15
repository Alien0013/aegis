"""Deferred tool schemas: name-only until tool_search activates them."""

from __future__ import annotations

from aegis.agent.agent import Agent
from aegis.config import Config
from aegis.session import Session
from aegis.tools.base import ToolContext
from aegis.tools.devtools import ToolSearchTool


def _agent():
    return Agent.create(Config.load(), session=Session.create())


def test_deferred_names_respect_config_and_activation():
    a = _agent()
    deferred = a.deferred_tool_names()
    assert "generate_image" in deferred and "tool_search" not in deferred
    a.activated_tools.add("generate_image")
    assert "generate_image" not in a.deferred_tool_names()
    a.config.data["tools"]["defer_schemas"] = False
    assert a.deferred_tool_names() == set()


def test_deferred_schemas_off_the_wire_but_indexed_in_prompt():
    a = _agent()
    available = a.registry.available(a.config.get("tools.toolsets", ["core"]))
    deferred = a.deferred_tool_names(available)
    live = [t.name for t in available if t.name not in deferred]
    assert "generate_image" not in live          # schema withheld
    assert "bash" in live and "tool_search" in live
    prompt = a._build_system_prompt()
    assert "Deferred tools" in prompt and "generate_image" in prompt


def test_tool_search_activates_deferred_tool():
    a = _agent()
    ctx = ToolContext(cwd=a.cwd, config=a.config, agent=a)
    r = ToolSearchTool().run({"query": "generate_image"}, ctx)
    assert not r.is_error
    assert "activated `generate_image`" in r.content and "parameters" in r.content
    assert "generate_image" in a.activated_tools
    # second search doesn't re-activate (already live)
    r2 = ToolSearchTool().run({"query": "generate_image"}, ctx)
    assert "activated" not in r2.display


def test_tool_search_and_deferred_index_respect_disabled_tools():
    a = _agent()
    a.config.data["tools"]["disabled"] = ["generate_image"]
    ctx = ToolContext(cwd=a.cwd, config=a.config, agent=a)

    r = ToolSearchTool().run({"query": "generate_image"}, ctx)

    assert "generate_image" not in r.content
    assert "generate_image" not in a._deferred_index_block()


def test_deferred_index_is_stable_across_activation():
    """Prefix-cache safety: activating a tool must not change the system prompt."""
    a = _agent()
    before = a._deferred_index_block()
    a.activated_tools.add("generate_image")
    assert a._deferred_index_block() == before
