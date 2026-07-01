"""Stage Z provider fallback rate-control parity."""

from __future__ import annotations

from datetime import datetime


def _http_error(status: int, body: str, headers: dict[str, str] | None = None):
    from aegis.providers.chat_completions import ProviderHTTPError

    err = ProviderHTTPError(status, body)
    err.headers = dict(headers or {})
    return err


class RecordingAuth:
    def __init__(self, *, rotates: bool = False):
        self.rotates = rotates
        self.reports: list[tuple[str, dict | None]] = []

    def headers(self) -> dict[str, str]:
        return {}

    def report(self, kind: str, error_context=None) -> bool:
        self.reports.append((kind, error_context))
        return self.rotates


class Provider:
    context_length = 64_000
    api_mode = None

    def __init__(self, name: str, *, exc: Exception | None = None, auth=None):
        self.name = name
        self.model = "stage-z-model"
        self.base_url = f"https://{name}.example.test/v1"
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


def test_retry_after_hands_off_to_auth_context_and_primary_cooldown():
    from aegis.providers.fallback import FallbackProvider

    auth = RecordingAuth(rotates=False)
    primary = Provider(
        "primary",
        exc=_http_error(429, "rate limit reached", {"Retry-After": "123"}),
        auth=auth,
    )
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"

    assert auth.reports
    kind, context = auth.reports[0]
    assert kind == "rate_limit"
    assert context["retry_after"] == 123.0
    assert datetime.fromisoformat(context["reset_at"]).timestamp() > 0

    cooldowns = provider.rate_control_status()
    assert len(cooldowns) == 1
    assert cooldowns[0]["provider"] == "primary"
    assert cooldowns[0]["reason"] == "rate_limit"
    assert cooldowns[0]["retry_after"] == 123.0
    assert 100.0 < cooldowns[0]["remaining_seconds"] <= 123.0

    assert provider.restore_primary_runtime() is False
    assert provider.active is fallback
    assert provider.last_attempts[1]["error"]["rate_control"]["retry_after"] == 123.0


def test_rate_limit_cooldown_keeps_followup_calls_on_active_fallback():
    from aegis.providers.fallback import FallbackProvider

    primary = Provider(
        "primary",
        exc=_http_error(429, "please retry after 60 seconds"),
        auth=RecordingAuth(rotates=False),
    )
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"
    assert provider.complete([]).text == "fallback"

    assert primary.calls == 1
    assert fallback.calls == 2
    assert provider.active is fallback


def test_expired_rate_limit_cooldown_allows_primary_restoration():
    from aegis.providers.fallback import FallbackProvider

    primary = Provider(
        "primary",
        exc=_http_error(429, "rate limit reached", {"Retry-After": "0"}),
        auth=RecordingAuth(rotates=False),
    )
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"
    assert provider.active is fallback

    assert provider.restore_primary_runtime() is True
    assert provider.active is primary
    assert provider.rate_control_status() == []


def test_bound_provider_preserves_retry_after_before_fallback():
    from aegis.providers.base import ApiMode, Provider as BoundProvider
    from aegis.providers.fallback import FallbackProvider

    class FailingTransport:
        def __init__(self):
            self.calls = 0

        def complete(self, **kwargs):
            self.calls += 1
            raise _http_error(429, "rate limit reached", {"Retry-After": "45"})

    auth = RecordingAuth(rotates=False)
    transport = FailingTransport()
    primary = BoundProvider(
        name="primary",
        transport=transport,
        auth=auth,
        base_url="https://primary.example.test/v1",
        model="stage-z-model",
        context_length=64_000,
        api_mode=ApiMode.CHAT_COMPLETIONS,
    )
    fallback = Provider("fallback")

    assert FallbackProvider(primary, [fallback]).complete([]).text == "fallback"

    assert transport.calls == 1
    assert any(
        kind == "rate_limit"
        and context
        and context["retry_after"] == 45.0
        and "reset_at" in context
        for kind, context in auth.reports
    )


def test_exhausted_rate_limit_bucket_trips_provider_account_breaker():
    from aegis.providers.fallback import FallbackProvider, classify_provider_failure

    headers = {
        "x-ratelimit-limit-requests-1h": "800",
        "x-ratelimit-remaining-requests-1h": "0",
        "x-ratelimit-reset-requests-1h": "3100",
        "x-ratelimit-limit-requests": "200",
        "x-ratelimit-remaining-requests": "198",
        "x-ratelimit-reset-requests": "40",
    }
    exc = _http_error(429, "rate limit reached", headers)
    failure = classify_provider_failure(exc)

    assert failure.reason == "rate_limit"
    assert failure.provider_account_limited is True
    assert failure.rotate_credentials is True

    auth = RecordingAuth(rotates=False)
    primary = Provider("primary", exc=exc, auth=auth)
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"

    assert auth.reports
    kind, context = auth.reports[0]
    assert kind == "rate_limit"
    assert context["provider_account_limited"] is True
    assert context["rate_limit_buckets"]["requests-1h"]["remaining"] == 0

    cooldowns = provider.rate_control_status()
    assert len(cooldowns) == 1
    assert cooldowns[0]["provider"] == "primary"
    assert provider.restore_primary_runtime() is False
    assert provider.last_attempts[1]["error"]["provider_account_limited"] is True
    assert "rate_control" in provider.last_attempts[1]["error"]


def test_healthy_rate_limit_headers_stay_fallback_only_without_account_breaker():
    from aegis.providers.fallback import FallbackProvider, classify_provider_failure

    headers = {
        "x-ratelimit-limit-requests": "200",
        "x-ratelimit-remaining-requests": "198",
        "x-ratelimit-reset-requests": "40",
        "x-ratelimit-limit-requests-1h": "800",
        "x-ratelimit-remaining-requests-1h": "750",
        "x-ratelimit-reset-requests-1h": "3100",
        "x-ratelimit-limit-tokens": "800000",
        "x-ratelimit-remaining-tokens": "790000",
        "x-ratelimit-reset-tokens": "40",
        "x-ratelimit-limit-tokens-1h": "8000000",
        "x-ratelimit-remaining-tokens-1h": "7800000",
        "x-ratelimit-reset-tokens-1h": "3100",
    }
    exc = _http_error(429, "rate limit reached", headers)
    failure = classify_provider_failure(exc)

    assert failure.reason == "rate_limit"
    assert failure.provider_account_limited is False
    assert failure.rotate_credentials is False

    auth = RecordingAuth(rotates=True)
    primary = Provider("primary", exc=exc, auth=auth)
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"

    assert auth.reports == []
    assert provider.rate_control_status() == []
    assert provider.restore_primary_runtime() is True
    assert provider.active is primary
    assert provider.last_attempts[1]["error"]["provider_account_limited"] is False
    assert "rate_control" not in provider.last_attempts[1]["error"]


def test_short_reset_exhausted_bucket_does_not_trip_provider_account_breaker():
    from aegis.providers.fallback import FallbackProvider, classify_provider_failure

    exc = _http_error(
        429,
        "rate limit reached",
        {
            "x-ratelimit-limit-requests": "200",
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": "30",
        },
    )
    failure = classify_provider_failure(exc)

    assert failure.reason == "rate_limit"
    assert failure.provider_account_limited is False
    assert failure.rotate_credentials is False

    auth = RecordingAuth(rotates=True)
    primary = Provider("primary", exc=exc, auth=auth)
    fallback = Provider("fallback")
    provider = FallbackProvider(primary, [fallback])

    assert provider.complete([]).text == "fallback"
    assert auth.reports == []
    assert provider.rate_control_status() == []
