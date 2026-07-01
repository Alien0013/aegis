"""Stage P continuation regressions: empty, truncated, and thinking-only replies."""

from __future__ import annotations

import copy


def _config(max_iterations: int = 8):
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["agent"]["max_iterations"] = max_iterations
    cfg.data["agent"]["stream"] = False
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["tools"]["defer_schemas"] = False
    cfg.data["tools"]["exec_mode"] = "full"
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}
    cfg.data["learn"]["auto"] = False
    cfg.data["learn"]["auto_apply_skills"] = False
    return cfg


class ScriptedProvider:
    name = "stage-p"
    model = "continuation"
    context_length = 200_000
    api_mode = None
    auth = None

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.seen_roles: list[list[str]] = []
        self.max_tokens_seen: list[int | None] = []

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, **kwargs):
        self.calls += 1
        self.max_tokens_seen.append(kwargs.get("max_tokens"))
        self.seen_roles.append([getattr(message, "role", "") for message in messages])
        if not self.responses:
            raise AssertionError("unexpected extra provider call")
        return self.responses.pop(0)


def _agent(provider, tmp_path, *, registry=None, max_iterations: int = 8):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    return Agent(
        config=_config(max_iterations=max_iterations),
        provider=provider,
        session=Session.create(),
        registry=registry,
        cwd=tmp_path,
    )


def test_truncated_text_continuation_is_bounded_to_three_retries(tmp_path):
    from aegis.types import LLMResponse

    provider = ScriptedProvider(
        [
            LLMResponse(text="chunk 1", finish_reason="length"),
            LLMResponse(text="chunk 2", finish_reason="length"),
            LLMResponse(text="chunk 3", finish_reason="length"),
            LLMResponse(text="chunk 4", finish_reason="length"),
        ]
    )
    agent = _agent(provider, tmp_path)
    events = []

    result = agent.run("write a long answer", events.append)

    assert provider.calls == 4
    assert [event["n"] for event in events if event["type"] == "continuation"] == [1, 2, 3]
    assert result.content == "chunk 1chunk 2chunk 3chunk 4"


def test_length_continuation_boosts_ephemeral_max_tokens(tmp_path):
    from aegis.types import LLMResponse

    provider = ScriptedProvider(
        [
            LLMResponse(text="part 1", finish_reason="length"),
            LLMResponse(text="part 2"),
        ]
    )
    agent = _agent(provider, tmp_path)
    agent._request_max_tokens = 100

    result = agent.run("write a long answer")

    assert result.content == "part 1part 2"
    assert provider.max_tokens_seen == [100, 200]


def test_post_tool_empty_response_gets_single_local_nudge_and_recovers(tmp_path):
    from aegis.tools.base import Tool, ToolResult
    from aegis.tools.registry import ToolRegistry
    from aegis.types import LLMResponse, ToolCall

    class ProbeTool(Tool):
        name = "stage_p_probe"
        description = "Local test probe."
        parameters = {"type": "object", "properties": {}}

        def run(self, args, ctx):
            return ToolResult.ok("probe result")

    registry = ToolRegistry()
    registry.register(ProbeTool())
    provider = ScriptedProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall("call_1", "stage_p_probe", {})]),
            LLMResponse(text=""),
            LLMResponse(text="processed the probe result"),
        ]
    )
    agent = _agent(provider, tmp_path, registry=registry)
    events = []

    result = agent.run("use the probe", events.append)

    assert result.content == "processed the probe result"
    assert provider.calls == 3
    assert [event["n"] for event in events if event["type"] == "empty_nudge"] == [1]


def test_truly_empty_response_retries_before_finalizing(tmp_path):
    from aegis.types import LLMResponse

    provider = ScriptedProvider(
        [
            LLMResponse(text=""),
            LLMResponse(text=""),
            LLMResponse(text=""),
            LLMResponse(text="recovered after empty retries"),
        ]
    )
    agent = _agent(provider, tmp_path)
    events = []

    result = agent.run("answer after a transient empty response", events.append)

    assert result.content == "recovered after empty retries"
    assert provider.calls == 4


def test_thinking_only_response_prefills_or_retries_until_visible_text(tmp_path):
    from aegis.types import LLMResponse

    provider = ScriptedProvider(
        [
            LLMResponse(
                text="",
                reasoning="private plan",
                thinking_blocks=[
                    {"type": "thinking", "thinking": "private plan", "signature": "sig-1"},
                ],
            ),
            LLMResponse(text="", reasoning="still private"),
            LLMResponse(text="visible answer"),
        ]
    )
    agent = _agent(provider, tmp_path)
    events = []

    result = agent.run("produce visible text after thinking", events.append)

    assert result.content == "visible answer"
    assert provider.calls == 3
    surfaced = "\n".join(
        [result.content]
        + [event.get("text", "") for event in events if event["type"] in {"assistant_message", "final"}]
    )
    assert "private plan" not in surfaced
