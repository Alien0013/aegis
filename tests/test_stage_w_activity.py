from __future__ import annotations

import copy


def _config(max_iterations: int = 3):
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["agent"]["max_iterations"] = max_iterations
    cfg.data["agent"]["stream"] = True
    cfg.data["tools"]["toolsets"] = ["core"]
    cfg.data["tools"]["defer_schemas"] = False
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}
    cfg.data.setdefault("learn", {})["auto"] = False
    cfg.data.setdefault("learn", {})["auto_apply_skills"] = False
    return cfg


class ProbeTool:
    name = "stage_w_probe"
    description = "Stage W activity probe."
    parameters = {"type": "object", "properties": {}}
    groups = []
    toolset = "core"
    source = "test"

    def available(self):
        return True, ""

    def schema(self):
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def run(self, args, ctx):
        from aegis.tools.base import ToolResult

        return ToolResult.ok("probe evidence")


class ActivityProvider:
    name = "stage-w"
    model = "activity"
    context_length = 200_000
    api_mode = None
    auth = None

    def __init__(self):
        self.calls = 0

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse, ToolCall

        self.calls += 1
        if self.calls == 1:
            kwargs["on_delta"]("working")
            kwargs["on_reasoning"]("private plan")
            return LLMResponse(
                text="working",
                reasoning="private plan",
                tool_calls=[ToolCall("call_stage_w", "stage_w_probe", {})],
            )
        kwargs["on_delta"]("done")
        return LLMResponse(text="done")


def _agent(tmp_path, *, event_callback=None):
    from aegis.agent.agent import Agent
    from aegis.session import Session
    from aegis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(ProbeTool())
    agent = Agent(
        config=_config(),
        provider=ActivityProvider(),
        session=Session.create("stage w"),
        registry=registry,
        cwd=tmp_path,
        event_callback=event_callback,
    )
    agent._surface_run_id = "run_stage_w"
    return agent


def test_activity_summary_and_loop_events_are_stamped_and_callbacked(tmp_path):
    callback_events = []
    agent = _agent(
        tmp_path,
        event_callback=lambda event_type, payload: callback_events.append((event_type, payload)),
    )
    events = []

    result = agent.run("collect activity evidence", on_event=events.append)

    assert result.content == "done"
    assert agent.provider.calls == 2
    event_types = [event["type"] for event in events]
    assert "assistant_delta" in event_types
    assert "reasoning_delta" in event_types
    assert "tool_start" in event_types
    assert "tool_result" in event_types
    assert event_types[-1] == "final"

    final_event = events[-1]
    assert final_event["session_id"] == agent.session.id
    assert final_event["trace_id"].startswith("trace_")
    assert final_event["turn_id"].startswith("turn_")
    assert final_event["run_id"] == "run_stage_w"
    assert all(event.get("session_id") == agent.session.id for event in events)
    assert all(event.get("turn_id") == final_event["turn_id"] for event in events)
    assert all(event.get("run_id") == "run_stage_w" for event in events)

    callback_types = [event_type for event_type, _payload in callback_events]
    assert "assistant_delta" in callback_types
    assert "reasoning_delta" in callback_types
    assert "tool_start" in callback_types
    assert "final" in callback_types
    callback_final = callback_events[-1][1]
    assert callback_final["session_id"] == agent.session.id
    assert callback_final["trace_id"] == final_event["trace_id"]
    assert callback_final["turn_id"] == final_event["turn_id"]
    assert callback_final["run_id"] == "run_stage_w"

    summary = agent.get_activity_summary()
    assert summary["last_activity_desc"] == "final response emitted"
    assert summary["seconds_since_activity"] >= 0
    assert summary["current_tool"] == ""
    assert summary["current_api_request_id"] == ""
    assert summary["last_api_request_id"].startswith("api_")
    assert summary["api_call_count"] == 2
    assert summary["completed_api_call_count"] == 2
    assert summary["turn_api_request_count"] == 2
    assert summary["budget_used"] == 2
    assert summary["budget_max"] == 3
    assert summary["budget_remaining"] == 1


def test_event_callback_failure_is_fail_soft(tmp_path):
    def boom(_event_type, _payload):
        raise RuntimeError("callback failed")

    agent = _agent(tmp_path, event_callback=boom)
    events = []

    result = agent.run("callback should not break the turn", on_event=events.append)

    assert result.content == "done"
    assert events[-1]["type"] == "final"
    assert agent.get_activity_summary()["last_activity_desc"] == "final response emitted"
