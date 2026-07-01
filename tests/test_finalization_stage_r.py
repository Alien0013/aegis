"""Stage R finalization parity regressions.

Hermes finalization treats the returned final text as the canonical assistant
turn and skips external memory sync for interrupted turns. These tests pin that
contract at AEGIS' Agent.run boundary.
"""

from __future__ import annotations

import copy


def _config(max_iterations: int = 8):
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["agent"]["max_iterations"] = max_iterations
    cfg.data["agent"]["stream"] = False
    cfg.data["memory"]["enabled"] = True
    cfg.data["memory"]["provider"] = ""
    cfg.data["skills"]["auto_load"] = False
    cfg.data["tools"]["defer_schemas"] = False
    cfg.data["tools"]["exec_mode"] = "full"
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}
    cfg.data["learn"]["auto"] = False
    cfg.data["learn"]["auto_apply_skills"] = False
    return cfg


class ScriptedProvider:
    name = "stage-r"
    model = "finalization"
    context_length = 200_000
    api_mode = None
    auth = None

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, **kwargs):
        self.calls += 1
        if not self.responses:
            raise AssertionError("unexpected extra provider call")
        return self.responses.pop(0)


class RecordingExternalMemory:
    def __init__(self):
        self.synced = []

    def sync_turn(self, user_content, assistant_content, *, session_id="", messages=None):
        self.synced.append(
            {
                "user_content": user_content,
                "assistant_content": assistant_content,
                "session_id": session_id,
                "messages": list(messages or []),
            }
        )


def _agent(provider, tmp_path, *, registry=None, external_memory=None, max_iterations: int = 8):
    from aegis.agent.agent import Agent
    from aegis.memory import MemoryManager
    from aegis.session import Session

    cfg = _config(max_iterations=max_iterations)
    memory = MemoryManager(cfg, external=external_memory or RecordingExternalMemory())
    return Agent(
        config=cfg,
        provider=provider,
        session=Session.create(),
        registry=registry,
        memory=memory,
        cwd=tmp_path,
    )


def _last_assistant_content(messages) -> str:
    return next(
        message.content
        for message in reversed(messages)
        if message.role == "assistant"
    )


def test_reused_post_tool_text_is_canonical_and_synced_to_external_memory(tmp_path):
    from aegis.tools.base import Tool, ToolResult
    from aegis.tools.registry import ToolRegistry
    from aegis.types import LLMResponse, ToolCall

    class ProbeTool(Tool):
        name = "stage_r_probe"
        description = "Local Stage R probe."
        parameters = {"type": "object", "properties": {}}

        def run(self, args, ctx):
            return ToolResult.ok("probe result")

    registry = ToolRegistry()
    registry.register(ProbeTool())
    external = RecordingExternalMemory()
    final_text = "Processed the probe result before the empty tail."
    provider = ScriptedProvider(
        [
            LLMResponse(
                text=final_text,
                tool_calls=[ToolCall("call_1", "stage_r_probe", {})],
            ),
            LLMResponse(text=""),
            LLMResponse(text=""),
            LLMResponse(text=""),
            LLMResponse(text=""),
            LLMResponse(text=""),
        ]
    )
    agent = _agent(provider, tmp_path, registry=registry, external_memory=external)
    events = []

    result = agent.run("use the probe", events.append)
    assert agent.memory.flush_pending(timeout=1)

    assert [event["type"] for event in events if event["type"] == "empty_reuse"] == [
        "empty_reuse"
    ]
    assert external.synced
    assert (
        result.content,
        _last_assistant_content(agent.session.messages),
        external.synced[-1]["assistant_content"],
        external.synced[-1]["messages"][-1]["content"],
    ) == (final_text, final_text, final_text, final_text)


def test_cancelled_turn_after_tool_call_does_not_sync_external_memory(tmp_path):
    from aegis.tools.base import Tool, ToolResult
    from aegis.tools.registry import ToolRegistry
    from aegis.types import LLMResponse, ToolCall

    class CancellingTool(Tool):
        name = "stage_r_cancel"
        description = "Request cancellation from inside a tool."
        parameters = {"type": "object", "properties": {}}

        def run(self, args, ctx):
            ctx.agent.cancel()
            return ToolResult.ok("cancel requested")

    registry = ToolRegistry()
    registry.register(CancellingTool())
    external = RecordingExternalMemory()
    provider = ScriptedProvider(
        [
            LLMResponse(
                text="",
                tool_calls=[ToolCall("call_cancel", "stage_r_cancel", {})],
            ),
        ]
    )
    agent = _agent(provider, tmp_path, registry=registry, external_memory=external)

    result = agent.run("start the tool and then stop")
    assert agent.memory.flush_pending(timeout=1)

    assert result.content == "[interrupted by user]"
    assert agent.tools_used == 1
    assert external.synced == []


def test_empty_terminal_final_does_not_sync_external_memory(tmp_path):
    from aegis.types import LLMResponse

    external = RecordingExternalMemory()
    provider = ScriptedProvider(
        [
            LLMResponse(text=""),
            LLMResponse(text=""),
            LLMResponse(text=""),
            LLMResponse(text=""),
        ]
    )
    agent = _agent(provider, tmp_path, external_memory=external)

    result = agent.run("return nothing after retries")
    assert agent.memory.flush_pending(timeout=1)

    assert result.content == ""
    assert result.meta["turn_exit_reason"] == "empty_response_exhausted"
    assert external.synced == []


def test_review_failure_does_not_skip_trajectory_capture(tmp_path, monkeypatch):
    from aegis.agent import review
    from aegis import trajectory
    from aegis.types import LLMResponse

    calls = []

    def boom_review(agent, tools_this_turn):
        calls.append(("review", tools_this_turn))
        raise RuntimeError("review down")

    def capture(config, session):
        calls.append(("trajectory", session.messages[-1].content))

    monkeypatch.setattr(review, "maybe_review", boom_review)
    monkeypatch.setattr(trajectory, "capture_turn", capture)
    agent = _agent(ScriptedProvider([LLMResponse(text="visible final")]), tmp_path)

    result = agent.run("finish visibly")

    assert result.content == "visible final"
    assert any(error.startswith("background_review: review down")
               for error in result.meta["cleanup_errors"])
    assert calls == [("review", 0), ("trajectory", "visible final")]


def test_cancelled_turn_skips_review_but_still_captures_trajectory(tmp_path, monkeypatch):
    from aegis.agent import review
    from aegis import trajectory
    from aegis.tools.base import Tool, ToolResult
    from aegis.tools.registry import ToolRegistry
    from aegis.types import LLMResponse, ToolCall

    class CancellingTool(Tool):
        name = "stage_r_review_cancel"
        description = "Request cancellation from inside a tool."
        parameters = {"type": "object", "properties": {}}

        def run(self, args, ctx):
            ctx.agent.cancel()
            return ToolResult.ok("cancel requested")

    calls = []
    monkeypatch.setattr(review, "maybe_review",
                        lambda agent, tools_this_turn: calls.append(("review", tools_this_turn)))
    monkeypatch.setattr(trajectory, "capture_turn",
                        lambda config, session: calls.append(("trajectory", session.messages[-1].content)))
    registry = ToolRegistry()
    registry.register(CancellingTool())
    provider = ScriptedProvider([
        LLMResponse(tool_calls=[ToolCall("call_cancel_review", CancellingTool.name, {})]),
    ])
    agent = _agent(provider, tmp_path, registry=registry)

    result = agent.run("cancel after tool")

    assert result.meta["interrupted"] is True
    assert result.meta["turn_status"] == "cancelled"
    assert calls == [("trajectory", "[interrupted by user]")]
