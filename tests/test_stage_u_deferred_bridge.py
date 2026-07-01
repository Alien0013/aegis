"""Stage U deferred-tool bridge parity tests."""

from __future__ import annotations

import json

from conftest import FakeProvider

from aegis.agent.agent import Agent
from aegis.agent.loop import ToolExecutor
from aegis.config import Config
from aegis.session import Session
from aegis.tools.base import Tool, ToolContext, ToolResult
from aegis.tools.devtools import (
    ToolCallTool,
    ToolDescribeTool,
    ToolSearchTool,
    dev_tools,
)
from aegis.tools.permissions import PermissionEngine
from aegis.tools.registry import ToolRegistry
from aegis.types import ToolCall


class SyntheticTool(Tool):
    def __init__(
        self,
        name: str,
        *,
        toolset: str = "extra",
        description: str = "",
        properties: dict | None = None,
    ) -> None:
        self.name = name
        self.description = description or f"{name} scoped operation"
        self.toolset = toolset
        self.parameters = {
            "type": "object",
            "properties": properties or {"value": {"type": "string"}},
        }
        self.calls: list[dict] = []

    def run(self, args, ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args or {}))
        body = {"ran": self.name, "args": dict(args or {})}
        return ToolResult.ok(json.dumps(body), display=f"ran {self.name}", data=body)


class CountingSchemaTool(SyntheticTool):
    def __init__(self, name: str, **kwargs) -> None:
        super().__init__(name, **kwargs)
        self.schema_calls = 0

    def schema(self):
        self.schema_calls += 1
        return super().schema()


def _config(*, deferred: list[str] | None = None, disabled: list[str] | None = None) -> Config:
    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["tools"]["exec_mode"] = "full"
    cfg.data["tools"]["toolsets"] = ["core", "extra"]
    cfg.data["tools"]["defer_schemas"] = True
    cfg.data["tools"]["deferred"] = list(deferred or ["toolset:extra"])
    cfg.data["tools"]["disabled"] = list(disabled or [])
    return cfg


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all([*tools, ToolSearchTool(), ToolDescribeTool(), ToolCallTool()])
    return reg


def _agent(tmp_path, cfg: Config, reg: ToolRegistry) -> Agent:
    return Agent(
        config=cfg,
        provider=FakeProvider(),
        session=Session.create(),
        registry=reg,
        cwd=tmp_path,
    )


def test_dev_tools_register_all_bridge_tools() -> None:
    names = {tool.name for tool in dev_tools()}
    assert {"tool_search", "tool_describe", "tool_call"}.issubset(names)


def test_scoped_candidates_respect_toolsets_disabled_and_protected_names(tmp_path) -> None:
    cfg = _config(deferred=["glob:*"], disabled=["scoped_disabled"])
    reg = _registry(
        SyntheticTool("scoped_enabled", toolset="extra"),
        SyntheticTool("scoped_disabled", toolset="extra"),
        SyntheticTool("scoped_other", toolset="other"),
        SyntheticTool("bash", toolset="core"),
    )
    agent = _agent(tmp_path, cfg, reg)

    assert agent.deferred_tool_candidate_names() == {"scoped_enabled"}
    assert agent.deferred_tool_names() == {"scoped_enabled"}
    agent.activated_tools.add("scoped_enabled")
    assert agent.deferred_tool_candidate_names() == {"scoped_enabled"}
    assert agent.deferred_tool_names() == set()


def test_auto_deferred_candidates_use_plugin_threshold_not_explicit_selectors(tmp_path) -> None:
    cfg = _config()
    cfg.data["tools"]["deferred"] = []
    cfg.data["tools"]["defer_auto"] = True
    cfg.data["tools"]["defer_threshold_ratio"] = 0
    cfg.data["tools"]["defer_min_tokens"] = 1
    reg = _registry(
        SyntheticTool("plugin_lookup", toolset="extra", description="Large plugin schema."),
        SyntheticTool("core_lookup", toolset="core", description="Core schema stays live."),
        SyntheticTool("bash", toolset="core", description="Direct execution stays live."),
    )
    agent = _agent(tmp_path, cfg, reg)

    candidates = agent.deferred_tool_candidate_names()

    assert "plugin_lookup" in candidates
    assert "core_lookup" not in candidates
    assert "bash" not in candidates


def test_auto_deferred_candidates_stay_live_below_threshold(tmp_path) -> None:
    cfg = _config()
    cfg.data["tools"]["deferred"] = []
    cfg.data["tools"]["defer_auto"] = True
    cfg.data["tools"]["defer_threshold_ratio"] = 0
    cfg.data["tools"]["defer_min_tokens"] = 1_000_000
    reg = _registry(SyntheticTool("plugin_lookup", toolset="extra"))
    agent = _agent(tmp_path, cfg, reg)

    assert agent.deferred_tool_candidate_names() == set()


