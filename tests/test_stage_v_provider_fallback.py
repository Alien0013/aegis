"""Stage V Hermes parity for provider failure classification and fallback."""

from __future__ import annotations

import pytest


def _err(status: int, body: str = ""):
    from aegis.providers.chat_completions import ProviderHTTPError

    return ProviderHTTPError(status, body)


class RecordingAuth:
    def __init__(self, *, rotates: bool = False):
        self.rotates = rotates
        self.reports: list[str] = []

    def report(self, kind: str) -> bool:
        self.reports.append(kind)
        return self.rotates


class Provider:
    context_length = 200_000
    api_mode = None

    def __init__(
        self,
        name: str,
        *,
        model: str = "m",
        base_url: str = "https://api.example.test/v1",
        exc: Exception | None = None,
        auth=None,
    ):
        self.name = name
        self.model = model
        self.base_url = base_url
        self.exc = exc
        self.auth = auth
        self.calls = 0

    def describe(self):
        return self.name

    def complete(self, messages, tools=None, **kwargs):
        from aegis.types import LLMResponse

        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return LLMResponse(text=self.name)


def test_structured_classifier_distinguishes_hermes_failure_reasons():
    from aegis.providers.fallback import (
        classify_provider_error,
        classify_provider_failure,
        recovery_action,
    )

    overloaded = classify_provider_failure(
        _err(429, "The upstream model is temporarily overloaded. Please try again later.")
    )
    assert overloaded.reason == "overloaded"
    assert classify_provider_error(
        _err(429, "The upstream model is temporarily overloaded. Please try again later.")
    ) == "overloaded"
    assert overloaded.rotate_credentials is False
    assert recovery_action(overloaded.reason) == "retry"

    server_overload = classify_provider_failure(_err(503, "upstream overloaded"))
    assert server_overload.reason == "overloaded"
    assert server_overload.legacy_reason == "server"

    usage_limit = classify_provider_failure(
        _err(402, "The usage limit has been reached. Retry after the limit resets.")
    )
    assert usage_limit.reason == "rate_limit"
    assert usage_limit.legacy_reason == "rate_limit"
    assert usage_limit.rotate_credentials is False

    assert classify_provider_failure(_err(401, "invalid api key")).reason == "auth"
    assert classify_provider_failure(_err(401, "api key revoked")).reason == "auth_permanent"
    assert classify_provider_failure(_err(403, "billing credits exhausted")).reason == "billing"
    assert classify_provider_failure(_err(403, "forbidden")).reason == "auth"

    model_missing = classify_provider_failure(_err(404, "The model 'missing-model' was not found."))
    assert model_missing.reason == "model_not_found"
    assert model_missing.fallback_eligible is True
    assert classify_provider_failure(_err(404, "no such route")).reason == "format"

    openrouter_policy = classify_provider_failure(
        _err(404, "No endpoints found matching your data policy."),
        provider=Provider("openrouter", base_url="https://openrouter.ai/api/v1"),
    )
    assert openrouter_policy.reason == "provider_policy_blocked"
    assert openrouter_policy.fallback_eligible is False

    content_policy = classify_provider_failure(
        _err(400, "Your request was rejected by our content policy.")
    )
    assert content_policy.reason == "content_policy_blocked"
    assert content_policy.legacy_reason == "content_policy"
    assert content_policy.fallback_eligible is True

    assert classify_provider_failure(
        _err(400, "maximum context length is 8192 tokens")
    ).reason == "context_overflow"
    assert classify_provider_failure(_err(413, "payload too large")).reason == "payload_too_large"
    assert classify_provider_failure(_err(400, "image is too large")).reason == "image_too_large"
    assert classify_provider_failure(_err(400, "thinking blocks cannot be modified")).reason == (
        "thinking_signature"
    )
    assert classify_provider_failure(ValueError("garbage")).reason == "invalid_response"


def test_overloaded_429_falls_back_without_rotating_credentials():
    from aegis.providers.fallback import FallbackProvider

    auth = RecordingAuth(rotates=True)
    primary = Provider(
        "primary",
        exc=_err(429, "Provider is temporarily overloaded. Try again later."),
        auth=auth,
    )
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"
    assert auth.reports == []
    assert provider.active is fallback
    assert provider.last_trigger == ("primary", "overloaded")
    assert provider.last_attempts[1]["error"]["classification"] == "overloaded"


