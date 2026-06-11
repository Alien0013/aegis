"""Auxiliary model routing for internal AEGIS tasks.

The main provider should stay focused on the user-facing agent loop. Internal
tasks such as compaction, session summaries, and trajectory compression can use a
small/cheap auxiliary model when configured, with predictable fallback to the
main provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import Message


@dataclass
class AuxRoute:
    purpose: str
    provider: Any
    source: str


@dataclass
class AuxRouter:
    config: Any
    fallback_provider: Any = None
    _cache: dict[str, AuxRoute] = field(default_factory=dict)

    def provider_for(self, purpose: str = "default") -> Any:
        """Return the provider for an internal purpose, cached per purpose."""

        route = self.route(purpose)
        return route.provider

    def route(self, purpose: str = "default") -> AuxRoute:
        purpose = purpose or "default"
        if purpose in self._cache:
            return self._cache[purpose]
        provider = None
        source = "auxiliary"
        try:
            from .providers.registry import build_aux_provider

            try:
                provider = build_aux_provider(
                    self.config,
                    purpose=purpose,
                    fallback_provider=self.fallback_provider,
                )
            except TypeError:
                provider = build_aux_provider(self.config)
        except Exception:  # noqa: BLE001
            provider = self.fallback_provider
            source = "fallback"
        if provider is None:
            provider = self.fallback_provider
            source = "fallback"
        if provider is None:
            raise RuntimeError("no auxiliary or fallback provider available")
        route = AuxRoute(purpose=purpose, provider=provider, source=source)
        self._cache[purpose] = route
        return route

    def summarize_text(
        self,
        text: str,
        *,
        purpose: str = "summary",
        instruction: str = "Summarize this text concisely and factually.",
        max_chars: int = 12_000,
    ) -> str:
        if not text.strip():
            return ""
        provider = self.provider_for(purpose)
        resp = provider.complete(
            [
                Message.system(instruction),
                Message.user(text[:max_chars]),
            ],
            tools=None,
            stream=False,
        )
        return (resp.text or "").strip()


def router_for(agent: Any, *, fallback_provider: Any = None) -> AuxRouter:
    """Return an agent-scoped AuxRouter."""

    live_fallback = fallback_provider or getattr(agent, "provider", None)
    router = getattr(agent, "_aux_router", None)
    if router is None:
        router = AuxRouter(
            getattr(agent, "config", None),
            fallback_provider=live_fallback,
        )
        try:
            agent._aux_router = router
        except Exception:  # noqa: BLE001
            pass
    elif live_fallback is not None and router.fallback_provider is not live_fallback:
        router.fallback_provider = live_fallback
        router._cache = {k: v for k, v in router._cache.items() if v.source != "fallback"}
    return router
