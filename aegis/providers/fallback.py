"""Fallback provider chain: try the primary, fall back on transport errors.

Duck-types ``Provider`` (has ``complete``, ``context_length``, ``describe``,
``auth``, ``model``) so the agent loop uses it transparently.
"""

from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from ..types import LLMResponse, Message, ToolSchema
from .base import ApiMode, Provider


# Failure class -> the recovery action the retry layer / loop should take.
#   retry     : transient — back off and try the same request again
#   rotate    : try the next key/provider immediately (bad key, quota exhausted)
#   compress  : the request is too big — compact the session, don't failover
#   abort     : retrying unchanged won't help (content policy, bad request)
RECOVERY_ACTION = {
    "rate_limit": "retry",
    "auth": "rotate",
    "auth_permanent": "rotate",
    "billing": "rotate",
    "overloaded": "retry",
    "context_overflow": "compress",
    "payload_too_large": "compress",
    "image_too_large": "compress",
    "content_policy": "abort",
    "content_policy_blocked": "abort",
    "provider_policy_blocked": "abort",
    "server": "retry",
    "server_error": "retry",
    "transient": "retry",
    "timeout": "retry",
    "client": "abort",
    "format": "abort",
    "model_not_found": "abort",
    "invalid_response": "retry",
    "thinking_signature": "strip_thinking",
}

_OVERFLOW_HINTS = ("context length", "maximum context", "context_length_exceeded", "too long",
                   "reduce the length", "too many tokens", "maximum number of tokens")
_PAYLOAD_TOO_LARGE_HINTS = ("request entity too large", "payload too large", "error code: 413",
                            "request too large", "body too large", "maximum request size")
_IMAGE_TOO_LARGE_HINTS = ("image_too_large", "image too large", "image is too large",
                          "maximum image", "image exceeds", "image size")
_REQUEST_VALIDATION_HINTS = ("unknown parameter", "unsupported parameter",
                             "unrecognized request argument", "unknown_parameter",
                             "unsupported_parameter", "invalid request", "invalid_request_error",
                             "response_format", "json schema")
_POLICY_HINTS = ("content policy", "content_policy", "content_filter", "moderation", "safety",
                 "flagged", "responsibleai")
_PROVIDER_POLICY_HINTS = ("data policy", "no endpoints found", "no endpoint found",
                          "no endpoints available", "provider policy", "provider routing")
_QUOTA_HINTS = ("insufficient_quota", "quota exceeded", "exceeded your current quota", "billing",
                "payment required", "credits", "credit balance", "prepaid", "balance")
_USAGE_LIMIT_HINTS = ("usage limit", "rate limit", "too many requests", "try again",
                      "retry after", "retry-at", "reset", "resets")
_OVERLOAD_HINTS = ("temporarily overloaded", "overloaded", "over capacity", "capacity",
                   "server is busy", "service is busy", "try again later")
_MODEL_NOT_FOUND_HINTS = ("model_not_found", "model not found", "no such model",
                          "unknown model", "model does not exist", "model doesn't exist",
                          "model was not found", "was not found", "not a valid model",
                          "requested model")
_AUTH_PERMANENT_HINTS = ("account disabled", "account deactivated", "account suspended",
                         "key revoked", "api key revoked", "project disabled",
                         "project archived", "organization disabled")
_LONG_CONTEXT_TIER_HINTS = ("extra usage", "long context")
# Anthropic rejects a request whose signed thinking/redacted_thinking blocks were mutated
# upstream (compaction, normalization, reload). Recovery: resend without thinking blocks.
_THINKING_SIG_HINTS = ("thinking or redacted_thinking", "thinking blocks", "cannot be modified",
                       "invalid signature", "signature is invalid")
_RATE_CONTROL_REASONS = {"billing", "rate_limit"}
_DEFAULT_PROVIDER_COOLDOWN_SECONDS = 60.0
_MAX_RETRY_AFTER_SECONDS = 600.0
_RETRY_AFTER_HEADERS = ("retry-after", "Retry-After")
_RESET_HEADERS = (
    "x-ratelimit-reset",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
    "x-rate-limit-reset",
)
_RATE_LIMIT_BUCKET_TAGS = ("requests", "requests-1h", "tokens", "tokens-1h")
_MIN_RESET_FOR_PROVIDER_BREAKER_SECONDS = 60.0


