"""Stage Z tool-result persistence and interrupt parity contracts.

Hermes flushes each tool result as soon as it is appended, records a terminal
tool result for cancelled/skipped calls, and propagates turn context into
parallel tool workers. These tests pin those contracts without live providers.
"""

from __future__ import annotations

import contextvars
import copy


_STAGE_Z_CONTEXT = contextvars.ContextVar("stage_z_context", default="missing")


def _config(*, max_iterations: int = 4):
    from aegis.config import Config, DEFAULT_CONFIG

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data.setdefault("agent", {})["max_iterations"] = max_iterations
    cfg.data.setdefault("agent", {})["stream"] = False
    cfg.data.setdefault("agent", {})["self_verify"] = False
    cfg.data.setdefault("memory", {})["enabled"] = False
    cfg.data.setdefault("skills", {})["auto_load"] = False
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
    name = "stage-z"
    model = "tool-persistence"
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
        return LLMResponse(text="done")


class _RecordingStore:
    def __init__(self, inner):
        self.inner = inner
        self.saved_messages = []

    def save(self, session):
        self.saved_messages.append([m.to_dict() for m in session.messages])
        self.inner.save(session)

    def load(self, session_id):
        return self.inner.load(session_id)


class _OkTool:
    description = "Return a deterministic Stage Z result."
    parameters = {"type": "object", "properties": {}}
    groups = []
    toolset = "core"

    def __init__(self, name: str, content: str):
        self.name = name
        self.content = content
        self.runs = 0

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

        self.runs += 1
        return ToolResult.ok(self.content)


class _CancelTool(_OkTool):
    def run(self, args, ctx):
        from aegis.tools.base import ToolResult

        self.runs += 1
        ctx.agent.cancel_event.set()
        return ToolResult.ok(self.content)


class _ContextReadTool:
    name = "read_file"
    description = "Read the Stage Z context variable instead of a file."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
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

        return ToolResult.ok(_STAGE_Z_CONTEXT.get())


def _registry(*tools):
    from aegis.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _agent(provider, tmp_path, *, registry, store=None, max_iterations: int = 4):
    from aegis.agent.agent import Agent
    from aegis.session import Session

    return Agent(
        config=_config(max_iterations=max_iterations),
        provider=provider,
        session=Session.create(),
        registry=registry,
        cwd=tmp_path,
        store=store,
    )


def _tool_ids(messages: list[dict]) -> tuple[str, ...]:
    return tuple(
        str(message.get("tool_call_id") or "")
        for message in messages
        if message.get("role") == "tool"
    )


def test_session_is_saved_after_each_tool_result(tmp_path):
    from aegis.session import SessionStore
    from aegis.types import LLMResponse, ToolCall

    first = _OkTool("stage_z_first", "first result")
    second = _OkTool("stage_z_second", "second result")
    store = _RecordingStore(SessionStore())
    provider = _Provider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall("call_stage_z_first", first.name, {}),
                    ToolCall("call_stage_z_second", second.name, {}),
                ],
            ),
            LLMResponse(text="final after both tools"),
        ]
    )
    agent = _agent(
        provider,
        tmp_path,
        registry=_registry(first, second),
        store=store,
    )

    result = agent.run("run both tools")

    assert result.content == "final after both tools"
    assert first.runs == 1
    assert second.runs == 1
    saved_tool_sequences = [_tool_ids(snapshot) for snapshot in store.saved_messages]
    assert ("call_stage_z_first",) in saved_tool_sequences
    assert ("call_stage_z_first", "call_stage_z_second") in saved_tool_sequences


def test_cancelled_remaining_tool_call_records_skipped_result(tmp_path):
    from aegis.types import LLMResponse, ToolCall

    first = _CancelTool("stage_z_cancel", "cancel requested")
    skipped = _OkTool("stage_z_should_skip", "this tool should not run")
    provider = _Provider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall("call_stage_z_cancel", first.name, {}),
                    ToolCall("call_stage_z_skipped", skipped.name, {}),
                ],
            ),
            LLMResponse(text="should not be reached"),
        ]
    )
    agent = _agent(
        provider,
        tmp_path,
        registry=_registry(first, skipped),
        max_iterations=3,
    )

    result = agent.run("cancel after the first tool")

    assert result.meta["turn_status"] == "cancelled"
    assert first.runs == 1
    assert skipped.runs == 0
    tool_messages = [m for m in agent.session.messages if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == [
        "call_stage_z_cancel",
        "call_stage_z_skipped",
    ]
    assert "cancel" in tool_messages[1].content.lower() or "skipped" in tool_messages[1].content.lower()


def test_parallel_tool_execution_propagates_turn_contextvars(tmp_path):
    from aegis.agent.loop import ToolExecutor
    from aegis.tools.base import ToolContext
    from aegis.tools.permissions import PermissionEngine
    from aegis.types import ToolCall

    cfg = _config()
    ctx = ToolContext(cwd=tmp_path, config=cfg)
    executor = ToolExecutor(
        _registry(_ContextReadTool()),
        PermissionEngine(cfg),
        ctx,
        lambda _event: None,
    )
    token = _STAGE_Z_CONTEXT.set("turn-context")
    try:
        results = executor.execute(
            [
                ToolCall("call_stage_z_ctx_a", "read_file", {"path": "a.txt"}),
                ToolCall("call_stage_z_ctx_b", "read_file", {"path": "b.txt"}),
            ]
        )
    finally:
        _STAGE_Z_CONTEXT.reset(token)

    assert [message.content for message in results] == ["turn-context", "turn-context"]
