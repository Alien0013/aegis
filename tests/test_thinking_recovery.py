"""Thinking-signature 400 recovery: resend without thinking blocks, never corrupt state."""

from __future__ import annotations

from aegis.providers.chat_completions import ProviderHTTPError
from aegis.providers.fallback import classify_provider_error, recovery_action
from aegis.types import Message


def test_classifier_routes_thinking_signature_400():
    for body in ("thinking or redacted_thinking blocks ... cannot be modified",
                 "Invalid signature for thinking block",
                 "thinking blocks in the latest assistant message"):
        exc = ProviderHTTPError(400, body)
        assert classify_provider_error(exc) == "thinking_signature"
        assert recovery_action("thinking_signature") == "strip_thinking"
    # a plain bad-request 400 is NOT misclassified
    assert classify_provider_error(ProviderHTTPError(400, "missing field x")) == "client"


def test_without_thinking_copies_not_mutates():
    from aegis.agent.loop import _without_thinking
    blk = [{"type": "thinking", "thinking": "t", "signature": "s"}]
    m = Message(role="assistant", content="hi", reasoning="t", thinking_blocks=list(blk))
    stripped = _without_thinking(m)
    assert stripped is not m                       # a copy
    assert stripped.thinking_blocks == [] and stripped.reasoning == ""
    assert m.thinking_blocks == blk and m.reasoning == "t"   # canonical untouched
    # messages without thinking pass through by identity (no needless copy)
    plain = Message.user("q")
    assert _without_thinking(plain) is plain


def test_loop_strips_thinking_on_signature_400_then_succeeds(monkeypatch):
    """End to end: first call 400s on signature, retry sends thinking-free wire copy and
    succeeds. The stored session keeps its signed blocks (no permanent corruption)."""
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session

    agent = Agent.create(Config.load(), session=Session.create())
    agent.session.messages = [
        Message.system("s"),
        Message.user("q1"),
        Message(role="assistant", content="a1", reasoning="why",
                thinking_blocks=[{"type": "thinking", "thinking": "why", "signature": "sig"}]),
        Message.user("q2"),
    ]
    seen = []
    calls = {"n": 0}

    def fake_complete(provider, messages, *, tools=None, **kw):
        calls["n"] += 1
        # capture whether the assistant turn still carried thinking blocks on this call
        asst = next(m for m in messages if m.role == "assistant")
        seen.append(bool(asst.thinking_blocks))
        if calls["n"] == 1:
            raise ProviderHTTPError(400, "thinking blocks cannot be modified")
        return Message.assistant("ok").__class__ and _resp("ok")

    from aegis.types import LLMResponse

    def _resp(t):
        return LLMResponse(text=t)

    monkeypatch.setattr("aegis.agent.loop._provider_complete", fake_complete)
    from aegis.agent import loop
    result = loop.run_conversation(agent)
    assert result.content == "ok"
    assert seen == [True, False]                   # first sent blocks, retry stripped them
    # canonical session still has the signed block for future turns
    asst = next(m for m in agent.session.messages if m.role == "assistant" and m.content == "a1")
    assert asst.thinking_blocks and asst.reasoning == "why"
    from aegis.tracing import TraceStore

    trace = TraceStore.from_config(agent.config).get_trace(agent._trace_context["trace_id"])
    assert trace["status"] == "ok"
    assert [s["status"] for s in trace["spans"] if s["kind"] == "provider_call"] == ["retrying", "ok"]
