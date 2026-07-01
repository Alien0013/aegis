from __future__ import annotations

import copy


class _FakeAuth:
    def __init__(self, headers: dict[str, str] | None = None):
        self._headers = headers or {}

    def headers(self) -> dict[str, str]:
        return dict(self._headers)


def test_provider_complete_forwards_request_overrides_only_when_supported():
    from aegis.providers.base import ApiMode, Provider
    from aegis.types import LLMResponse, Message

    class OverrideTransport:
        def complete(
            self,
            *,
            base_url,
            auth,
            model,
            messages,
            tools,
            stream,
            on_delta=None,
            max_tokens=8192,
            extra_headers=None,
            reasoning="off",
            tool_runner=None,
            approver=None,
            cwd=None,
            request_overrides=None,
        ):
            self.kwargs = {
                "base_url": base_url,
                "auth": auth,
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": stream,
                "on_delta": on_delta,
                "max_tokens": max_tokens,
                "extra_headers": extra_headers,
                "reasoning": reasoning,
                "tool_runner": tool_runner,
                "approver": approver,
                "cwd": cwd,
                "request_overrides": request_overrides,
            }
            return LLMResponse(text="ok")

    class PlainTransport:
        def complete(
            self,
            *,
            base_url,
            auth,
            model,
            messages,
            tools,
            stream,
            on_delta=None,
            max_tokens=8192,
            extra_headers=None,
            reasoning="off",
            tool_runner=None,
            approver=None,
            cwd=None,
        ):
            self.kwargs = {
                "base_url": base_url,
                "auth": auth,
                "model": model,
                "messages": messages,
                "tools": tools,
                "stream": stream,
                "on_delta": on_delta,
                "max_tokens": max_tokens,
                "extra_headers": extra_headers,
                "reasoning": reasoning,
                "tool_runner": tool_runner,
                "approver": approver,
                "cwd": cwd,
            }
            return LLMResponse(text="ok")

    def make_provider(transport):
        provider = Provider(
            name="test",
            transport=transport,
            auth=_FakeAuth(),
            base_url="https://example.test/v1",
            model="model",
            context_length=64_000,
            api_mode=ApiMode.RESPONSES,
        )
        provider.request_overrides = {"extra_body": {"temperature": 0.2}}
        return provider

    override_transport = OverrideTransport()
    make_provider(override_transport).complete([Message.user("hi")])
    assert override_transport.kwargs["request_overrides"] == {"extra_body": {"temperature": 0.2}}

    plain_transport = PlainTransport()
    make_provider(plain_transport).complete([Message.user("hi")])
    assert "request_overrides" not in plain_transport.kwargs


def test_responses_merges_request_overrides_without_leaking_control_keys(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    captured: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, headers, json):
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)

    ResponsesTransport().complete(
        base_url="https://api.openai.com/v1",
        auth=_FakeAuth({"Authorization": "Bearer token"}),
        model="gpt-5.5",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        extra_headers={"X-Provider": "base"},
        request_overrides={
            "extra_headers": {"X-Override": "yes"},
            "extra_body": {"temperature": 0.2, "metadata": {"lane": "parity"}},
        },
    )

    assert captured["headers"]["X-Provider"] == "base"
    assert captured["headers"]["X-Override"] == "yes"
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["json"]["temperature"] == 0.2
    assert captured["json"]["metadata"]["lane"] == "parity"
    assert "extra_headers" not in captured["json"]
    assert "extra_body" not in captured["json"]


def test_responses_strips_xai_service_tier_even_when_supplied_by_overrides(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    captured: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, headers, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)

    ResponsesTransport().complete(
        base_url="https://api.x.ai/v1",
        auth=_FakeAuth(),
        model="grok-4.3",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        service_tier="priority",
        request_overrides={"extra_body": {"service_tier": "priority", "temperature": 0.2}},
    )

    assert captured["json"]["temperature"] == 0.2
    assert "service_tier" not in captured["json"]