@dataclass(frozen=True)
class ProviderFailure:
    """Structured provider-failure classification used by fallback orchestration."""

    reason: str
    recovery: str
    legacy_reason: str
    fallback_eligible: bool
    rotate_credentials: bool
    retry_same_provider: bool = False
    status: int | None = None
    provider_account_limited: bool = False


_LEGACY_REASON = {
    "auth_permanent": "auth",
    "content_policy_blocked": "content_policy",
    "format": "client",
    "server_error": "server",
    "timeout": "transient",
}


def _legacy_reason(reason: str) -> str:
    return _LEGACY_REASON.get(reason, reason)


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(h in text for h in hints)


def _failure(
    reason: str,
    *,
    status: int | None = None,
    legacy_reason: str | None = None,
    fallback_eligible: bool | None = None,
    rotate_credentials: bool | None = None,
    retry_same_provider: bool | None = None,
    provider_account_limited: bool | None = None,
) -> ProviderFailure:
    recovery = recovery_action(reason)
    legacy = legacy_reason or _legacy_reason(reason)
    if rotate_credentials is None:
        rotate_credentials = reason in {"auth", "auth_permanent", "billing"}
    if retry_same_provider is None:
        retry_same_provider = bool(rotate_credentials)
    if fallback_eligible is None:
        fallback_eligible = reason in {
            "auth",
            "auth_permanent",
            "billing",
            "rate_limit",
            "overloaded",
            "server_error",
            "timeout",
            "transient",
            "model_not_found",
            "content_policy_blocked",
            "invalid_response",
        }
    if provider_account_limited is None:
        provider_account_limited = reason in {"billing", "rate_limit"}
    return ProviderFailure(
        reason=reason,
        recovery=recovery,
        legacy_reason=legacy,
        fallback_eligible=fallback_eligible,
        rotate_credentials=bool(rotate_credentials),
        retry_same_provider=bool(retry_same_provider),
        status=status,
        provider_account_limited=bool(provider_account_limited),
    )


def is_long_context_tier_error(exc: Exception, *, model: str | None = None) -> bool:
    """Anthropic's gated Sonnet 1M tier reports as a 429, not a 400 overflow."""
    from .chat_completions import ProviderHTTPError

    if not isinstance(exc, ProviderHTTPError) or exc.status != 429:
        return False
    body = (getattr(exc, "body", "") or "").lower()
    if not all(h in body for h in _LONG_CONTEXT_TIER_HINTS):
        return False
    if model:
        return "sonnet" in str(model).lower()
    return True


def _maybe_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _maybe_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _rate_limit_bucket_state(headers: Any) -> dict[str, dict[str, int | float | None]]:
    try:
        items = dict(headers).items()
    except Exception:  # noqa: BLE001
        return {}
    lowered = {str(k).lower(): v for k, v in items}
    if not any(k.startswith("x-ratelimit-") for k in lowered):
        return {}
    buckets: dict[str, dict[str, int | float | None]] = {}
    for tag in _RATE_LIMIT_BUCKET_TAGS:
        remaining = _maybe_int(lowered.get(f"x-ratelimit-remaining-{tag}"))
        reset = _maybe_float(lowered.get(f"x-ratelimit-reset-{tag}"))
        limit = _maybe_int(lowered.get(f"x-ratelimit-limit-{tag}"))
        if remaining is not None or reset is not None or limit is not None:
            buckets[tag] = {"remaining": remaining, "reset": reset, "limit": limit}
    return buckets


def _provider_account_limited_from_headers(headers: Any) -> bool | None:
    """Return True/False when headers prove account quota state; None if absent."""
    buckets = _rate_limit_bucket_state(headers)
    if not buckets:
        return None
    for bucket in buckets.values():
        remaining = bucket.get("remaining")
        reset = bucket.get("reset")
        if remaining is None or remaining > 0 or reset is None:
            continue
        if reset >= _MIN_RESET_FOR_PROVIDER_BREAKER_SECONDS:
            return True
    return False


