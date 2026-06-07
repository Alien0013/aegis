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


def test_responses_wire_and_parse():
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message, ToolCall

    t = ResponsesTransport()
    msgs = [
        Message.system("sys"),
        Message.user("hi"),
        Message.assistant("ok", [ToolCall("c1", "read_file", {"path": "a"})]),
        Message.tool("c1", "read_file", "contents"),
    ]
    wire = t._to_wire_input(msgs)
    assert wire[0] == {
        "type": "message",
        "role": "system",
        "content": [{"type": "input_text", "text": "sys"}],
    }
    assert wire[2]["content"][0]["type"] == "output_text"
    assert wire[3]["type"] == "function_call"
    assert wire[4] == {"type": "function_call_output", "call_id": "c1", "output": "contents"}

    parsed = t._parse_response({
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "done"}]},
            {"type": "function_call", "call_id": "c2", "name": "write_file", "arguments": "{\"path\":\"b\"}"},
        ],
        "usage": {"input_tokens": 3, "output_tokens": 4},
    })
    assert parsed.text == "done"
    assert parsed.tool_calls[0].name == "write_file"
    assert parsed.usage.input_tokens == 3


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
    assert all(registry.get_spec(p).oauth for p in ("anthropic", "openai", "openai-codex", "google"))


def test_openai_codex_builds_oauth_responses_provider():
    from aegis.config import Config
    from aegis.providers import build_provider
    from aegis.providers.base import ApiMode
    from aegis.providers.responses import ResponsesTransport

    cfg = Config.load()
    cfg.data["model"]["provider"] = "openai-codex"
    cfg.data["model"]["default"] = "gpt-5.5"
    provider = build_provider(cfg)

    assert provider.api_mode == ApiMode.RESPONSES
    assert isinstance(provider.transport, ResponsesTransport)
    assert provider.auth.describe() == "oauth (openai-codex: not logged in)"
    assert provider.base_url == "https://chatgpt.com/backend-api/codex"


def test_openai_codex_oauth_adds_account_header():
    import base64
    import json
    import time

    from aegis.providers.auth import AuthStore, OAuthAuth
    from aegis.providers.registry import OPENAI_CODEX_OAUTH

    def enc(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    token = enc({"alg": "none"}) + "." + enc({"chatgpt_account_id": "acct_123"}) + ".sig"
    store = AuthStore()
    store.save("openai-codex", {
        "access_token": token,
        "refresh_token": "refresh",
        "token_type": "Bearer",
        "expires_at": time.time() + 3600,
        "quarantined": False,
    })

    headers = OAuthAuth(OPENAI_CODEX_OAUTH, store).headers()
    assert headers["Authorization"] == f"Bearer {token}"
    assert headers["chatgpt-account-id"] == "acct_123"


def test_openai_api_key_wins_over_identity_only_oauth(monkeypatch):
    from aegis.config import Config
    from aegis.providers import build_provider, registry
    from aegis.providers.auth import AuthError, AuthStore, OAuthAuth

    cfg = Config.load()
    cfg.data["model"]["provider"] = "openai"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    AuthStore().save("openai", {
        "access_token": "opaque",
        "token_type": "Bearer",
        "scope": "openid profile email offline_access",
        "quarantined": False,
    })

    provider = build_provider(cfg)
    assert provider.auth.describe().startswith("api-key")

    oauth = OAuthAuth(registry.get_spec("openai").oauth, AuthStore())
    assert oauth.missing_required_scopes() == ["model.request"]
    assert not oauth.available()
    with pytest.raises(AuthError, match="missing required API scope"):
        oauth.headers()


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
