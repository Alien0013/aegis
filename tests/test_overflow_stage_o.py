"""Stage O overflow recovery parity tests."""

from __future__ import annotations

import pytest


def _http_error(status: int, body: str = ""):
    from aegis.providers.chat_completions import ProviderHTTPError

    return ProviderHTTPError(status, body)


def test_http_413_payload_too_large_is_compressible_and_does_not_fallback():
    from aegis.providers.fallback import FallbackProvider, classify_provider_error, recovery_action

    class Provider:
        model = "fake-model"
        api_mode = None
        context_length = 200_000
        auth = None

        def __init__(self, name, *, exc=None, response=None):
            self.name = name
            self.exc = exc
            self.response = response
            self.calls = 0

        def describe(self):
            return self.name

        def complete(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.exc is not None:
                raise self.exc
            return self.response

    exc = _http_error(413, '{"error":{"message":"payload too large"}}')

    reason = classify_provider_error(exc)
    assert reason == "payload_too_large"
    assert recovery_action(reason) == "compress"

    primary = Provider("primary", exc=exc)
    fallback = Provider("fallback", response="should-not-run")

    with pytest.raises(type(exc)):
        FallbackProvider(primary, [fallback]).complete([])

    assert primary.calls == 1
    assert fallback.calls == 0


def test_unsupported_max_tokens_400_is_client_abort_not_compressible():
    from aegis.providers.fallback import classify_provider_error, recovery_action

    exc = _http_error(
        400,
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead.",
    )

    assert classify_provider_error(exc) == "client"
    assert recovery_action("client") == "abort"


def test_agent_retries_output_cap_overflow_with_reduced_max_tokens_without_compaction(
    monkeypatch,
    tmp_path,
):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.providers.chat_completions import ProviderHTTPError
    from aegis.session import Session
    from aegis.tools.registry import ToolRegistry
    from aegis.types import LLMResponse

    class Provider:
        name = "fake"
        model = "fake-model"
        api_mode = None
        context_length = 200_000
        auth = None

        def __init__(self):
            self.max_tokens_seen = []

        def describe(self):
            return "fake"

        def complete(self, messages, tools=None, **kwargs):
            self.max_tokens_seen.append(kwargs.get("max_tokens"))
            if len(self.max_tokens_seen) == 1:
                raise ProviderHTTPError(
                    400,
                    "This model's maximum number of tokens for output is 256. "
                    "Please reduce max_tokens.",
                )
            return LLMResponse(text="recovered")

    def fail_compact(*_args, **_kwargs):
        raise AssertionError("output-cap overflow should reduce max_tokens, not compact")

    monkeypatch.setenv("AEGIS_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("aegis.agent.loop._force_compact", fail_compact)

    cfg = Config.load()
    cfg.data["memory"]["enabled"] = False
    cfg.data["skills"]["auto_load"] = False
    cfg.data["tools"]["toolsets"] = []

    provider = Provider()
    session = Session.create("stage o output cap")
    agent = Agent(
        config=cfg,
        provider=provider,
        session=session,
        registry=ToolRegistry(),
        memory=None,
        cwd=tmp_path,
    )
    agent.stream = False
    agent._request_max_tokens = 400

    out = agent.run("answer briefly")

    assert out.content == "recovered"
    assert provider.max_tokens_seen == [400, 192]
    assert session.meta.get("compactions", []) == []


def test_chat_completions_payload_includes_max_tokens(monkeypatch):
    from aegis.providers.chat_completions import ChatCompletionsTransport
    from aegis.types import Message

    captured = {}

    class FakeAuth:
        def headers(self):
            return {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

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

    monkeypatch.setattr("aegis.providers.chat_completions.httpx.Client", FakeClient)

    ChatCompletionsTransport().complete(
        base_url="https://api.example.test/v1",
        auth=FakeAuth(),
        model="example-model",
        messages=[Message.user("hi")],
        tools=None,
        stream=False,
        max_tokens=123,
    )

    assert captured["json"]["max_tokens"] == 123