def classify_provider_failure(
    exc: Exception,
    *,
    provider: Provider | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> ProviderFailure:
    """Classify provider failures with structured recovery hints."""
    from .chat_completions import ProviderHTTPError

    provider_name = str(getattr(provider, "name", "") or "").lower()
    active_model = str(model or getattr(provider, "model", "") or "").lower()
    active_base_url = str(base_url or getattr(provider, "base_url", "") or "").lower()

    if isinstance(exc, ProviderHTTPError):
        s = exc.status
        body = " ".join((str(getattr(exc, "body", "") or ""), str(exc))).lower()
        is_openrouter = (
            provider_name == "openrouter"
            or "openrouter.ai" in active_base_url
            or "openrouter" in active_model
            or "openrouter" in body
        )
        if _contains_any(body, _THINKING_SIG_HINTS):
            return _failure("thinking_signature", status=s, fallback_eligible=False,
                            rotate_credentials=False, retry_same_provider=False)
        if _contains_any(body, _IMAGE_TOO_LARGE_HINTS):
            return _failure("image_too_large", status=s, fallback_eligible=False,
                            rotate_credentials=False, retry_same_provider=False)
        if _contains_any(body, _PAYLOAD_TOO_LARGE_HINTS) or s == 413:
            return _failure("payload_too_large", status=s, fallback_eligible=False,
                            rotate_credentials=False, retry_same_provider=False)
        if is_openrouter and _contains_any(body, _PROVIDER_POLICY_HINTS):
            return _failure("provider_policy_blocked", status=s, fallback_eligible=False,
                            rotate_credentials=False, retry_same_provider=False)
        if s == 429:
            # Anthropic Sonnet's gated long-context tier reports as a 429, but
            # retrying or failing over does not fix the request; compacting does.
            if is_long_context_tier_error(exc, model=active_model):
                return _failure("context_overflow", status=s, fallback_eligible=False,
                                rotate_credentials=False, retry_same_provider=False)
            if _contains_any(body, _OVERLOAD_HINTS):
                return _failure("overloaded", status=s, rotate_credentials=False,
                                retry_same_provider=False)
            if _contains_any(body, _QUOTA_HINTS) and not _contains_any(body, _USAGE_LIMIT_HINTS):
                return _failure("billing", status=s)
            provider_account_limited = _provider_account_limited_from_headers(_exception_headers(exc))
            if provider_account_limited is False:
                return _failure("rate_limit", status=s, rotate_credentials=False,
                                retry_same_provider=False, provider_account_limited=False)
            return _failure("rate_limit", status=s, rotate_credentials=True,
                            retry_same_provider=True,
                            provider_account_limited=provider_account_limited)
        if s == 401:
            reason = "auth_permanent" if _contains_any(body, _AUTH_PERMANENT_HINTS) else "auth"
            return _failure(reason, status=s)
        if s == 402 and _contains_any(body, _USAGE_LIMIT_HINTS):
            return _failure("rate_limit", status=s, rotate_credentials=False,
                            retry_same_provider=False)
        if s == 402:
            return _failure("billing", status=s)
        if s == 403:
            if _contains_any(body, _QUOTA_HINTS):
                return _failure("billing", status=s)
            reason = "auth_permanent" if _contains_any(body, _AUTH_PERMANENT_HINTS) else "auth"
            return _failure(reason, status=s)
        if s == 404:
            if _contains_any(body, _PROVIDER_POLICY_HINTS):
                return _failure("provider_policy_blocked", status=s, fallback_eligible=False,
                                rotate_credentials=False, retry_same_provider=False)
            if _contains_any(body, _MODEL_NOT_FOUND_HINTS) and "route" not in body:
                return _failure("model_not_found", status=s, rotate_credentials=False,
                                retry_same_provider=False)
            return _failure("format", status=s, fallback_eligible=False,
                            rotate_credentials=False, retry_same_provider=False)
        if 400 <= s < 500:
            if _contains_any(body, _REQUEST_VALIDATION_HINTS):
                return _failure("format", status=s, fallback_eligible=False,
                                rotate_credentials=False, retry_same_provider=False)
            if _contains_any(body, _OVERFLOW_HINTS):
                return _failure("context_overflow", status=s, fallback_eligible=False,
                                rotate_credentials=False, retry_same_provider=False)
            if _contains_any(body, _POLICY_HINTS):
                return _failure("content_policy_blocked", status=s,
                                rotate_credentials=False, retry_same_provider=False)
            return _failure("format", status=s, fallback_eligible=False,
                            rotate_credentials=False, retry_same_provider=False)
        if 500 <= s < 600:
            if _contains_any(body, _OVERLOAD_HINTS):
                return _failure("overloaded", status=s, legacy_reason="server",
                                rotate_credentials=False, retry_same_provider=False)
            return _failure("server_error", status=s, rotate_credentials=False,
                            retry_same_provider=False)
        return _failure("transient", status=s, rotate_credentials=False,
                        retry_same_provider=False)
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return _failure("timeout", fallback_eligible=True, rotate_credentials=False,
                            retry_same_provider=False)
        if isinstance(exc, httpx.TransportError):
            return _failure("transient", fallback_eligible=True, rotate_credentials=False,
                            retry_same_provider=False)
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, TimeoutError):
        return _failure("timeout", fallback_eligible=True, rotate_credentials=False,
                        retry_same_provider=False)
    if isinstance(exc, ConnectionError):
        return _failure("transient", fallback_eligible=True, rotate_credentials=False,
                        retry_same_provider=False)
    body = str(exc).lower()
    if _contains_any(body, _REQUEST_VALIDATION_HINTS):
        return _failure("format", fallback_eligible=False, rotate_credentials=False,
                        retry_same_provider=False)
    if _contains_any(body, _IMAGE_TOO_LARGE_HINTS):
        return _failure("image_too_large", fallback_eligible=False, rotate_credentials=False,
                        retry_same_provider=False)
    if _contains_any(body, _PAYLOAD_TOO_LARGE_HINTS):
        return _failure("payload_too_large", fallback_eligible=False, rotate_credentials=False,
                        retry_same_provider=False)
    return _failure("invalid_response", fallback_eligible=True, rotate_credentials=False,
                    retry_same_provider=False)