def test_provider_tool_schemas_cache_until_registry_generation_changes(tmp_path) -> None:
    cfg = _config(deferred=[])
    cfg.data["tools"]["deferred"] = []
    cfg.data["tools"]["defer_auto"] = False
    first_tool = CountingSchemaTool("core_first", toolset="core")
    second_tool = CountingSchemaTool("core_second", toolset="core")
    reg = _registry(first_tool)
    agent = _agent(tmp_path, cfg, reg)

    first = agent.provider_tool_schemas()
    second = agent.provider_tool_schemas()

    assert [schema["name"] for schema in first] == [schema["name"] for schema in second]
    assert first_tool.schema_calls == 1

    reg.register(second_tool)
    after_register = agent.provider_tool_schemas()

    assert "core_second" in [schema["name"] for schema in after_register]
    assert second_tool.schema_calls == 1


def test_provider_tool_schemas_cache_invalidates_on_deferred_activation(tmp_path) -> None:
    cfg = _config(deferred=["toolset:extra"])
    cfg.data["tools"]["defer_auto"] = False
    target = CountingSchemaTool("deferred_extra", toolset="extra")
    reg = _registry(target)
    agent = _agent(tmp_path, cfg, reg)

    names_before = [schema["name"] for schema in agent.provider_tool_schemas()]
    assert "deferred_extra" not in names_before
    assert target.schema_calls == 0

    agent.activated_tools.add("deferred_extra")
    names_after = [schema["name"] for schema in agent.provider_tool_schemas()]
    cached_again = [schema["name"] for schema in agent.provider_tool_schemas()]

    assert "deferred_extra" in names_after
    assert cached_again == names_after
    assert target.schema_calls == 1


def test_tool_search_and_describe_are_scoped_to_session(tmp_path) -> None:
    cfg = _config(deferred=["toolset:extra"], disabled=["scoped_disabled"])
    reg = _registry(
        SyntheticTool("scoped_enabled", toolset="extra"),
        SyntheticTool("scoped_disabled", toolset="extra"),
        SyntheticTool("scoped_other", toolset="other"),
    )
    agent = _agent(tmp_path, cfg, reg)
    ctx = ToolContext(cwd=tmp_path, config=cfg, agent=agent)

    search = ToolSearchTool().run({"query": "scoped"}, ctx)

    assert not search.is_error
    search_body = json.loads(search.content)
    assert [match["name"] for match in search_body["matches"]] == ["scoped_enabled"]
    assert "scoped_disabled" not in search.content
    assert "scoped_other" not in search.content
    assert "scoped_enabled" in agent.activated_tools

    described = ToolDescribeTool().run({"name": "scoped_enabled"}, ctx)
    assert not described.is_error
    described_body = json.loads(described.content)
    assert described_body["name"] == "scoped_enabled"
    assert described_body["parameters"] == {"type": "object", "properties": {"value": {"type": "string"}}}

    disabled = ToolDescribeTool().run({"name": "scoped_disabled"}, ctx)
    assert disabled.is_error
    assert "not available in this session" in disabled.content


def test_tool_search_matches_parameter_names_and_honors_limit(tmp_path) -> None:
    cfg = _config(deferred=["toolset:extra"])
    reg = _registry(
        SyntheticTool(
            "issue_lookup",
            toolset="extra",
            description="Lookup records.",
            properties={"ticket_id": {"type": "string"}},
        ),
        SyntheticTool("scoped_alpha", toolset="extra"),
        SyntheticTool("scoped_beta", toolset="extra"),
        SyntheticTool("scoped_gamma", toolset="extra"),
    )
    agent = _agent(tmp_path, cfg, reg)
    ctx = ToolContext(cwd=tmp_path, config=cfg, agent=agent)

    by_param = ToolSearchTool().run({"query": "ticket id"}, ctx)
    limited = ToolSearchTool().run({"query": "scoped", "limit": 2}, ctx)

    assert "issue_lookup" in by_param.content
    assert by_param.display == "1 tool(s), 1 activated"
    assert by_param.data == {
        "query": "ticket id",
        "total_available": 4,
        "matches": [
            {
                "name": "issue_lookup",
                "source": "plugin",
                "source_name": "extra",
                "description": "Lookup records.",
            },
        ],
        "activated": ["issue_lookup"],
        "schemas": [
            {
                "name": "issue_lookup",
                "description": "Lookup records.",
                "parameters": {
                    "type": "object",
                    "properties": {"ticket_id": {"type": "string"}},
                },
            },
        ],
    }
    limited_body = json.loads(limited.content)
    assert [match["name"] for match in limited_body["matches"]] == ["scoped_alpha", "scoped_beta"]
    assert "scoped_gamma" not in limited.content
    assert limited_body["total_available"] == 4
    assert limited_body["activated"] == ["scoped_alpha", "scoped_beta"]
    assert limited.display == "2 tool(s), 2 activated"


