"""Stage T subagent parity with Hermes delegation constraints."""

from __future__ import annotations

import pytest

from aegis.agent.agent import Agent
from aegis.config import Config
from aegis.session import Session
from aegis.tools.agentic import SubagentTool, list_subagents
from aegis.tools.base import ToolContext
from aegis.types import Message, Usage


@pytest.fixture
def child_capture(monkeypatch):
    seen: dict[str, object] = {"runs": []}

    def fake_run(self, task, on_event=None):
        toolsets = list(self.config.get("tools.toolsets", []) or [])
        available = self.registry.available(toolsets, disabled=self.config.get("tools.disabled", []))
        run = {
            "task": task if isinstance(task, str) else task.content,
            "toolsets": toolsets,
            "available_tools": {tool.name for tool in available},
            "all_tools": {tool.name for tool in self.registry.all()},
            "session_id": self.session.id,
            "session_meta": dict(self.session.meta),
            "provider": str(getattr(self.provider, "name", "") or ""),
            "model": str(getattr(self.provider, "model", "") or ""),
            "depth": getattr(self, "_depth", None),
        }
        seen["runs"].append(run)
        seen["last"] = run
        return Message.assistant("child ok")

    monkeypatch.setattr(Agent, "run", fake_run)
    return seen


def _config(toolsets: list[str]) -> Config:
    cfg = Config.load()
    cfg.data.setdefault("memory", {})["enabled"] = False
    cfg.data.setdefault("tools", {})["toolsets"] = list(toolsets)
    cfg.data.setdefault("model", {})["provider"] = "anthropic"
    cfg.data.setdefault("model", {})["default"] = "claude-test"
    return cfg


def _ctx(tmp_path, cfg: Config, *, parent=None, events=None) -> ToolContext:
    return ToolContext(cwd=tmp_path, config=cfg, agent=parent, emit=events.append if events is not None else None)


def _last(child_capture) -> dict:
    return child_capture["last"]


def _event_session_id(event: dict) -> str:
    return str(event.get("child_session_id") or event.get("session_id") or "")


@pytest.mark.parametrize("parent_toolsets", [["default"], ["guided/default"]])
def test_requested_child_toolsets_are_scoped_through_default_parent_aliases(
    tmp_path,
    child_capture,
    parent_toolsets,
):
    cfg = _config(parent_toolsets)

    result = SubagentTool().run(
        {"task": "inspect using default scope", "toolsets": ["core", "browser", "lsp"]},
        _ctx(tmp_path, cfg),
    )

    assert not result.is_error
    child = _last(child_capture)
    assert child["toolsets"] == ["core"]
    assert "read_file" in child["available_tools"]
    assert "browser" not in child["available_tools"]
    assert "lsp" not in child["available_tools"]
    assert cfg.get("tools.toolsets") == parent_toolsets


def test_requested_child_toolsets_cannot_widen_all_parent_with_unknown_composite(
    tmp_path,
    child_capture,
):
    cfg = _config(["all"])

    result = SubagentTool().run(
        {"task": "try an unknown composite", "toolsets": ["core", "browser", "made_up_composite"]},
        _ctx(tmp_path, cfg),
    )

    assert not result.is_error
    child = _last(child_capture)
    assert child["toolsets"] == ["core", "browser"]
    assert "read_file" in child["available_tools"]
    assert "browser" in child["available_tools"]
    assert "made_up_composite" not in child["toolsets"]
    assert cfg.get("tools.toolsets") == ["all"]


def test_leaf_subagents_block_cronjob_and_delegate_alias(tmp_path, child_capture):
    cfg = _config(["all"])

    result = SubagentTool().run({"task": "leaf work"}, _ctx(tmp_path, cfg))

    assert not result.is_error
    child = _last(child_capture)
    blocked = {
        "clarify",
        "memory",
        "send_message",
        "execute_code",
        "cronjob",
        "spawn_subagent",
        "delegate_task",
    }
    assert child["available_tools"].isdisjoint(blocked)
    assert child["all_tools"].isdisjoint(blocked)