def classify_provider_error(exc: Exception) -> str:
    """Map a provider failure to the public compatibility class.

    Legacy callers still see ``server``/``transient``/``client``/``content_policy``
    aliases, while newer structured-recovery code can use ``classify_provider_failure``.
    """
    return classify_provider_failure(exc).legacy_reason


def recovery_action(reason: str) -> str:
    """The recovery action for a failure class (see ``RECOVERY_ACTION``)."""
    return RECOVERY_ACTION.get(reason, "retry")


def available_output_tokens_from_error(exc: Exception) -> int | None:
    """Return provider-reported output-token room for max_tokens-too-large errors."""
    import re

    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body:
        parts.append(str(body))
    text = " ".join(parts).lower()
    looks_like_output_cap = (
        "max_tokens" in text
        and ("available_tokens" in text or "available tokens" in text)
    ) or (
        "max_tokens" in text
        and "maximum number of tokens for output" in text
    ) or (
        "maximum context length" in text
        and "requested" in text
        and "output tokens" in text
    ) or (
        "maximum context length" in text
        and "in the output" in text
    )
    if not looks_like_output_cap:
        return None
    for pattern in (
        r"available_tokens[:\s]+(\d+)",
        r"available\s+tokens[:\s]+(\d+)",
        r"maximum\s+number\s+of\s+tokens\s+for\s+output\s+is\s+(\d+)",
        r"=\s*(\d+)\s*$",
    ):
        match = re.search(pattern, text)
        if match:
            value = int(match.group(1))
            if value >= 1:
                return value
    ctx = re.search(r"maximum context length is (\d+)", text)
    parts_match = re.search(
        r"\((\d+)\s+of text input,\s*(\d+)\s+of tool input,\s*(\d+)\s+in the output\)",
        text,
    )
    if ctx and parts_match:
        available = int(ctx.group(1)) - int(parts_match.group(1)) - int(parts_match.group(2))
        if available >= 1:
            return available
    ctx_tokens = re.search(r"maximum context length is (\d+)\s*token", text)
    prompt_chars = re.search(r"prompt contains (\d+)\s*character", text)
    if ctx_tokens and prompt_chars:
        available = int(ctx_tokens.group(1)) - ((int(prompt_chars.group(1)) + 2) // 3)
        if available >= 1:
            return available
    return None


def _headers_get(headers: Any, name: str) -> Any:
    get = getattr(headers, "get", None)
    if callable(get):
        value = get(name)
        if value is not None:
            return value
        value = get(name.lower())
        if value is not None:
            return value
        return get(name.title())
    if isinstance(headers, dict):
        lowered = {str(k).lower(): v for k, v in headers.items()}
        return lowered.get(name.lower())
    return None


def _exception_headers(exc: Exception) -> Any:
    for owner in (exc, getattr(exc, "response", None)):
        headers = getattr(owner, "headers", None)
        if headers is not None:
            return headers
    return None


def _seconds_from_delta(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = parsed.timestamp() - time.time()
    if seconds < 0:
        return 0.0
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _seconds_until_reset(value: Any) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?|m|min|minutes?)", text, re.I)
        if match:
            amount = float(match.group(1))
            unit = match.group(2).lower()
            if unit.startswith("ms"):
                return min(amount / 1000.0, _MAX_RETRY_AFTER_SECONDS)
            if unit.startswith("m") and not unit.startswith("ms"):
                return min(amount * 60.0, _MAX_RETRY_AFTER_SECONDS)
            return min(amount, _MAX_RETRY_AFTER_SECONDS)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return _seconds_from_delta(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = parsed.timestamp() - time.time()
    else:
        if number > 10_000_000_000:
            number /= 1000.0
        if number > 10_000_000:
            seconds = number - time.time()
        else:
            seconds = number
    if seconds < 0:
        return 0.0
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _retry_after_seconds(exc: Exception) -> float | None:
    for attr in ("retry_after", "retry_after_seconds"):
        seconds = _seconds_until_reset(getattr(exc, attr, None))
        if seconds is not None:
            return seconds
    headers = _exception_headers(exc)
    for name in _RETRY_AFTER_HEADERS:
        seconds = _seconds_from_delta(_headers_get(headers, name))
        if seconds is not None:
            return seconds
    for name in _RESET_HEADERS:
        seconds = _seconds_until_reset(_headers_get(headers, name))
        if seconds is not None:
            return seconds
    body = " ".join(
        str(part)
        for part in (getattr(exc, "body", None), str(exc))
        if part
    ).lower()
    match = re.search(
        r"(?:retry|try again)\s+(?:after|in)\s+(\d+(?:\.\d+)?)\s*"
        r"(ms|milliseconds?|s|sec|seconds?|m|min|minutes?)?",
        body,
    )
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "seconds").lower()
    if unit.startswith("ms"):
        amount /= 1000.0
    elif unit.startswith("m") and not unit.startswith("ms"):
        amount *= 60.0
    return min(amount, _MAX_RETRY_AFTER_SECONDS)


def _reset_at_iso(seconds: float) -> str:
    return datetime.fromtimestamp(time.time() + max(0.0, seconds), timezone.utc).isoformat()


def provider_failure_error_context(exc: Exception, failure: ProviderFailure) -> dict:
    """Build structured failure context for credential pools and telemetry."""
    context = {
        "reason": failure.reason,
        "status": failure.status,
        "status_code": failure.status,
        "message": str(exc),
    }
    body = getattr(exc, "body", None)
    if body:
        context["body"] = str(body)
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None and failure.reason in _RATE_CONTROL_REASONS:
        context["retry_after"] = retry_after
        context["reset_at"] = _reset_at_iso(retry_after)
    if failure.reason in _RATE_CONTROL_REASONS:
        context["provider_account_limited"] = failure.provider_account_limited
        buckets = _rate_limit_bucket_state(_exception_headers(exc))
        if buckets:
            context["rate_limit_buckets"] = buckets
    reset_at = getattr(exc, "reset_at", None)
    if reset_at is not None:
        context["reset_at"] = reset_at
    return {k: v for k, v in context.items() if v not in (None, "")}


def reduce_long_context_tier(provider, exc: Exception, *, target_context: int = 200_000) -> dict | None:
    """Drop a gated long-context Sonnet route to the broadly available 200k tier.

    The reduction is deliberately runtime-only: subscription tier failures should not
    rewrite model metadata or the user's configured context length.
    """
    active = getattr(provider, "active", provider)
    model = str(getattr(active, "model", "") or getattr(provider, "model", "") or "")
    if not is_long_context_tier_error(exc, model=model):
        return None
    try:
        current = int(getattr(active, "context_length", getattr(provider, "context_length", 0)) or 0)
    except (TypeError, ValueError):
        current = 0
    if current <= target_context:
        return None
    try:
        active.context_length = target_context
    except Exception:  # noqa: BLE001
        return None
    return {
        "model": model,
        "old_context_length": current,
        "new_context_length": target_context,
        "persisted": False,
        "reason": "long_context_tier_429",
    }


def _failure_error_context(exc: Exception, failure: ProviderFailure) -> dict:
    return provider_failure_error_context(exc, failure)


def _report_provider_failure(provider: Provider, reason: str, exc: Exception | None = None,
                             failure: ProviderFailure | None = None) -> bool:
    """Notify a provider's auth strategy about a classified failure.

    API-key pools use this signal to rotate or bench credentials. The boolean
    return value lets the fallback chain retry the same provider once when the
    failure class is explicitly recoverable by credential rotation (auth/billing),
    instead of burning a configured fallback provider unnecessarily.
    """
    auth = getattr(provider, "auth", None)
    report = getattr(auth, "report", None)
    if not callable(report):
        return False
    error_context = _failure_error_context(exc, failure) if exc is not None and failure is not None else None
    try:
        return bool(report(reason, error_context=error_context))
    except TypeError:
        try:
            return bool(report(reason))
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False


def _route_signature(provider: Provider) -> tuple[str, str, str] | None:
    """Return a comparable provider route when the object exposes enough data."""
    name = str(getattr(provider, "name", "") or "").strip().lower()
    model = str(getattr(provider, "model", "") or "").strip().lower()
    base_url = getattr(provider, "base_url", None)
    if not name or not model or base_url is None:
        return None
    return (name, model, str(base_url or "").strip().rstrip("/").lower())


def _route_key(provider: Provider) -> tuple[str, str, str]:
    return _route_signature(provider) or (
        f"object:{id(provider)}",
        str(getattr(provider, "model", "") or "").strip().lower(),
        str(getattr(provider, "name", "") or "").strip().lower(),
    )


class FallbackProvider:
    def __init__(self, primary: Provider, fallbacks: list[Provider]):
        self.primary = primary
        self.fallbacks = fallbacks
        self.active = primary
        self.last_trigger: tuple[str, str] | None = None   # (provider_name, reason)
        self.last_attempts: list[dict] = []
        self._cooldowns: dict[tuple[str, str, str], dict] = {}

    # -- Provider-compatible surface ---------------------------------------
    @property
    def name(self) -> str:
        return self.active.name

    @property
    def model(self) -> str:
        return self.active.model

    @property
    def auth(self):
        return self.active.auth

    @property
    def context_length(self) -> int:
        return self.active.context_length

    @property
    def api_mode(self):
        return self.active.api_mode

    def describe(self) -> str:
        extra = f" (+{len(self.fallbacks)} fallback)" if self.fallbacks else ""
        return self.primary.describe() + extra

    def restore_primary_runtime(self) -> bool:
        """Restore the primary provider for a new turn after fallback failover."""
        if self.active is self.primary:
            return False
        if self._cooldown_active(self.primary):
            return False
        self.active = self.primary
        return True

    def _cooldown_active(self, provider: Provider) -> bool:
        key = _route_key(provider)
        row = self._cooldowns.get(key)
        if not row:
            return False
        if float(row.get("until", 0.0) or 0.0) <= time.monotonic():
            self._cooldowns.pop(key, None)
            return False
        return True

    def _arm_cooldown(self, provider: Provider, failure: ProviderFailure,
                      exc: Exception) -> dict | None:
        if failure.reason not in _RATE_CONTROL_REASONS:
            return None
        if not failure.provider_account_limited:
            return None
        seconds = _retry_after_seconds(exc)
        if seconds is None:
            seconds = _DEFAULT_PROVIDER_COOLDOWN_SECONDS
        now = time.monotonic()
        until = now + max(0.0, seconds)
        key = _route_key(provider)
        existing = self._cooldowns.get(key)
        if existing and float(existing.get("until", 0.0) or 0.0) > until:
            return self._cooldown_row(key, existing)
        row = {
            "until": until,
            "provider": getattr(provider, "name", ""),
            "model": getattr(provider, "model", ""),
            "base_url": str(getattr(provider, "base_url", "") or "").rstrip("/"),
            "reason": failure.reason,
            "legacy_reason": failure.legacy_reason,
            "retry_after": seconds,
            "reset_at": _reset_at_iso(seconds),
        }
        self._cooldowns[key] = row
        return self._cooldown_row(key, row)

    def _cooldown_row(self, key: tuple[str, str, str], row: dict) -> dict:
        remaining = max(0.0, float(row.get("until", 0.0) or 0.0) - time.monotonic())
        return {
            "route": key,
            "provider": row.get("provider", ""),
            "model": row.get("model", ""),
            "base_url": row.get("base_url", ""),
            "reason": row.get("reason", ""),
            "legacy_reason": row.get("legacy_reason", ""),
            "retry_after": row.get("retry_after"),
            "reset_at": row.get("reset_at"),
            "remaining_seconds": remaining,
        }

    def rate_control_status(self) -> list[dict]:
        """Return active provider cooldowns created by fallback routing."""
        rows = []
        for key, row in list(self._cooldowns.items()):
            if float(row.get("until", 0.0) or 0.0) <= time.monotonic():
                self._cooldowns.pop(key, None)
                continue
            rows.append(self._cooldown_row(key, row))
        return rows

    def _chain(self) -> list[Provider]:
        chain: list[Provider] = []
        route_signatures: set[tuple[str, str, str]] = set()
        for provider in [self.active, self.primary, *self.fallbacks]:
            if self._cooldown_active(provider):
                continue
            if any(provider is existing for existing in chain):
                continue
            signature = _route_signature(provider)
            if signature is not None and signature in route_signatures:
                continue
            chain.append(provider)
            if signature is not None:
                route_signatures.add(signature)
        return chain or [self.active]

    def complete(self, messages: list[Message], tools: list[ToolSchema] | None = None,
                 **kw) -> LLMResponse:
        on_attempt = kw.pop("on_provider_attempt", None)
        chain = self._chain()
        self.last_attempts = []
        last_err: Exception | None = None
        deterministic_fallback_ceiling: int | None = None
        for index, prov in enumerate(chain):
            rotated_retry = False
            while True:
                attempt = {
                    "index": index,
                    "provider": getattr(prov, "name", ""),
                    "model": getattr(prov, "model", ""),
                    "api_mode": str(getattr(getattr(prov, "api_mode", ""), "value", getattr(prov, "api_mode", "")) or ""),
                }
                if rotated_retry:
                    attempt["retry"] = "credential_rotation"
                self.last_attempts.append({**attempt, "event": "pre"})
                if callable(on_attempt):
                    try:
                        on_attempt({**attempt, "event": "pre"})
                    except Exception:  # noqa: BLE001
                        pass
                import time
                started = time.perf_counter()
                try:
                    call_kw = dict(kw)
                    metadata = dict(call_kw.get("metadata") or {})
                    metadata["_fallback_orchestrated"] = True
                    call_kw["metadata"] = metadata
                    resp = prov.complete(messages, tools=tools, **call_kw)
                    self.active = prov
                    post_attempt = {
                        **attempt,
                        "event": "post",
                        "status": "ok",
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                    }
                    self.last_attempts.append(post_attempt)
                    if callable(on_attempt):
                        try:
                            on_attempt(post_attempt)
                        except Exception:  # noqa: BLE001
                            pass
                    return resp
                except Exception as e:  # noqa: BLE001
                    failure = classify_provider_failure(e, provider=prov)
                    reason = failure.legacy_reason
                    action = failure.recovery
                    report_reason = "auth_permanent" if failure.reason == "auth_permanent" else reason
                    credential_rotated = (
                        _report_provider_failure(prov, report_reason, e, failure)
                        if failure.rotate_credentials
                        else False
                    )
                    cooldown = None
                    if not (credential_rotated and failure.retry_same_provider):
                        cooldown = self._arm_cooldown(prov, failure, e)
                    self.last_trigger = (getattr(prov, "name", "?"), reason)
                    last_err = e
                    error_info = {
                        "type": type(e).__name__,
                        "message": str(e),
                        "reason": reason,
                        "classification": failure.reason,
                        "recovery": action,
                        "credential_rotated": credential_rotated,
                        "provider_account_limited": failure.provider_account_limited,
                    }
                    if cooldown:
                        error_info["rate_control"] = cooldown
                    error_attempt = {
                        **attempt,
                        "event": "error",
                        "status": "error",
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "error": error_info,
                    }
                    self.last_attempts.append(error_attempt)
                    if callable(on_attempt):
                        try:
                            on_attempt(error_attempt)
                        except Exception:  # noqa: BLE001
                            pass
                    if (
                        deterministic_fallback_ceiling is not None
                        and index >= deterministic_fallback_ceiling
                    ):
                        raise
                    # Deterministic payload/context/format/provider-policy/thinking errors need
                    # loop-level recovery or terminal surfacing instead of burning fallbacks.
                    if not failure.fallback_eligible:
                        raise
                    from .._log import info
                    if (
                        credential_rotated
                        and failure.retry_same_provider
                        and not rotated_retry
                    ):
                        rotated_retry = True
                        info(f"fallback: {getattr(prov, 'name', '?')} failed ({reason}); "
                             "retrying with rotated credential")
                        continue
                    if failure.reason == "content_policy_blocked":
                        deterministic_fallback_ceiling = min(index + 1, len(chain) - 1)
                    info(f"fallback: {getattr(prov, 'name', '?')} failed ({reason}); "
                         + ("trying next provider" if prov is not chain[-1] else "chain exhausted"))
                    break
        raise last_err or RuntimeError("all providers failed")

    def cancel_response(self, response_id: str) -> dict | None:
        cancel = getattr(self.active, "cancel_response", None)
        if not callable(cancel) or not response_id:
            return None
        return cancel(response_id)


def _fallback_overlay_config(config, spec: dict):
    """Return a config copy carrying one fallback row's endpoint/auth hints."""
    provider_name = str(spec.get("provider") or "").strip()
    key_env = (
        spec.get("key_env")
        or spec.get("api_key_env")
        or spec.get("api_key_env_var")
        or spec.get("env_var")
    )
    hinted = any(spec.get(k) not in (None, "") for k in (
        "base_url", "api_mode", "context_length", "key_env",
        "api_key_env", "api_key_env_var", "env_var", "auth_scheme",
    ))
    if not hinted:
        return config
    data = copy.deepcopy(getattr(config, "data", {}) or {})
    model_cfg = data.setdefault("model", {})
    for key in ("base_url", "api_mode", "context_length"):
        value = spec.get(key)
        if value not in (None, ""):
            model_cfg[key] = value
    if key_env or spec.get("auth_scheme"):
        try:
            from .registry import _specs_for
            base_spec = _specs_for(config).get(provider_name)
        except Exception:  # noqa: BLE001
            base_spec = None
        api_mode = spec.get("api_mode")
        if not api_mode and base_spec is not None:
            api_mode = getattr(base_spec.api_mode, "value", str(base_spec.api_mode))
        base_url = spec.get("base_url") or getattr(base_spec, "base_url", "")
        context_length = spec.get("context_length") or getattr(base_spec, "context_length", 64_000)
        fallback_model = spec.get("model") or getattr(base_spec, "default_model", "") or "local-model"
        if base_url:
            custom = [row for row in data.get("custom_providers", []) or []
                      if row.get("name") != provider_name]
            custom.append({
                "name": provider_name,
                "base_url": base_url,
                "api_mode": api_mode or ApiMode.CHAT_COMPLETIONS.value,
                "default_model": fallback_model,
                "context_length": context_length,
                "env_var": key_env or "",
                "auth_scheme": spec.get("auth_scheme") or ("bearer" if key_env else "none"),
                "models": [fallback_model],
            })
            data["custom_providers"] = custom
    try:
        return type(config)(data)
    except Exception:  # noqa: BLE001
        return config


def build_with_fallbacks(config, *, model=None, name=None) -> Provider | FallbackProvider:
    """Build the primary provider plus any configured fallbacks."""
    from .registry import build_provider

    primary = build_provider(config, model=model, name=name)
    specs = config.get("fallback_providers", []) or []
    fallbacks: list[Provider] = []
    for s in specs:
        try:
            if not isinstance(s, dict):
                continue
            fallback_config = _fallback_overlay_config(config, s)
            fallbacks.append(build_provider(fallback_config, model=s.get("model"), name=s.get("provider")))
        except Exception:  # noqa: BLE001
            continue
    return FallbackProvider(primary, fallbacks) if fallbacks else primary