def test_tool_call_dispatches_underlying_tool_through_context(tmp_path) -> None:
    target = SyntheticTool("scoped_enabled", toolset="extra")
    cfg = _config(deferred=["toolset:extra"])
    reg = _registry(target)
    agent = _agent(tmp_path, cfg, reg)
    agent.activated_tools.add("scoped_enabled")
    events: list[dict] = []
    ctx = ToolContext(cwd=tmp_path, config=cfg, agent=agent, emit=events.append)

    result = ToolCallTool().run(
        {"name": "scoped_enabled", "arguments": {"value": "42"}},
        ctx,
    )

    assert not result.is_error
    assert json.loads(result.content) == {"ran": "scoped_enabled", "args": {"value": "42"}}
    assert target.calls == [{"value": "42"}]
    assert any(e.get("type") == "tool_start" and e.get("name") == "scoped_enabled" for e in events)


def test_executor_unwraps_deferred_tool_call_for_events_and_result_name(tmp_path) -> None:
    target = SyntheticTool("scoped_enabled", toolset="extra")
    cfg = _config(deferred=["toolset:extra"])
    reg = _registry(target)
    agent = _agent(tmp_path, cfg, reg)
    events: list[dict] = []
    ctx = ToolContext(cwd=tmp_path, config=cfg, agent=agent, emit=events.append)
    executor = ToolExecutor(reg, PermissionEngine(cfg), ctx, events.append)

    messages = executor.execute([
        ToolCall(
            "call_deferred_bridge",
            "tool_call",
            {"name": "scoped_enabled", "arguments": {"value": "via-bridge"}},
        )
    ])

    assert len(messages) == 1
    assert messages[0].tool_call_id == "call_deferred_bridge"
    assert messages[0].name == "scoped_enabled"
    assert json.loads(messages[0].content) == {
        "ran": "scoped_enabled",
        "args": {"value": "via-bridge"},
    }
    assert target.calls == [{"value": "via-bridge"}]
    started = [e.get("name") for e in events if e.get("type") == "tool_start"]
    completed = [e.get("name") for e in events if e.get("type") == "tool_result"]
    assert started == ["scoped_enabled"]
    assert completed == ["scoped_enabled"]


def test_executor_blocks_out_of_scope_deferred_tool_call_before_dispatch(tmp_path) -> None:
    target = SyntheticTool("scoped_other", toolset="other")
    cfg = _config(deferred=["toolset:extra"])
    reg = _registry(target)
    agent = _agent(tmp_path, cfg, reg)
    events: list[dict] = []
    ctx = ToolContext(cwd=tmp_path, config=cfg, agent=agent, emit=events.append)
    executor = ToolExecutor(reg, PermissionEngine(cfg), ctx, events.append)

    messages = executor.execute([
        ToolCall(
            "call_deferred_block",
            "tool_call",
            {"name": "scoped_other", "arguments": {"value": "blocked"}},
        )
    ])

    assert target.calls == []
    assert messages[0].tool_call_id == "call_deferred_block"
    assert messages[0].name == "scoped_other"
    assert "not available in this session" in messages[0].content
    assert any(e.get("type") == "tool_result" and e.get("name") == "scoped_other" for e in events)


def test_tool_call_rejects_recursion_direct_live_and_out_of_scope_tools(tmp_path) -> None:
    cfg = _config(deferred=["toolset:extra"])
    reg = _registry(
        SyntheticTool("scoped_enabled", toolset="extra"),
        SyntheticTool("scoped_other", toolset="other"),
        SyntheticTool("live_core", toolset="core"),
        SyntheticTool("bash", toolset="core"),
    )
    agent = _agent(tmp_path, cfg, reg)
    ctx = ToolContext(cwd=tmp_path, config=cfg, agent=agent)
    bridge = ToolCallTool()

    recursive = bridge.run({"name": "tool_call", "arguments": {}}, ctx)
    assert recursive.is_error
    assert "bridge tool" in recursive.content

    direct = bridge.run({"name": "bash", "arguments": {}}, ctx)
    assert direct.is_error
    assert "not a deferred tool" in direct.content

    live = bridge.run({"name": "live_core", "arguments": {}}, ctx)
    assert live.is_error
    assert "not a deferred tool" in live.content

    out_of_scope = bridge.run({"name": "scoped_other", "arguments": {}}, ctx)
    assert out_of_scope.is_error
    assert "not available in this session" in out_of_scope.content
