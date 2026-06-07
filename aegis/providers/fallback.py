"""Fallback provider chain: try the primary, fall back on transport errors.

Duck-types ``Provider`` (has ``complete``, ``context_length``, ``describe``,
``auth``, ``model``) so the agent loop uses it transparently.
"""

from __future__ import annotations

from ..types import LLMResponse, Message, ToolSchema
from .base import Provider


class FallbackProvider:
    def __init__(self, primary: Provider, fallbacks: list[Provider]):
        self.primary = primary
        self.fallbacks = fallbacks
        self.active = primary

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
                last_err = e
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