def test_orchestrator_metadata_is_stored_and_emitted_when_depth_allows(
    tmp_path,
    child_capture,
):
    cfg = _config(["core", "browser"])
    cfg.data.setdefault("agent", {})["max_spawn_depth"] = 2
    parent = type("Parent", (), {"session": Session.create(), "_depth": 0})()
    events: list[dict] = []

    result = SubagentTool().run(
        {"task": "coordinate safely", "role": "orchestrator", "toolsets": ["browser"]},
        _ctx(tmp_path, cfg, parent=parent, events=events),
    )

    assert not result.is_error
    child = _last(child_capture)
    meta = child["session_meta"]
    start = next(event for event in events if event["type"] == "subagent_start")
    done = next(event for event in events if event["type"] == "subagent_done")

    assert meta["parent_session_id"] == parent.session.id
    assert meta["depth"] == 1
    assert meta["role"] == "orchestrator"
    assert meta["agent_type"] == "general"
    assert meta["toolsets"] == ["browser"]
    assert meta["provider"] == child["provider"] == "anthropic"
    assert meta["model"] == child["model"] == "claude-test"

    for event in (start, done):
        assert event["parent_session_id"] == parent.session.id
        assert _event_session_id(event) == child["session_id"]
        assert event["depth"] == 1
        assert event["role"] == "orchestrator"
        assert event["agent_type"] == "general"
        assert event["toolsets"] == ["browser"]
        assert event["provider"] == "anthropic"
        assert event["model"] == "claude-test"


def test_orchestrator_role_degrades_to_leaf_without_depth_budget(
    tmp_path,
    child_capture,
):
    cfg = _config(["all"])
    cfg.data.setdefault("agent", {})["max_spawn_depth"] = 1
    events: list[dict] = []

    result = SubagentTool().run(
        {"task": "asked to coordinate", "role": "orchestrator"},
        _ctx(tmp_path, cfg, events=events),
    )

    assert not result.is_error
    child = _last(child_capture)
    meta = child["session_meta"]
    start = next(event for event in events if event["type"] == "subagent_start")
    done = next(event for event in events if event["type"] == "subagent_done")

    assert meta["depth"] == 1
    assert meta["role"] == "leaf"
    assert "spawn_subagent" not in child["available_tools"]
    assert "delegate_task" not in child["available_tools"]
    assert start["role"] == "leaf"
    assert done["role"] == "leaf"


def test_subagent_done_event_carries_child_usage_and_cost(tmp_path, monkeypatch):
    cfg = _config(["core"])
    events: list[dict] = []

    def fake_run(self, task, on_event=None):
        self._last_turn_usage = Usage(123, 45, cache_read=6, cache_write=7)
        self._last_turn_cost = {
            "amount_usd": 0.012345,
            "cost_status": "estimated",
            "cost_source": "official_docs_snapshot",
            "pricing_source": "official_docs_snapshot",
            "cost_label": "~$0.0123",
        }
        return Message.assistant("child ok")

    monkeypatch.setattr(Agent, "run", fake_run)

    result = SubagentTool().run(
        {"task": "measure child usage"},
        _ctx(tmp_path, cfg, events=events),
    )

    assert not result.is_error
    done = next(event for event in events if event["type"] == "subagent_done")
    assert done["input_tokens"] == 123
    assert done["output_tokens"] == 45
    assert done["cache_read_tokens"] == 6
    assert done["cache_write_tokens"] == 7
    assert done["prompt_tokens"] == 136
    assert done["total_tokens"] == 181
    assert done["usage"]["total_tokens"] == 181
    assert done["cost_usd"] == 0.012345
    assert done["cost_status"] == "estimated"
    assert done["cost_source"] == "official_docs_snapshot"

    row = next(item for item in list_subagents() if item["id"] == done["id"])
    assert row["usage"]["total_tokens"] == 181
    assert row["cost_usd"] == 0.012345
