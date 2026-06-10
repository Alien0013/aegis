"""Structured error classifier + recovery actions, and mid-run steering."""

from __future__ import annotations


def _err(status, body=""):
    from aegis.providers.chat_completions import ProviderHTTPError
    return ProviderHTTPError(status, body)


def test_error_classifier_taxonomy_and_actions():
    from aegis.providers.fallback import classify_provider_error as c, recovery_action as a

    # context overflow -> compress (don't failover)
    assert c(_err(400, "This model's maximum context length is 8192 tokens")) == "context_overflow"
    assert a("context_overflow") == "compress"
    # content policy -> abort (retrying unchanged won't help)
    assert c(_err(400, "Your request was rejected by our content policy")) == "content_policy"
    assert a("content_policy") == "abort"
    # quota/billing -> rotate immediately (even on a 429)
    assert c(_err(429, "You exceeded your current quota (insufficient_quota)")) == "billing"
    assert c(_err(402, "payment required")) == "billing"
    assert a("billing") == "rotate"
    # plain rate limit -> retry; auth -> rotate; 5xx -> retry; other 4xx -> abort
    assert c(_err(429, "rate limit reached")) == "rate_limit" and a("rate_limit") == "retry"
    assert c(_err(401, "bad key")) == "auth" and a("auth") == "rotate"
    assert c(_err(503, "overloaded")) == "server" and a("server") == "retry"
    assert c(_err(404, "no such route")) == "client" and a("client") == "abort"
    # network timeouts -> transient -> retry
    assert c(TimeoutError("slow")) == "transient" and a("transient") == "retry"


def test_steer_folds_into_last_tool_message():
    from aegis.agent.loop import _drain_steering
    from aegis.types import Message
    import queue

    class FakeAgent:
        def __init__(self): self.steer_queue = queue.Queue()
    agent = FakeAgent()

    class Sess:
        messages = [Message.user("go"),
                    Message.assistant("running"),
                    Message.tool("c1", "bash", "ls output")]
    s = Sess()
    # no steering queued -> no change
    _drain_steering(agent, s)
    assert s.messages[-1].content == "ls output"
    # queued guidance is appended to the last tool message (preserves role alternation)
    agent.steer_queue.put("focus on the tests dir")
    _drain_steering(agent, s)
    assert "[user steering]: focus on the tests dir" in s.messages[-1].content
    assert s.messages[-1].role == "tool"           # no new message inserted


def test_steer_appends_user_msg_when_no_tool_message():
    from aegis.agent.loop import _drain_steering
    from aegis.types import Message
    import queue

    class FakeAgent:
        def __init__(self): self.steer_queue = queue.Queue()
    agent = FakeAgent()

    class Sess:
        messages = [Message.user("hi")]
    s = Sess()
    agent.steer_queue.put("actually use python")
    _drain_steering(agent, s)
    assert s.messages[-1].role == "user" and "actually use python" in s.messages[-1].content


def test_fallback_short_circuits_unfixable_errors():
    from aegis.providers.fallback import FallbackProvider
    from aegis.providers.chat_completions import ProviderHTTPError

    class P:
        def __init__(self, name, exc=None, resp=None):
            self.name = name; self.exc = exc; self.resp = resp; self.calls = 0
        def complete(self, messages, tools=None, **kw):
            self.calls += 1
            if self.exc:
                raise self.exc
            return self.resp

    # content_policy on primary -> raise immediately, DON'T try the fallback
    prim = P("a", exc=ProviderHTTPError(400, "rejected by our content policy"))
    fb = P("b", resp="ok")
    try:
        FallbackProvider(prim, [fb]).complete([])
        raise AssertionError("should have raised")
    except ProviderHTTPError:
        pass
    assert fb.calls == 0                         # never reached the fallback

    # server error on primary -> DOES fail over
    prim2 = P("a", exc=ProviderHTTPError(503, "overloaded"))
    fb2 = P("b", resp="recovered")
    assert FallbackProvider(prim2, [fb2]).complete([]) == "recovered" and fb2.calls == 1