def test_responses_xai_cache_routing_preserves_caller_cache_key(monkeypatch):
    from aegis.providers.responses import ResponsesTransport
    from aegis.types import Message

    captured: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, headers, json):
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("aegis.providers.responses.httpx.Client", FakeClient)

    ResponsesTransport().complete(
        base_url="https://api.x.ai/v1",
        auth=_FakeAuth(),
        model="grok-4.3",
        messages=[Message.system("stable prefix"), Message.user("hi")],
        tools=[{"name": "lookup", "description": "Lookup", "parameters": {"type": "object"}}],
        stream=False,
        session_id="conv-xai-1",
        request_overrides={
            "extra_headers": {"x-test": "1"},
            "extra_body": {"prompt_cache_key": "caller-cache-key"},
        },
    )

    assert captured["headers"]["x-test"] == "1"
    assert captured["headers"]["x-grok-conv-id"] == "conv-xai-1"
    assert captured["json"]["prompt_cache_key"] == "caller-cache-key"


def test_anthropic_merges_request_overrides_preserving_beta_and_cache(monkeypatch):
    from aegis.providers.anthropic import AnthropicTransport
    from aegis.types import Message

    captured: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, headers, json):
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("aegis.providers.anthropic.httpx.Client", FakeClient)

    AnthropicTransport().complete(
        base_url="https://api.anthropic.com",
        auth=_FakeAuth({"x-api-key": "secret"}),
        model="claude-sonnet-4-6",
        messages=[Message.system("system prompt"), Message.user("hi")],
        tools=None,
        stream=False,
        extra_headers={"anthropic-beta": "oauth-2025-04-20"},
        request_overrides={
            "extra_headers": {"X-Override": "yes"},
            "extra_body": {"metadata": {"lane": "parity"}},
        },
    )

    assert captured["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    assert captured["headers"]["X-Override"] == "yes"
    assert captured["headers"]["x-api-key"] == "secret"
    assert captured["json"]["metadata"] == {"lane": "parity"}
    assert captured["json"]["system"][0]["text"].startswith("You are Claude Code")
    assert "cache_control" in captured["json"]["system"][-1]
    assert "cache_control" in captured["json"]["messages"][-1]["content"][-1]
    assert "extra_headers" not in captured["json"]
    assert "extra_body" not in captured["json"]


def test_anthropic_invalid_cache_ttl_falls_back_to_default(monkeypatch):
    from aegis.config import Config
    from aegis.providers import anthropic

    anthropic._CACHE_TTL = None
    monkeypatch.setattr(
        "aegis.config.Config.load",
        lambda: Config({"prompt_caching": {"cache_ttl": "24h"}}),
    )

    assert anthropic._cache_ttl() == "5m"
    assert anthropic._cache_marker() == {"type": "ephemeral"}


def test_build_provider_applies_model_default_headers_and_request_overrides():
    from aegis.config import DEFAULT_CONFIG, Config
    from aegis.providers import build_provider

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["model"].update(
        {
            "provider": "custom",
            "default": "custom-model",
            "base_url": "http://localhost:8080/v1",
            "api_mode": "responses",
            "context_length": 64_000,
            "default_headers": {"X-Model-Default": "yes"},
            "request_overrides": {
                "extra_headers": {"X-Override": "yes"},
                "extra_body": {"temperature": 0.2},
            },
        }
    )

    provider = build_provider(cfg)

    assert provider.extra_headers["X-Model-Default"] == "yes"
    assert provider.request_overrides == {
        "extra_headers": {"X-Override": "yes"},
        "extra_body": {"temperature": 0.2},
    }


def test_custom_provider_extra_body_merges_under_model_request_overrides():
    from aegis.config import DEFAULT_CONFIG, Config
    from aegis.providers import build_provider

    cfg = Config(copy.deepcopy(DEFAULT_CONFIG))
    cfg.data["model"].update({"provider": "gemma", "default": "google/gemma-4-31b-it"})
    cfg.data["custom_providers"] = [
        {
            "name": "gemma",
            "base_url": "https://example.test/v1",
            "api_mode": "chat_completions",
            "context_length": 64_000,
            "extra_body": {"enable_thinking": True, "reasoning_effort": "high"},
            "extra_headers": {"X-Custom": "yes"},
        }
    ]
    cfg.data["model"]["request_overrides"] = {
        "extra_body": {"reasoning_effort": "low", "caller_only": True}
    }

    provider = build_provider(cfg)

    assert provider.extra_headers["X-Custom"] == "yes"
    assert provider.request_overrides["extra_body"] == {
        "enable_thinking": True,
        "reasoning_effort": "low",
        "caller_only": True,
    }