def test_auth_failure_reports_and_retries_same_provider_before_fallback():
    from aegis.providers.chat_completions import ProviderHTTPError
    from aegis.providers.fallback import FallbackProvider
    from aegis.types import LLMResponse

    class RotatingProvider(Provider):
        def complete(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderHTTPError(401, "invalid api key")
            return LLMResponse(text="primary-recovered")

    auth = RecordingAuth(rotates=True)
    primary = RotatingProvider("primary", auth=auth)
    fallback = Provider("fallback")

    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "primary-recovered"
    assert auth.reports == ["auth"]
    assert primary.calls == 2
    assert fallback.calls == 0
    assert provider.active is primary
    assert [a["event"] for a in provider.last_attempts] == ["pre", "error", "pre", "post"]
    assert provider.last_attempts[2]["retry"] == "credential_rotation"


def test_plain_rate_limit_rotates_with_error_context_before_fallback():
    from aegis.providers.chat_completions import ProviderHTTPError
    from aegis.providers.fallback import FallbackProvider
    from aegis.types import LLMResponse

    class ContextAuth:
        def __init__(self):
            self.reports: list[tuple[str, dict | None]] = []

        def report(self, kind: str, error_context=None) -> bool:
            self.reports.append((kind, error_context))
            return True

    class RotatingProvider(Provider):
        def complete(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderHTTPError(429, "rate limit reached; retry after reset")
            return LLMResponse(text="primary-after-rotate")

    auth = ContextAuth()
    primary = RotatingProvider("primary", auth=auth)
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "primary-after-rotate"
    assert primary.calls == 2
    assert fallback.calls == 0
    assert auth.reports[0][0] == "rate_limit"
    assert auth.reports[0][1]["status"] == 429
    assert auth.reports[0][1]["reason"] == "rate_limit"


def test_content_policy_tries_configured_fallback_once():
    from aegis.providers.fallback import FallbackProvider

    auth = RecordingAuth(rotates=True)
    primary = Provider(
        "primary",
        exc=_err(400, "Your request was rejected by our content policy."),
        auth=auth,
    )
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"
    assert auth.reports == []
    assert primary.calls == 1
    assert fallback.calls == 1


def test_context_and_payload_failures_do_not_provider_fallback():
    from aegis.providers.fallback import FallbackProvider

    for exc in (
        _err(400, "maximum context length is 8192 tokens"),
        _err(413, "payload too large"),
        _err(400, "thinking blocks cannot be modified"),
        _err(404, "No endpoints found matching your data policy."),
    ):
        primary = Provider("primary", exc=exc)
        fallback = Provider("fallback")
        with pytest.raises(type(exc)):
            FallbackProvider(primary, [fallback]).complete([])
        assert fallback.calls == 0


def test_model_not_found_can_fallback_but_generic_404_cannot():
    from aegis.providers.fallback import FallbackProvider

    missing_model = Provider("primary", exc=_err(404, "model not found: missing-model"))
    fallback = Provider("fallback")
    assert FallbackProvider(missing_model, [fallback]).complete([]).text == "fallback"

    generic_404 = Provider("primary", exc=_err(404, "no such route"))
    unused = Provider("unused")
    with pytest.raises(type(generic_404.exc)):
        FallbackProvider(generic_404, [unused]).complete([])
    assert unused.calls == 0


def test_fallback_chain_skips_same_provider_model_base_url_routes():
    from aegis.providers.fallback import FallbackProvider

    primary = Provider("openai", model="gpt-test", base_url="https://api.openai.com/v1", exc=RuntimeError("down"))
    duplicate = Provider("openai", model="gpt-test", base_url="https://api.openai.com/v1/")
    distinct = Provider("openai", model="gpt-other", base_url="https://api.openai.com/v1")
    provider = FallbackProvider(primary, [duplicate, distinct])

    assert provider.complete([]).text == "openai"
    assert primary.calls == 1
    assert duplicate.calls == 0
    assert distinct.calls == 1
    assert provider.active is distinct


def test_fallback_active_provider_restores_primary_for_next_turn():
    from aegis.providers.fallback import FallbackProvider

    primary = Provider("primary", exc=RuntimeError("down"))
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"
    assert provider.active is fallback

    assert provider.restore_primary_runtime() is True
    assert provider.active is primary
    assert provider.restore_primary_runtime() is False


def test_agent_turn_prologue_restores_primary_after_fallback(tmp_path):
    from aegis.agent.agent import Agent
    from aegis.config import Config
    from aegis.providers.fallback import FallbackProvider
    from aegis.session import Session

    config = Config.load()
    config.data["memory"]["enabled"] = False
    primary = Provider("primary")
    fallback = Provider("fallback")
    wrapper = FallbackProvider(primary, [fallback])
    wrapper.active = fallback
    agent = Agent(config=config, provider=wrapper, session=Session.create(), cwd=tmp_path)

    agent._begin_turn_prologue()

    assert wrapper.active is primary


def test_real_provider_hands_failover_to_fallback_without_local_retry():
    from aegis.providers.auth import ApiKeyAuth
    from aegis.providers.base import ApiMode
    from aegis.providers.base import Provider as BoundProvider
    from aegis.providers.chat_completions import ProviderHTTPError
    from aegis.providers.fallback import FallbackProvider

    class FailingTransport:
        def __init__(self):
            self.calls = 0

        def complete(self, **kwargs):
            self.calls += 1
            raise ProviderHTTPError(503, "server error")

    transport = FailingTransport()
    primary = BoundProvider(
        name="primary",
        transport=transport,
        auth=ApiKeyAuth([], "none"),
        base_url="https://primary.test/v1",
        model="primary-model",
        context_length=64_000,
        api_mode=ApiMode.CHAT_COMPLETIONS,
    )
    fallback = Provider("fallback")

    assert FallbackProvider(primary, [fallback]).complete([]).text == "fallback"
    assert transport.calls == 1
    assert fallback.calls == 1


def test_build_with_fallbacks_honors_fallback_endpoint_and_key_env(monkeypatch):
    from aegis.config import Config
    from aegis.providers.fallback import FallbackProvider, build_with_fallbacks

    monkeypatch.setenv("LOCAL_FALLBACK_KEY", "local-key")
    config = Config.load()
    config.data["model"]["provider"] = "anthropic"
    config.data["model"]["default"] = "claude-sonnet-4-6"
    config.data["fallback_providers"] = [{
        "provider": "local-row",
        "model": "local-model",
        "base_url": "http://local.test/v1",
        "api_mode": "chat_completions",
        "key_env": "LOCAL_FALLBACK_KEY",
        "context_length": 64_000,
    }]

    provider = build_with_fallbacks(config)

    assert isinstance(provider, FallbackProvider)
    fallback = provider.fallbacks[0]
    assert fallback.name == "local-row"
    assert fallback.model == "local-model"
    assert fallback.base_url == "http://local.test/v1"
    assert fallback.auth.env_vars == ["LOCAL_FALLBACK_KEY"]
