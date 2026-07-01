"""Stage R turn-finalization parity tests.

These pin the Hermes-first safety properties around interrupted tool tails and
synthetic final text persistence without reaching into production internals.
"""

from __future__ import annotations

import copy


def _config(*, max_iterations: int = 3):
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data.setdefault("memory", {})["enabled"] = False
    cfg.data.setdefault("skills", {})["auto_load"] = False
    cfg.data.setdefault("agent", {})["max_iterations"] = max_iterations
    cfg.data.setdefault("agent", {})["stream"] = False
    cfg.data.setdefault("agent", {})["self_verify"] = False
    cfg.data.setdefault("tools", {})["toolsets"] = ["core"]
    cfg.data.setdefault("tools", {})["defer_schemas"] = False
    cfg.data.setdefault("tools", {})["exec_mode"] = "full"
    cfg.data["hooks"] = {}
    cfg.data["plugins"] = {"enabled": False}
    cfg.data.setdefault("learn", {})["auto"] = False
    cfg.data.setdefault("learn", {})["auto_apply_skills"] = False
    cfg.data.setdefault("checkpoints", {})["enabled"] = False
    return cfg


class _Provider:
    context_length = 200_000
    name = "stage-r"
    model = "turn-finalization"
    api_mode = None
    auth = None

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def describe(self):
        return f"{self.name}/{self.model}"

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return LLMResponse(text="unexpected extra provider call")


class _RecordingStore:
    def __init__(self, inner):
        self.inner = inner
        self.saved_messages = []

    def save(self, session):
        self.saved_messages.append([m.to_dict() for m in session.messages])
        self.inner.save(session)

    def load(self, session_id):
        return self.inner.load(session_id)


class _CancelAfterTool:
    name = "stage_r_cancel_after_tool"
    description = "Return a tool result and request cancellation before the next model call."
    parameters = {"type": "object", "properties": {}}
    groups = []
    toolset = "core"

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

        ctx.agent.cancel_event.set()
        return ToolResult.ok("tool completed before cancellation")


class _OkTool:
    name = "stage_r_ok"
    description = "Return a deterministic tool result."
    parameters = {"type": "object", "properties": {}}
    groups = []
    toolset = "core"

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

        return ToolResult.ok("ok")


def _registry(*tools):
    from aegis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _agent(provider, tmp_path, *, max_iterations=3, registry=None, session=None, store=None):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    return Agent(
        config=_config(max_iterations=max_iterations),
        provider=provider,
        session=session or Session.create(),
        registry=registry,
        cwd=tmp_path,
        store=store,
    )


def _role(message):
    return message.get("role") if isinstance(message, dict) else message.role


def _content(message):
    return message.get("content", "") if isinstance(message, dict) else message.content


def _meta(message):
    return message.get("meta", {}) if isinstance(message, dict) else message.meta


def _last_assistant(messages):
    for message in reversed(messages):
        if _role(message) == "assistant":
            return message
    raise AssertionError("expected at least one assistant message")


def _assert_no_tool_then_user(messages):
    for idx, (before, after) in enumerate(zip(messages, messages[1:])):
        assert not (_role(before) == "tool" and _role(after) == "user"), (
            f"role alternation violation: tool -> user at index {idx}"
        )


def test_cancel_after_tool_result_closes_transcript_and_marks_cancelled(tmp_path):
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message, ToolCall

    session = Session.create()
    store = _RecordingStore(SessionStore())
    provider = _Provider([
        LLMResponse(
            tool_calls=[
                ToolCall("call_stage_r_cancel", _CancelAfterTool.name, {}),
            ],
        ),
        LLMResponse(text="should not be reached"),
    ])
    agent = _agent(
        provider,
        tmp_path,
        max_iterations=3,
        registry=_registry(_CancelAfterTool()),
        session=session,
        store=store,
    )
    events = []

    result = agent.run("run the cancellable tool", events.append)

    assert provider.calls == 1
    assert any(event["type"] == "cancelled" for event in events)
    assert result.role == "assistant"
    assert agent.session.messages[-2].role == "tool"
    assert agent.session.messages[-1].role == "assistant"
    assert agent.session.messages[-1].content == result.content

    _assert_no_tool_then_user(agent.session.messages + [Message.user("next turn")])
    saved = store.load(session.id)
    assert saved is not None
    assert saved.messages[-1].role == "assistant"
    _assert_no_tool_then_user(saved.messages + [Message.user("next turn")])

    assert result.meta["interrupted"] is True
    assert result.meta["turn_status"] == "cancelled"
    assert agent.session.messages[-1].meta["interrupted"] is True
    assert agent.session.messages[-1].meta["turn_status"] == "cancelled"


