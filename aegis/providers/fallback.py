"""Fallback provider chain: try the primary, fall back on transport errors.

Duck-types ``Provider`` (has ``complete``, ``context_length``, ``describe``,
``auth``, ``model``) so the agent loop uses it transparently.
"""

from __future__ import annotations

from ..types import LLMResponse, Message, ToolSchema
from .base import Provider


# Failure class -> the recovery action the retry layer / loop should take.
#   retry     : transient — back off and try the same request again
#   rotate    : try the next key/provider immediately (bad key, quota exhausted)
#   compress  : the request is too big — compact the session, don't failover
#   abort     : retrying unchanged won't help (content policy, bad request)
RECOVERY_ACTION = {
    "rate_limit": "retry",
    "auth": "rotate",
    "billing": "rotate",
    "context_overflow": "compress",
    "content_policy": "abort",
    "server": "retry",
    "transient": "retry",
    "client": "abort",
    "invalid_response": "retry",
    "thinking_signature": "strip_thinking",
}

_OVERFLOW_HINTS = ("context length", "maximum context", "context_length_exceeded", "too long",
                   "reduce the length", "too many tokens", "maximum number of tokens")
_POLICY_HINTS = ("content policy", "content_policy", "content_filter", "moderation", "safety",
                 "flagged", "responsibleai")
_QUOTA_HINTS = ("insufficient_quota", "quota exceeded", "exceeded your current quota", "billing")
# Anthropic rejects a request whose signed thinking/redacted_thinking blocks were mutated
# upstream (compaction, normalization, reload). Recovery: resend without thinking blocks.
_THINKING_SIG_HINTS = ("thinking or redacted_thinking", "thinking blocks", "cannot be modified",
                       "invalid signature", "signature is invalid")


def classify_provider_error(exc: Exception) -> str:
    """Map a provider failure to a precise class so each gets the right recovery action:
    rate_limit · auth · billing · context_overflow · content_policy · server · client ·
    transient (timeout/dropped stream) · invalid_response (unparseable). See ``RECOVERY_ACTION``."""
    from .chat_completions import ProviderHTTPError
    if isinstance(exc, ProviderHTTPError):
        s = exc.status
        body = (getattr(exc, "body", "") or "").lower()
        if s == 429:
            return "billing" if any(h in body for h in _QUOTA_HINTS) else "rate_limit"
        if s == 401:
            return "auth"
        if s in (402, 403):
            return "billing"
        if 400 <= s < 500:
            if any(h in body for h in _THINKING_SIG_HINTS):
                return "thinking_signature"    # resend without thinking blocks
            if any(h in body for h in _OVERFLOW_HINTS):
                return "context_overflow"      # compress, don't failover
            if any(h in body for h in _POLICY_HINTS):
                return "content_policy"        # don't retry unchanged
            return "client"
        if 500 <= s < 600:
            return "server"
        return "transient"
    try:
        import httpx
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return "transient"
    except Exception:  # noqa: BLE001
        pass
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "transient"
    return "invalid_response"


def recovery_action(reason: str) -> str:
    """The recovery action for a failure class (see ``RECOVERY_ACTION``)."""
    return RECOVERY_ACTION.get(reason, "retry")


class FallbackProvider:
    def __init__(self, primary: Provider, fallbacks: list[Provider]):
        self.primary = primary
        self.fallbacks = fallbacks
        self.active = primary
        self.last_trigger: tuple[str, str] | None = None   # (provider_name, reason)

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

    def complete(self, messages: list[Message], tools: list[ToolSchema] | None = None,
                 **kw) -> LLMResponse:
        chain = [self.primary, *self.fallbacks]
        last_err: Exception | None = None
        for prov in chain:
            try:
                resp = prov.complete(messages, tools=tools, **kw)
                self.active = prov
                return resp
            except Exception as e:  # noqa: BLE001
                reason = classify_provider_error(e)
                self.last_trigger = (getattr(prov, "name", "?"), reason)
                last_err = e
                # content_policy / context_overflow: another provider can't fix it (the request
                # itself is the problem) — stop failing over and let the caller handle it
                # (the loop compresses on context_overflow). Saves wasted calls down the chain.
                if recovery_action(reason) in ("abort", "compress"):
                    raise
                from .._log import info
                info(f"fallback: {getattr(prov, 'name', '?')} failed ({reason}); "
                     + ("trying next provider" if prov is not chain[-1] else "chain exhausted"))
                continue
        raise last_err or RuntimeError("all providers failed")


def build_with_fallbacks(config, *, model=None, name=None) -> Provider | FallbackProvider:
    """Build the primary provider plus any configured fallbacks."""
    from .registry import build_provider

    primary = build_provider(config, model=model, name=name)
    specs = config.get("fallback_providers", []) or []
    fallbacks: list[Provider] = []
    for s in specs:
        try:
            fallbacks.append(build_provider(config, model=s.get("model"), name=s.get("provider")))
        except Exception:  # noqa: BLE001
            continue
    return FallbackProvider(primary, fallbacks) if fallbacks else primary
