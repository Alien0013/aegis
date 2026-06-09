"""Fallback provider chain: try the primary, fall back on transport errors.

Duck-types ``Provider`` (has ``complete``, ``context_length``, ``describe``,
``auth``, ``model``) so the agent loop uses it transparently.
"""

from __future__ import annotations

from ..types import LLMResponse, Message, ToolSchema
from .base import Provider


def classify_provider_error(exc: Exception) -> str:
    """Map a provider failure to a fallback trigger class (à la Hermes credential pool):
    rate_limit (429) · auth (401) · billing (402/403) · server (5xx) · client (4xx) ·
    transient (timeout/dropped stream) · invalid_response (unparseable)."""
    from .chat_completions import ProviderHTTPError
    if isinstance(exc, ProviderHTTPError):
        s = exc.status
        if s == 429:
            return "rate_limit"
        if s == 401:
            return "auth"
        if s in (402, 403):
            return "billing"
        if 500 <= s < 600:
            return "server"
        if 400 <= s < 500:
            return "client"
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