def test_budget_grace_empty_reuse_persists_visible_final_text(tmp_path):
    from aegis.session import Session, SessionStore
    from aegis.types import LLMResponse, Message, ToolCall

    expected = "Reusable final answer from the prior turn."
    session = Session.create()
    session.messages.extend([
        Message.user("earlier request"),
        Message.assistant(expected),
    ])
    store = _RecordingStore(SessionStore())
    provider = _Provider([
        LLMResponse(
            tool_calls=[
                ToolCall("call_stage_r_ok", _OkTool.name, {}),
            ],
        ),
        LLMResponse(text=""),
    ])
    agent = _agent(
        provider,
        tmp_path,
        max_iterations=1,
        registry=_registry(_OkTool()),
        session=session,
        store=store,
    )
    events = []

    result = agent.run("use the final budget step", events.append)

    assert provider.calls == 2
    assert result.content == expected
    assert any(event["type"] == "budget_exhausted" for event in events)
    assert any(event["type"] == "empty_reuse" for event in events)
    assert [event for event in events if event["type"] == "final"][-1]["text"] == expected

    assert _content(_last_assistant(agent.session.messages)) == expected

    saved = store.load(session.id)
    assert saved is not None
    assert _content(_last_assistant(saved.messages)) == expected
    assert _content(_last_assistant(store.saved_messages[-1])) == expected


def test_repaired_session_tool_tail_closes_before_next_user_and_keeps_thinking(tmp_path):
    from aegis.session import Session
    from aegis.types import LLMResponse, Message, ToolCall

    class _CapturingProvider(_Provider):
        def __init__(self, script):
            super().__init__(script)
            self.seen_messages = []

        def complete(self, messages, tools=None, **kwargs):
            self.seen_messages = copy.deepcopy(messages)
            return super().complete(messages, tools=tools, **kwargs)

    thinking_blocks = [
        {
            "type": "thinking",
            "thinking": "signed prior tool plan",
            "signature": "sig-stage-r",
        }
    ]
    session = Session.create()
    session.messages.extend([
        Message.user("previous request"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall("call_tail", "stage_r_ok", {})],
            reasoning="structured prior reasoning",
            thinking_blocks=copy.deepcopy(thinking_blocks),
        ),
        Message.tool("call_tail", "stage_r_ok", "tool finished before interruption"),
    ])
    provider = _CapturingProvider([LLMResponse(text="handled the new instruction")])
    agent = _agent(provider, tmp_path, session=session)

    result = agent.run("new instruction")

    assert result.content == "handled the new instruction"
    tool_index = next(
        idx
        for idx, message in enumerate(provider.seen_messages)
        if message.role == "tool" and message.tool_call_id == "call_tail"
    )
    assert provider.seen_messages[tool_index + 1].role == "assistant"
    assert provider.seen_messages[tool_index + 1].content == "Operation interrupted."
    assert provider.seen_messages[tool_index + 2].role == "user"
    prior_assistant = next(
        message
        for message in provider.seen_messages
        if message.role == "assistant" and message.tool_calls
    )
    assert prior_assistant.reasoning == "structured prior reasoning"
    assert prior_assistant.thinking_blocks == thinking_blocks
    assert thinking_blocks == [
        {
            "type": "thinking",
            "thinking": "signed prior tool plan",
            "signature": "sig-stage-r",
        }
    ]
    _assert_no_tool_then_user(provider.seen_messages)
    _assert_no_tool_then_user(agent.session.messages)
