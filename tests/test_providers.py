"""Provider abstraction: wire conversion, auth, pools, fallback, reasoning, registry."""

from __future__ import annotations

import pytest


def test_chat_completions_wire_messages():
    from aegis.providers.chat_completions import ChatCompletionsTransport
    from aegis.types import Message, ToolCall

    t = ChatCompletionsTransport()
    msgs = [
        Message.system("sys"),
        Message.user("hi"),
        Message.assistant("ok", [ToolCall("c1", "read_file", {"path": "a"})]),
        Message.tool("c1", "read_file", "contents"),
    ]
    wire = t._to_wire_messages(msgs)
    assert wire[0] == {"role": "system", "content": "sys"}
    assert wire[2]["tool_calls"][0]["id"] == "c1"
    assert wire[3] == {"role": "tool", "tool_call_id": "c1", "content": "contents"}


def test_chat_completions_tools_wire():
    from aegis.providers.chat_completions import ChatCompletionsTransport
    out = ChatCompletionsTransport()._to_wire_tools([{"name": "x", "description": "d", "parameters": {}}])
    assert out[0]["type"] == "function" and out[0]["function"]["name"] == "x"


def test_anthropic_coalesces_tool_results():
    from aegis.providers.anthropic import AnthropicTransport
    from aegis.types import Message, ToolCall

    msgs = [
        Message.system("s"),
        Message.user("u1"),
        Message.assistant("", [ToolCall("c1", "t", {}), ToolCall("c2", "t", {})]),
        Message.tool("c1", "t", "r1"),
        Message.tool("c2", "t", "r2"),
    ]
    system, wire = AnthropicTransport()._to_wire(msgs)
    assert system == "s"
    # both tool_results land in one user turn after the assistant
    assert wire[1]["role"] == "assistant"
    results = [b for b in wire[2]["content"] if b["type"] == "tool_result"]
    assert {r["tool_use_id"] for r in results} == {"c1", "c2"}


def test_api_key_auth_schemes(monkeypatch):
    from aegis.providers.auth import ApiKeyAuth
    monkeypatch.setenv("X_KEY", "secret")
    assert ApiKeyAuth(["X_KEY"], "bearer").headers()["Authorization"] == "Bearer secret"
    assert ApiKeyAuth(["X_KEY"], "anthropic").headers()["x-api-key"] == "secret"
    assert ApiKeyAuth([], "none").headers() == {}


def test_api_key_missing_raises():
    from aegis.providers.auth import ApiKeyAuth, AuthError
    with pytest.raises(AuthError):
        ApiKeyAuth(["NOPE_KEY"], "bearer").headers()


def test_credential_pool_rotates(monkeypatch):
    from aegis.providers.auth import ApiKeyAuth
    monkeypatch.setenv("P", "a,b,c")
    auth = ApiKeyAuth(["P"])
    seen = [auth.headers()["Authorization"]]
    for _ in range(3):
        auth.rotate()
        seen.append(auth.headers()["Authorization"])
    assert seen == ["Bearer a", "Bearer b", "Bearer c", "Bearer a"]


def test_registry_builds_and_enforces_64k():
    from aegis.config import Config
    from aegis.providers import build_provider
    cfg = Config.load()
    cfg.data["model"]["provider"] = "anthropic"
    p = build_provider(cfg)
    assert p.context_length >= 64_000
    cfg.data["model"]["context_length"] = 1000
    with pytest.raises(ValueError):
        build_provider(cfg)


def test_provider_count_and_oauth():
    from aegis.providers import list_providers
    from aegis.providers import registry
    assert len(list_providers()) >= 20
    assert all(registry.get_spec(p).oauth for p in ("anthropic", "openai", "google"))


def test_fallback_provider_retries():
    from aegis.providers.fallback import FallbackProvider
    from aegis.types import LLMResponse

    class Down:
        context_length = 64_000; name = "d"; model = "m"; api_mode = None; auth = None
        def describe(self): return "d"
        def complete(self, *a, **k): raise RuntimeError("boom")

    class Up(Down):
        def complete(self, *a, **k): return LLMResponse(text="ok")

    assert FallbackProvider(Down(), [Up()]).complete([]).text == "ok"


def test_reasoning_threads_to_provider():
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.session import Session
    from conftest import FakeProvider
    cfg = Config.load()
    cfg.data["agent"]["reasoning_effort"] = "high"
    fp = FakeProvider()
    agent = Agent(config=cfg, provider=fp, session=Session.create())
    agent.run("hi")
    assert fp.last_reasoning == "high"
